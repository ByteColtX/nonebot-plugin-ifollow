"""爱播订阅轮询：调度、抓取、发送和失败处理。

轮询是一个常驻协程：每隔 :func:`tick_seconds` 扫描一次到期作者，抓取阶段并发、
发送阶段串行。每个作者按自己的 ``next_poll_at`` 到期，所以轮询周期与作者数量无关，
启动后立即开始首轮。

作者大多是月更，轮询周期默认 30 分钟：相比 5 分钟能把请求量降一个量级，代价只是
推送晚半小时。扫描间隔跟着周期伸缩，周期调大时也不会白白唤醒。

一个作品发送失败有明确出口：作品不可用这类永久性错误立即放弃，网络和风控这类
暂时性错误重试 ``WORK_RETRY_LIMIT`` 次后放弃；放弃时推进游标，避免一个坏作品
把整个目标的后续内容全部卡住。
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass

from nonebot import logger
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg import Target

from .bilibili_media import bilibili_media_messages
from .bilibili_platform import (
    BilibiliRequestError,
    bilibili_dynamics_enabled,
    fetch_dynamic_works,
    fetch_video_works,
    is_bilibili_value,
    resolve_bilibili_author,
)
from .bilibili_platform import fetch_author_profile as fetch_bilibili_profile
from .config import config
from .douyin_media import douyin_media_messages
from .douyin_platform import DouyinRequestError, fetch_latest_works, resolve_sec_user_id
from .douyin_platform import fetch_author_profile as fetch_douyin_profile
from .media import SEPARATOR
from .models import AuthorSubscription, Platform, SubscriptionStore, TargetSubscription
from .platforms import AuthorInfo, Cursor, PlatformWork

TICK_MIN_SECONDS = 15.0
TICK_MAX_SECONDS = 300.0

FETCH_CONCURRENCY = 4
"""同时抓取的作者数量上限。"""

WORK_RETRY_LIMIT = 3
"""单个作品发往单个目标的暂时性失败次数上限，超过后放弃并推进游标。"""

POLL_BACKOFF_BASE_SECONDS = 30.0
POLL_BACKOFF_MAX_SECONDS = 300.0


def tick_seconds() -> float:
    """返回调度扫描间隔。

    扫描只负责发现到期作者，因此间隔取作者周期的四分之一即可，并夹在 15 秒到
    5 分钟之间：周期调大时不会为了一次扫描白唤醒，周期很小时也不会比周期还慢。

    Returns:
        扫描间隔，单位为秒。
    """

    interval = config.ifollow_sub_interval
    return min(max(interval / 4, TICK_MIN_SECONDS), TICK_MAX_SECONDS)


PERMANENT_WORK_ERROR_NAMES = frozenset({"ContentUnavailableError", "UnknownPlatform"})
"""parsehub 中表示「重试也没有意义」的异常类名。

这里按类名判断而不是导入这些异常：导入 parsehub 会连带加载 numpy/opencv，几秒的
阻塞不能落在事件循环上。``ContentUnavailableFallbackError`` 继承自
``ContentUnavailableError``，按 MRO 匹配可以一并覆盖。
"""

PERMANENT_TARGET_KEYWORDS = (
    "被移出该群",
    "重新加群",
    "群不存在",
    "不在该群",
    "不在群里",
    "群号不存在",
    "not in group",
    "group not found",
)
"""发送失败信息里表示目标群已不可用的关键字。"""


@dataclass
class AuthorPollState:
    """单个作者的运行时轮询状态，重启后清零。"""

    next_poll_at: float = 0.0
    failure_count: int = 0
    last_success_at: float = 0.0
    last_error: str = ""


@dataclass
class RoundStats:
    """最近一轮轮询的统计，供 ``爱播状态`` 和日志摘要使用。"""

    authors: int = 0
    due: int = 0
    duration: float = 0.0
    new_works: int = 0
    delivered: int = 0
    abandoned: int = 0


_states: dict[str, AuthorPollState] = {}
_work_attempts: dict[tuple[str, str, str, str], int] = {}
_permanent_targets: set[str] = set()
_in_flight: set[str] = set()
_recent_abandoned: deque[tuple[str, str]] = deque(maxlen=5)
_stats = RoundStats()
_send_lock = asyncio.Lock()
_task: asyncio.Task[None] | None = None
_last_round_at: float = 0.0


def start(store: SubscriptionStore) -> None:
    """启动常驻轮询协程，重复调用无副作用。

    Args:
        store: 订阅存储；协程只在启动时取一次引用。
    """

    global _task

    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_poll_loop(store))


async def stop() -> None:
    """停止常驻轮询协程，等待它退出。"""

    global _task

    task, _task = _task, None
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


async def _poll_loop(store: SubscriptionStore) -> None:
    """常驻轮询循环：扫描到期作者，然后睡到下一次扫描。"""

    while True:
        try:
            await _poll_round(store)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.opt(exception=True).warning("爱播轮询异常，下一轮继续")
        await asyncio.sleep(tick_seconds())


async def _poll_round(store: SubscriptionStore) -> None:
    """执行一轮：抓取所有到期作者，并输出一条摘要。"""

    global _last_round_at

    started = time.monotonic()
    authors = await store.snapshot()
    _prune_states(authors)
    now = time.monotonic()
    due = [
        author for author in authors.values() if _state(author.key).next_poll_at <= now
    ]
    if not due:
        return
    _stats.authors = len(authors)
    _stats.due = len(due)
    _stats.new_works = 0
    _stats.delivered = 0
    _stats.abandoned = 0
    semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def run(author: AuthorSubscription) -> None:
        async with semaphore:
            await _poll_author(store, author)

    results = await asyncio.gather(
        *(run(author) for author in due), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            logger.opt(exception=result).warning("爱播轮询任务异常")
    _stats.duration = time.monotonic() - started
    _last_round_at = time.monotonic()
    logger.info(
        f"爱播轮询完成：{_stats.due}/{_stats.authors} 个作者到期，"
        f"耗时 {_stats.duration:.1f} 秒，{_stats.new_works} 个新作品，"
        f"发送 {_stats.delivered} 次，放弃 {_stats.abandoned} 个"
    )


async def _poll_author(store: SubscriptionStore, author: AuthorSubscription) -> None:
    """抓取并投递一个作者的内容，失败时只让该作者退避。"""

    state = _state(author.key)
    _in_flight.add(author.key)
    try:
        feeds = await _fetch_feeds(author)
        async with _send_lock:
            await _deliver_author(store, author, feeds)
    except asyncio.CancelledError:
        raise
    except (DouyinRequestError, BilibiliRequestError) as exc:
        _record_failure(state, exc)
        logger.warning(
            f"{platform_name(author.platform)}作者 {author.author_id} 轮询失败：{exc}；"
            f"{_format_wait(state.next_poll_at - time.monotonic())}重试"
        )
    except Exception as exc:
        _record_failure(state, exc)
        logger.opt(exception=exc).warning(
            f"{platform_name(author.platform)}作者 {author.author_id} 轮询失败；"
            f"{_format_wait(state.next_poll_at - time.monotonic())}重试"
        )
    else:
        state.failure_count = 0
        state.last_error = ""
        state.last_success_at = time.monotonic()
        state.next_poll_at = time.monotonic() + config.ifollow_sub_interval
    finally:
        _in_flight.discard(author.key)


async def _fetch_feeds(author: AuthorSubscription) -> dict[str, list[PlatformWork]]:
    """抓取作者的全部 feed；单个 feed 失败不影响其他 feed。"""

    results: dict[str, list[PlatformWork]] = {}
    errors: list[Exception] = []
    for feed in _feeds_for_platform(author.platform):
        cursor = _earliest_cursor(author, feed)
        try:
            results[feed] = await _fetch_feed(
                author.platform, author.author_id, feed, cursor
            )
        except (DouyinRequestError, BilibiliRequestError) as exc:
            errors.append(exc)
            logger.warning(
                f"{platform_name(author.platform)}作者 {author.author_id} "
                f"{feed} feed 请求失败：{exc}"
            )
    if not results and errors:
        raise errors[0]
    return results


async def _fetch_feed(
    platform: Platform,
    author_id: str,
    feed: str,
    cursor: Cursor | None,
) -> list[PlatformWork]:
    """按平台和 feed 拉取作品列表。"""

    if platform == "douyin":
        return await fetch_latest_works(author_id, cursor) if feed == "video" else []
    if feed == "video":
        return await fetch_video_works(author_id, cursor)
    if feed == "dynamic":
        return await fetch_dynamic_works(author_id, cursor)
    return []


async def fetch_author_feeds(
    platform: Platform, author_id: str
) -> dict[str, list[PlatformWork]]:
    """订阅时拉取一次作者的全部 feed，用于初始化游标。"""

    feeds: dict[str, list[PlatformWork]] = {}
    for feed in _feeds_for_platform(platform):
        feeds[feed] = await _fetch_feed(platform, author_id, feed, None)
    return feeds


async def resolve_author(value: str) -> tuple[Platform, AuthorInfo]:
    """按链接识别平台并解析作者。"""

    if is_bilibili_value(value) or value.strip().isdigit():
        if value.strip().isdigit():
            return "bilibili", await fetch_bilibili_profile(value.strip())
        return "bilibili", await resolve_bilibili_author(value)
    sec_user_id = await resolve_sec_user_id(value)
    works = await fetch_latest_works(sec_user_id)
    if works:
        return "douyin", works[0].author
    name, handle = await fetch_douyin_profile(sec_user_id)
    return "douyin", AuthorInfo(sec_user_id, name or sec_user_id, handle)


async def _deliver_author(
    store: SubscriptionStore,
    author: AuthorSubscription,
    feeds: dict[str, list[PlatformWork]],
) -> None:
    """投递一个作者所有 feed 的新作品。"""

    latest = max(
        (work for works in feeds.values() for work in works),
        key=lambda work: (work.create_time, work.work_id),
        default=None,
    )
    if latest is not None:
        await store.update_author_metadata(
            author.platform,
            author.author_id,
            latest.author.name,
            latest.author.handle,
            latest.create_time,
        )
    video_bvids = {work.bvid for work in feeds.get("video", []) if work.bvid}
    sent_bvids = {target_key: set(video_bvids) for target_key in author.targets}
    for feed, works in feeds.items():
        await _deliver_feed(store, author, feed, works, sent_bvids)


async def _deliver_feed(
    store: SubscriptionStore,
    author: AuthorSubscription,
    feed: str,
    works: list[PlatformWork],
    sent_bvids: dict[str, set[str]],
) -> None:
    """投递一个 feed 的新作品：作品在外层，目标在内层。"""

    if not works:
        return
    limit = config.ifollow_sub_max_items
    pending: dict[str, list[PlatformWork]] = {}
    for target_key, target_state in author.targets.items():
        cursor = target_state.cursors.get(feed)
        if cursor is None:
            await _advance(store, author, target_key, feed, works[0])
            continue
        unseen = _unseen(works, cursor)
        if len(unseen) > limit:
            logger.warning(
                f"{platform_name(author.platform)}作者 {author.author_id} 的 {feed} "
                f"积压 {len(unseen)} 条，只补发最新 {limit} 条，"
                f"其余 {len(unseen) - limit} 条不再补发"
            )
            unseen = unseen[-limit:]
        if unseen:
            pending[target_key] = unseen
    if not pending:
        return
    targets_by_work: dict[str, list[str]] = {}
    for target_key, unseen in pending.items():
        for work in unseen:
            targets_by_work.setdefault(work.work_id, []).append(target_key)
    ordered = [work for work in reversed(works) if work.work_id in targets_by_work]
    _stats.new_works += len(ordered)
    stalled: set[str] = set()
    for work in ordered:
        targets = [
            target_key
            for target_key in targets_by_work[work.work_id]
            if target_key not in stalled
        ]
        if not targets:
            continue
        await _deliver_work(store, author, feed, work, targets, stalled, sent_bvids)


async def _deliver_work(
    store: SubscriptionStore,
    author: AuthorSubscription,
    feed: str,
    work: PlatformWork,
    target_keys: list[str],
    stalled: set[str],
    sent_bvids: dict[str, set[str]],
) -> None:
    """解析下载一次，然后依次发给该作品的所有目标。"""

    sendable: list[str] = []
    for target_key in target_keys:
        if (
            feed == "dynamic"
            and work.bvid
            and work.bvid in sent_bvids.get(target_key, set())
        ):
            await _advance(store, author, target_key, feed, work)
            continue
        sendable.append(target_key)
    if not sendable:
        return
    try:
        async with _media_for(work) as messages:
            for target_key in sendable:
                target_state = author.targets[target_key]
                try:
                    await _send_work(messages, target_state)
                except Exception as exc:
                    await _handle_target_failure(
                        store, author, feed, work, target_key, exc, stalled
                    )
                    continue
                _permanent_targets.discard(target_key)
                if work.bvid:
                    sent_bvids.setdefault(target_key, set()).add(work.bvid)
                await _advance(store, author, target_key, feed, work)
                _stats.delivered += 1
    except Exception as exc:
        for target_key in sendable:
            await _handle_target_failure(
                store, author, feed, work, target_key, exc, stalled
            )


async def _send_work(
    messages: list[UniMessage], target_state: TargetSubscription
) -> None:
    """把一条作品的消息发给一个目标；订阅人提醒合并到文案末尾。"""

    target = Target.load(target_state.target.copy())
    outgoing: list[UniMessage] = list(messages)
    if messages and target_state.subscriber_ids:
        footer = UniMessage(f"\n{SEPARATOR}\n")
        for index, subscriber_id in enumerate(target_state.subscriber_ids):
            if index:
                footer += " "
            footer += UniMessage.at(subscriber_id)
        outgoing[0] = messages[0] + footer
    for message in outgoing:
        await message.send(target=target)


async def _handle_target_failure(
    store: SubscriptionStore,
    author: AuthorSubscription,
    feed: str,
    work: PlatformWork,
    target_key: str,
    exc: Exception,
    stalled: set[str],
) -> None:
    """按失败类型决定：跳过目标、重试作品，或放弃作品并推进游标。"""

    attempt_key = (author.key, target_key, feed, work.work_id)
    label = _target_label(author, target_key)
    if _is_permanent_target_error(exc):
        _work_attempts.pop(attempt_key, None)
        stalled.add(target_key)
        if target_key in _permanent_targets:
            logger.debug(f"目标 {label} 仍不可用，本轮跳过")
        else:
            _permanent_targets.add(target_key)
            logger.warning(
                f"目标 {label} 已不可用（{exc}），跳过该目标且不重试；"
                "如果确实不在该群了，可以取消对应订阅"
            )
        return
    attempts = _work_attempts.get(attempt_key, 0) + 1
    if _is_permanent_work_error(exc) or attempts >= WORK_RETRY_LIMIT:
        _work_attempts.pop(attempt_key, None)
        await _abandon(store, author, feed, work, target_key, exc)
        stalled.discard(target_key)
        return
    _work_attempts[attempt_key] = attempts
    stalled.add(target_key)
    logger.warning(
        f"{platform_name(work.platform)}作品 {work.work_id} 发往目标 {label} 失败"
        f"（第 {attempts}/{WORK_RETRY_LIMIT} 次）：{exc}"
    )


async def _abandon(
    store: SubscriptionStore,
    author: AuthorSubscription,
    feed: str,
    work: PlatformWork,
    target_key: str,
    exc: Exception,
) -> None:
    """放弃一个作品：推进游标并记录原因。"""

    await _advance(store, author, target_key, feed, work)
    _stats.abandoned += 1
    reason = _short_reason(exc)
    _recent_abandoned.append((work.title or work.work_id, reason))
    logger.warning(
        f"放弃{platform_name(work.platform)}作品 {work.work_id}"
        f"（{work.title or work.work_id}）"
        f"发往目标 {_target_label(author, target_key)}："
        f"{reason}；已跳过该作品并推进游标"
    )


async def _advance(
    store: SubscriptionStore,
    author: AuthorSubscription,
    target_key: str,
    feed: str,
    work: PlatformWork,
) -> None:
    """把某个目标的游标推进到指定作品。"""

    cursor = Cursor(work.work_id, work.create_time)
    await store.update_cursor(
        author.platform, author.author_id, target_key, feed, cursor
    )
    target_state = author.targets.get(target_key)
    if target_state is not None:
        target_state.cursors[feed] = cursor


def _unseen(works: list[PlatformWork], cursor: Cursor) -> list[PlatformWork]:
    """返回游标之后的新作品，最旧在前。

    游标作品可能已被作者删除，因此优先按发布时间判断；v1 迁移来的游标没有时间，
    退回到「遇到游标作品为止」，若游标已不在页内则只补发最新一条，避免刷屏。
    """

    unseen: list[PlatformWork] = []
    reached = False
    for work in works:
        if work.work_id == cursor.work_id:
            reached = True
            break
        if cursor.time and work.create_time and work.create_time <= cursor.time:
            reached = True
            break
        unseen.append(work)
    if not reached and not cursor.time and len(unseen) > 1:
        logger.warning(
            f"游标 {cursor.work_id} 不在最新一页且没有发布时间，只补发最新一条"
        )
        return unseen[:1]
    return list(reversed(unseen))


def _media_for(work: PlatformWork) -> AbstractAsyncContextManager[list[UniMessage]]:
    """按平台选择解析下载上下文。"""

    if work.platform == "douyin":
        return douyin_media_messages(work)
    return bilibili_media_messages(work)


def _feeds_for_platform(platform: Platform) -> tuple[str, ...]:
    if platform == "douyin":
        return ("video",)
    if bilibili_dynamics_enabled():
        return ("video", "dynamic")
    return ("video",)


def _earliest_cursor(author: AuthorSubscription, feed: str) -> Cursor | None:
    """返回作者该 feed 最早的游标，用于有限分页追赶。"""

    cursors = [
        cursor
        for target_state in author.targets.values()
        if (cursor := target_state.cursors.get(feed)) is not None
    ]
    if not cursors:
        return None
    return min(cursors, key=lambda cursor: cursor.time)


def _state(author_key: str) -> AuthorPollState:
    return _states.setdefault(author_key, AuthorPollState())


def _prune_states(authors: dict[str, AuthorSubscription]) -> None:
    """清掉已退订作者和目标留下的运行时状态。"""

    active = set(authors)
    for author_key in list(_states):
        if author_key not in active:
            _states.pop(author_key, None)
    for attempt_key in list(_work_attempts):
        if attempt_key[0] not in active:
            _work_attempts.pop(attempt_key, None)
    active_targets = {
        target_key for author in authors.values() for target_key in author.targets
    }
    _permanent_targets.intersection_update(active_targets)


def _record_failure(state: AuthorPollState, exc: BaseException) -> None:
    state.failure_count += 1
    delay = _exponential_backoff_seconds(
        state.failure_count,
        base_seconds=POLL_BACKOFF_BASE_SECONDS,
        max_seconds=POLL_BACKOFF_MAX_SECONDS,
    )
    state.next_poll_at = time.monotonic() + delay
    state.last_error = _short_reason(exc)


def _exponential_backoff_seconds(
    attempt: int, *, base_seconds: float, max_seconds: float
) -> float:
    return min(base_seconds * (2 ** max(attempt - 1, 0)), max_seconds)


def _is_permanent_work_error(exc: BaseException) -> bool:
    """判断作品级失败是否重试也没有意义（作品已删除、平台不支持）。"""

    return any(cls.__name__ in PERMANENT_WORK_ERROR_NAMES for cls in type(exc).__mro__)


def _is_permanent_target_error(exc: BaseException) -> bool:
    """判断发送失败是否因为目标群已不可用（机器人被踢、群解散）。

    Args:
        exc: 发送时抛出的异常。

    Returns:
        目标群已不可用时返回 True，此时重试没有意义。
    """

    info = getattr(exc, "info", None)
    if isinstance(info, dict):
        text = " ".join(str(info.get(key) or "") for key in ("message", "wording"))
    else:
        text = str(exc)
    lowered = text.lower()
    return any(keyword in lowered for keyword in PERMANENT_TARGET_KEYWORDS)


def _target_label(author: AuthorSubscription, target_key: str) -> str:
    target_state = author.targets.get(target_key)
    if target_state is None:
        return target_key
    return str(target_state.target.get("id") or target_key)


def _short_reason(exc: BaseException) -> str:
    """取异常信息的第一行并截断，用于日志和状态展示。"""

    text = str(exc).strip().splitlines()
    if not text:
        return type(exc).__name__
    return text[0][:80]


def platform_name(platform: str) -> str:
    return "抖音" if platform == "douyin" else "B站"


def _format_age(seconds: float) -> str:
    if seconds < 60:
        return f"{max(0, int(seconds))} 秒前"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    return f"{int(seconds // 3600)} 小时前"


def _format_wait(seconds: float) -> str:
    if not math.isfinite(seconds):
        return "稍后"
    if seconds <= 0:
        return "立即"
    if seconds < 60:
        return f"{int(seconds)} 秒后"
    return f"{int(seconds // 60)} 分钟后"


def status_text(authors: dict[str, AuthorSubscription]) -> str:
    """生成 ``爱播状态`` 的正文。"""

    now = time.monotonic()
    lines = [
        "爱播状态",
        f"调度：每 {int(tick_seconds())} 秒扫描 · "
        f"作者周期 {config.ifollow_sub_interval} 秒 · "
        f"抓取并发 {FETCH_CONCURRENCY}",
    ]
    if _last_round_at:
        lines.append(
            f"上次轮询：{_format_age(now - _last_round_at)}"
            f"（耗时 {_stats.duration:.1f} 秒，"
            f"{_stats.due}/{_stats.authors} 个作者到期）"
        )
    else:
        lines.append("上次轮询：尚未运行")
    if _in_flight:
        lines.append(f"正在抓取：{len(_in_flight)} 个作者")
    lines.append(
        f"本轮：{_stats.new_works} 个新作品 · 发送 {_stats.delivered} 次 · "
        f"放弃 {_stats.abandoned} 个"
    )
    if _recent_abandoned:
        title, reason = _recent_abandoned[-1]
        lines.append(f"最近放弃：{title}（{reason}）")
    unhealthy = [
        (author_key, state)
        for author_key, state in _states.items()
        if state.failure_count and author_key in authors
    ]
    if not unhealthy:
        lines.append("所有作者状态正常。")
        return "\n".join(lines)
    lines.append("")
    lines.append(f"异常作者（{len(unhealthy)} 个）：")
    for author_key, state in sorted(unhealthy)[:10]:
        author = authors[author_key]
        lines.append(
            f"{platform_name(author.platform)} {author.author_name}："
            f"连续失败 {state.failure_count} 次（{state.last_error}），"
            f"{_format_wait(state.next_poll_at - now)}重试"
        )
    return "\n".join(lines)


__all__ = [
    "AuthorPollState",
    "fetch_author_feeds",
    "resolve_author",
    "start",
    "status_text",
    "stop",
    "tick_seconds",
]
