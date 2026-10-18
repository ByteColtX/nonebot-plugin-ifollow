"""轮询调度：发现到期作者、抓取、投递与失败退避。

每个作者持有自己的 ``next_poll_at``，所以轮询周期与作者数量无关；抓取阶段并发、
发送阶段串行。所有运行时状态都在 :class:`Poller` 实例上，重启即清零，测试也不再
需要清理模块级全局变量。
"""

from __future__ import annotations

import asyncio
import time

from nonebot import logger

from .adapters import adapter_for
from .delivery import DeliveryContext, deliver_author, prune
from .errors import PermanentError, TransientError
from .failures import backoff_seconds, short_reason
from .models import AuthorSubscription, SubscriptionStore
from .platforms import Cursor, FeedKind, PlatformWork
from .settings import Settings
from .status import (
    AuthorPollState,
    RoundStats,
    format_wait,
    platform_name,
    render,
)

TICK_MIN_SECONDS = 15.0
TICK_MAX_SECONDS = 300.0

FETCH_CONCURRENCY = 4
"""同时抓取的作者数量上限。"""


class Poller:
    """常驻轮询器：持有全部运行时状态。"""

    def __init__(self, store: SubscriptionStore, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self.states: dict[str, AuthorPollState] = {}
        self.in_flight: set[str] = set()
        self.stats = RoundStats()
        self.last_round_at = 0.0
        self.context = DeliveryContext(store=store, settings=settings, stats=self.stats)
        self._send_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    def tick_seconds(self) -> float:
        """返回调度扫描间隔：周期的四分之一，夹在 15 秒到 5 分钟之间。"""

        return min(max(self.settings.interval / 4, TICK_MIN_SECONDS), TICK_MAX_SECONDS)

    def start(self) -> None:
        """启动常驻协程，重复调用无副作用。"""

        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """停止常驻协程并等待退出。"""

        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def state(self, author_key: str) -> AuthorPollState:
        """取（或创建）一个作者的运行时状态。"""

        return self.states.setdefault(author_key, AuthorPollState())

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_round()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning("爱播轮询异常，下一轮继续")
            await asyncio.sleep(self.tick_seconds())

    async def run_round(self) -> None:
        """执行一轮：抓取所有到期作者，并输出一条摘要。"""

        started = time.monotonic()
        authors = await self.store.snapshot()
        self._prune(authors)
        now = time.monotonic()
        due = [
            author
            for author in authors.values()
            if not self.state(author.key).disabled
            and self.state(author.key).next_poll_at <= now
        ]
        if not due:
            return
        self.stats = RoundStats(authors=len(authors), due=len(due))
        self.context.stats = self.stats
        semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

        async def run(author: AuthorSubscription) -> None:
            async with semaphore:
                await self.poll_author(author)

        results = await asyncio.gather(
            *(run(author) for author in due), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.opt(exception=result).warning("爱播轮询任务异常")
        self.stats.duration = time.monotonic() - started
        self.last_round_at = time.monotonic()
        logger.info(
            f"爱播轮询完成：{self.stats.due}/{self.stats.authors} 个作者到期，"
            f"耗时 {self.stats.duration:.1f} 秒，{self.stats.new_works} 个新作品，"
            f"发送 {self.stats.delivered} 次，放弃 {self.stats.abandoned} 个"
        )

    async def poll_author(self, author: AuthorSubscription) -> None:
        """抓取并投递一个作者的内容，失败时只影响该作者。"""

        state = self.state(author.key)
        self.in_flight.add(author.key)
        try:
            feeds = await self.fetch_feeds(author)
            async with self._send_lock:
                async with self.store.transaction():
                    await deliver_author(self.context, author, feeds)
        except asyncio.CancelledError:
            raise
        except PermanentError as exc:
            self._disable(author, state, exc)
        except Exception as exc:
            self._record_failure(author, state, exc)
        else:
            state.failure_count = 0
            state.last_error = ""
            state.last_success_at = time.monotonic()
            state.next_poll_at = time.monotonic() + self.settings.interval
        finally:
            self.in_flight.discard(author.key)

    async def fetch_feeds(
        self, author: AuthorSubscription
    ) -> dict[FeedKind, list[PlatformWork]]:
        """抓取作者的全部 feed；单个 feed 失败不影响其他 feed。"""

        adapter = adapter_for(author.platform)
        results: dict[FeedKind, list[PlatformWork]] = {}
        errors: list[Exception] = []
        for feed in adapter.feeds(self.settings):
            cursor = self._earliest_cursor(author, feed)
            try:
                results[feed] = await adapter.fetch(
                    self.settings, author.author_id, feed, cursor
                )
            except PermanentError:
                raise
            except TransientError as exc:
                errors.append(exc)
                logger.warning(
                    f"{platform_name(author.platform)}作者 {author.author_id} "
                    f"{feed} feed 请求失败：{exc}"
                )
        if not results and errors:
            raise errors[0]
        return results

    def status_text(self, authors: dict[str, AuthorSubscription]) -> str:
        """生成 ``爱播状态`` 的正文。"""

        return render(
            settings=self.settings,
            tick_seconds=self.tick_seconds(),
            fetch_concurrency=FETCH_CONCURRENCY,
            stats=self.stats,
            states=self.states,
            authors=authors,
            in_flight=self.in_flight,
            last_round_at=self.last_round_at,
            recent_abandoned=list(self.context.recent_abandoned),
        )

    def _record_failure(
        self, author: AuthorSubscription, state: AuthorPollState, exc: BaseException
    ) -> None:
        state.failure_count += 1
        state.next_poll_at = time.monotonic() + backoff_seconds(state.failure_count)
        state.last_error = short_reason(exc)
        logger.warning(
            f"{platform_name(author.platform)}作者 {author.author_id} 轮询失败："
            f"{exc}；{format_wait(state.next_poll_at - time.monotonic())}重试"
        )

    def _disable(
        self, author: AuthorSubscription, state: AuthorPollState, exc: BaseException
    ) -> None:
        state.disabled = True
        state.failure_count = 0
        state.last_error = short_reason(exc)
        state.next_poll_at = float("inf")
        logger.warning(
            f"{platform_name(author.platform)}作者 {author.author_id} 已停用：{exc}"
        )

    def _earliest_cursor(
        self, author: AuthorSubscription, feed: FeedKind
    ) -> Cursor | None:
        """返回该 feed 最早的游标，用于有界补发。"""

        cursors = [
            cursor
            for target_state in author.targets.values()
            if (cursor := target_state.cursors.get(feed)) is not None
        ]
        if not cursors:
            return None
        return min(cursors, key=lambda cursor: cursor.order)

    def _prune(self, authors: dict[str, AuthorSubscription]) -> None:
        """清掉已退订作者留下的运行时状态。"""

        active = set(authors)
        for author_key in list(self.states):
            if author_key not in active:
                del self.states[author_key]
        prune(self.context, authors)


__all__ = ["FETCH_CONCURRENCY", "TICK_MAX_SECONDS", "TICK_MIN_SECONDS", "Poller"]
