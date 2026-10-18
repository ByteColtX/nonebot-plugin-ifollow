"""把新作品投递到订阅目标：筛选、补发上限、发送与游标推进。"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field

from nonebot import logger
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg import Target

from .bilibili_media import bilibili_media_messages
from .douyin_media import douyin_media_messages
from .failures import (
    WORK_RETRY_LIMIT,
    is_permanent_target_error,
    is_permanent_work_error,
    short_reason,
)
from .media import SEPARATOR
from .models import AuthorSubscription, SubscriptionStore, TargetSubscription
from .platforms import Cursor, FeedKind, PlatformWork, is_newer
from .settings import Settings
from .status import RoundStats, platform_name

SENT_MEMORY = 50
"""每个目标记住最近多少个已发作品标识，用于跨 feed 去重。"""

AttemptKey = tuple[str, str, str, str]


@dataclass
class DeliveryContext:
    """一次投递需要的共享状态，由 Poller 持有。"""

    store: SubscriptionStore
    settings: Settings
    stats: RoundStats
    attempts: dict[AttemptKey, int] = field(default_factory=dict)
    permanent_targets: set[str] = field(default_factory=set)
    recent_abandoned: deque[tuple[str, str]] = field(
        default_factory=lambda: deque(maxlen=5)
    )
    sent: dict[str, deque[str]] = field(default_factory=dict)


def attempt_key(
    author: AuthorSubscription, target_key: str, feed: FeedKind, work: PlatformWork
) -> AttemptKey:
    """返回「某作品发往某目标」的重试计数键。"""

    return (author.key, target_key, feed, work.work_id)


def media_for(
    work: PlatformWork, settings: Settings
) -> AbstractAsyncContextManager[list[UniMessage]]:
    """按平台选择解析下载上下文。"""

    if work.platform == "douyin":
        return douyin_media_messages(work, settings)
    return bilibili_media_messages(work, settings)


def sent_identities(ctx: DeliveryContext, target_key: str) -> deque[str]:
    """取某个目标的已发标识环形缓冲。"""

    return ctx.sent.setdefault(target_key, deque(maxlen=SENT_MEMORY))


async def deliver_author(
    ctx: DeliveryContext,
    author: AuthorSubscription,
    feeds: dict[FeedKind, list[PlatformWork]],
) -> None:
    """投递一个作者所有 feed 的新作品。"""

    latest = max(
        (work for works in feeds.values() for work in works),
        key=lambda work: work.order,
        default=None,
    )
    if latest is not None:
        await ctx.store.update_author_metadata(
            author.platform,
            author.author_id,
            latest.author.name,
            latest.author.handle,
            latest.create_time,
        )
    for feed, works in feeds.items():
        await deliver_feed(ctx, author, feed, works)


async def deliver_feed(
    ctx: DeliveryContext,
    author: AuthorSubscription,
    feed: FeedKind,
    works: list[PlatformWork],
) -> None:
    """投递一个 feed 的新作品：作品在外层，目标在内层。"""

    if not works:
        return
    pending: dict[str, list[PlatformWork]] = {}
    for target_key, target_state in author.targets.items():
        cursor = target_state.cursors.get(feed)
        if cursor is None:
            await advance(ctx, author, target_key, feed, works[0])
            continue
        unseen = _unseen(works, cursor, ctx.settings.max_items)
        if unseen:
            pending[target_key] = unseen
    if not pending:
        return
    targets_by_work: dict[str, list[str]] = {}
    for target_key, unseen in pending.items():
        for work in unseen:
            targets_by_work.setdefault(work.work_id, []).append(target_key)
    ordered = [work for work in reversed(works) if work.work_id in targets_by_work]
    ctx.stats.new_works += len(ordered)
    stalled: set[str] = set()
    for work in ordered:
        targets = [
            target_key
            for target_key in targets_by_work[work.work_id]
            if target_key not in stalled
        ]
        if not targets:
            continue
        await deliver_work(ctx, author, feed, work, targets, stalled)


def _unseen(
    works: list[PlatformWork], cursor: Cursor, limit: int
) -> list[PlatformWork]:
    """返回游标之后的新作品，最旧在前，超过补发上限时保留最新的几条。"""

    unseen = [work for work in works if is_newer(work, cursor)]
    unseen.reverse()
    if len(unseen) > limit:
        logger.warning(
            f"积压 {len(unseen)} 条，只补发最新 {limit} 条，"
            f"其余 {len(unseen) - limit} 条不再补发"
        )
        return unseen[-limit:]
    return unseen


async def deliver_work(
    ctx: DeliveryContext,
    author: AuthorSubscription,
    feed: FeedKind,
    work: PlatformWork,
    target_keys: list[str],
    stalled: set[str],
) -> None:
    """解析下载一次，然后依次发给该作品的所有目标。"""

    sendable: list[str] = []
    for target_key in target_keys:
        if work.identity in sent_identities(ctx, target_key):
            await advance(ctx, author, target_key, feed, work)
            continue
        sendable.append(target_key)
    if not sendable:
        return
    try:
        async with media_for(work, ctx.settings) as messages:
            for target_key in sendable:
                target_state = author.targets[target_key]
                try:
                    await send_work(messages, target_state)
                except Exception as exc:
                    await handle_target_failure(
                        ctx, author, feed, work, target_key, exc, stalled
                    )
                    continue
                ctx.permanent_targets.discard(target_key)
                ctx.attempts.pop(attempt_key(author, target_key, feed, work), None)
                sent_identities(ctx, target_key).append(work.identity)
                await advance(ctx, author, target_key, feed, work)
                ctx.stats.delivered += 1
    except Exception as exc:
        for target_key in sendable:
            await handle_target_failure(
                ctx, author, feed, work, target_key, exc, stalled
            )


async def send_work(
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


async def handle_target_failure(
    ctx: DeliveryContext,
    author: AuthorSubscription,
    feed: FeedKind,
    work: PlatformWork,
    target_key: str,
    exc: Exception,
    stalled: set[str],
) -> None:
    """按失败类型决定：跳过目标、重试作品，或放弃作品并推进游标。"""

    key = attempt_key(author, target_key, feed, work)
    label = _target_label(author, target_key)
    if is_permanent_target_error(exc):
        ctx.attempts.pop(key, None)
        stalled.add(target_key)
        if target_key in ctx.permanent_targets:
            logger.debug(f"目标 {label} 仍不可用，本轮跳过")
        else:
            ctx.permanent_targets.add(target_key)
            logger.warning(
                f"目标 {label} 已不可用（{exc}），跳过该目标且不重试；"
                "如果确实不在该群了，可以取消对应订阅"
            )
        return
    attempts = ctx.attempts.get(key, 0) + 1
    if is_permanent_work_error(exc) or attempts >= WORK_RETRY_LIMIT:
        ctx.attempts.pop(key, None)
        await abandon(ctx, author, feed, work, target_key, exc)
        stalled.discard(target_key)
        return
    ctx.attempts[key] = attempts
    stalled.add(target_key)
    logger.warning(
        f"{platform_name(work.platform)}作品 {work.work_id} 发往目标 {label} 失败"
        f"（第 {attempts}/{WORK_RETRY_LIMIT} 次）：{exc}"
    )


async def abandon(
    ctx: DeliveryContext,
    author: AuthorSubscription,
    feed: FeedKind,
    work: PlatformWork,
    target_key: str,
    exc: Exception,
) -> None:
    """放弃一个作品：推进游标并记录原因。"""

    await advance(ctx, author, target_key, feed, work)
    ctx.stats.abandoned += 1
    reason = short_reason(exc)
    ctx.recent_abandoned.append((work.title or work.work_id, reason))
    logger.warning(
        f"放弃{platform_name(work.platform)}作品 {work.work_id}"
        f"（{work.title or work.work_id}）"
        f"发往目标 {_target_label(author, target_key)}："
        f"{reason}；已跳过该作品并推进游标"
    )


async def advance(
    ctx: DeliveryContext,
    author: AuthorSubscription,
    target_key: str,
    feed: FeedKind,
    work: PlatformWork,
) -> None:
    """把某个目标的游标推进到指定作品。"""

    cursor = Cursor(work.work_id, work.create_time)
    await ctx.store.update_cursor(
        author.platform, author.author_id, target_key, feed, cursor
    )
    target_state = author.targets.get(target_key)
    if target_state is not None:
        target_state.cursors[feed] = cursor


def _target_label(author: AuthorSubscription, target_key: str) -> str:
    target_state = author.targets.get(target_key)
    if target_state is None:
        return target_key
    return str(target_state.target.get("id") or target_key)


def prune(ctx: DeliveryContext, authors: dict[str, AuthorSubscription]) -> None:
    """清掉已退订作者和目标留下的运行时状态。"""

    active = set(authors)
    for attempt in list(ctx.attempts):
        if attempt[0] not in active:
            del ctx.attempts[attempt]
    active_targets = {
        target_key for author in authors.values() for target_key in author.targets
    }
    ctx.permanent_targets.intersection_update(active_targets)
    for target_key in list(ctx.sent):
        if target_key not in active_targets:
            del ctx.sent[target_key]


def iter_feeds(platform: str) -> Iterator[FeedKind]:
    """返回平台的基础 feed，动态开关由 Settings 决定。"""

    del platform
    yield "video"


__all__ = [
    "DeliveryContext",
    "abandon",
    "advance",
    "attempt_key",
    "deliver_author",
    "deliver_feed",
    "deliver_work",
    "handle_target_failure",
    "media_for",
    "prune",
    "send_work",
    "sent_identities",
]
