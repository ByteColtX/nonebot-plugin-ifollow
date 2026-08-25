"""爱播统一命令、轮询和消息发送。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from nonebot import logger, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg import Target, get_target
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_parser.renders import get_renderer

from .bilibili_parser import IFollowBilibiliParser
from .bilibili_platform import (
    BilibiliRequestError,
    fetch_dynamic_works,
    fetch_video_works,
    is_bilibili_value,
    resolve_bilibili_author,
)
from .bilibili_platform import (
    fetch_author_profile as fetch_bilibili_profile,
)
from .config import config
from .douyin_platform import (
    DouyinRequestError,
    fetch_latest_works,
    resolve_sec_user_id,
)
from .douyin_platform import (
    fetch_author_profile as fetch_douyin_profile,
)
from .models import (
    AuthorSubscription,
    Platform,
    SubscriptionStore,
    TargetSubscription,
    make_target_key,
)
from .parser import DouyinSubscribeParser
from .platforms import AuthorInfo, PlatformWork

store = SubscriptionStore()
poll_lock = asyncio.Lock()
author_poll_states: dict[str, AuthorPollState] = {}
send_retry_attempts: dict[tuple[str, str, str, str, str], int] = {}
send_retry_until: dict[tuple[str, str, str, str, str], float] = {}
POLL_BACKOFF_BASE_SECONDS = 30.0
POLL_BACKOFF_MAX_SECONDS = 300.0
SEND_BACKOFF_BASE_SECONDS = 30.0
SEND_BACKOFF_MAX_SECONDS = 300.0


@dataclass
class AuthorPollState:
    """记录单个平台作者的轮询失败和下一次重试时间。"""

    failure_count: int = 0
    retry_at: float = 0.0


@on_command("爱播订阅", aliases={"订阅爱播"}, priority=5, block=True).handle()
async def subscribe(event: Event, bot: Bot, args: Message = CommandArg()) -> None:
    """订阅一个抖音或 B 站作者。"""

    value = args.extract_plain_text().strip()
    if not value:
        value = await _get_replied_message_text(event, bot)
    if not value:
        await UniMessage("用法：爱播订阅 <抖音或 B 站作者主页链接、作品链接>").finish()

    target = get_target(event, bot)
    target_key = _target_key(target)
    subscriber_id = event.get_user_id()
    try:
        platform, author = await _resolve_author(value)
    except Exception as exc:
        logger.opt(exception=True).warning("爱播订阅解析失败")
        await UniMessage(f"订阅失败：{exc}").finish()

    authors = await store.snapshot()
    author_key = f"{platform}:{author.author_id}"
    existing_author = authors.get(author_key)
    if existing_author and target_key in existing_author.targets:
        if await store.add_subscriber(
            platform, author.author_id, target_key, subscriber_id
        ):
            await UniMessage(
                f"已加入{_platform_name(platform)}作者 "
                f"{existing_author.author_name} 的更新提醒。"
            ).send(target=target, bot=bot)
            return
        await UniMessage(
            f"当前会话已订阅{_platform_name(platform)}作者 "
            f"{existing_author.author_name}，无需重复订阅。"
        ).send(target=target, bot=bot)
        return

    try:
        feeds = await _fetch_author_feeds(platform, author.author_id)
    except Exception as exc:
        logger.opt(exception=True).warning("爱播订阅获取作品失败")
        await UniMessage(f"订阅失败：{exc}").finish()

    cursors: dict[str, str | None] = {
        feed: works[0].work_id for feed, works in feeds.items() if works
    }
    latest = max(
        (work for works in feeds.values() for work in works),
        key=lambda work: (work.create_time, work.work_id),
        default=None,
    )
    if latest is not None:
        author = latest.author
    added = await store.add_target(
        platform,
        author.author_id,
        author.name,
        target_key,
        target.dump(),
        cursors,
        subscriber_id=subscriber_id,
        author_handle=author.handle,
        last_update_time=latest.create_time if latest else 0,
    )
    if not added:
        if await store.add_subscriber(
            platform, author.author_id, target_key, subscriber_id
        ):
            await UniMessage(f"已加入{_platform_name(platform)}作者更新提醒。").send(
                target=target, bot=bot
            )
            return
        await UniMessage("当前会话已订阅该作者，无需重复订阅。").send(
            target=target, bot=bot
        )
        return
    await UniMessage(
        f"已订阅{_platform_name(platform)}作者 {author.name}。\n"
        "首次订阅不会补发历史内容，有新作品或动态时会自动发送。"
    ).send(target=target, bot=bot)


@on_command(
    "爱播取消订阅",
    aliases={"取消爱播订阅", "爱播订阅取消"},
    priority=5,
    block=True,
).handle()
async def unsubscribe(event: Event, bot: Bot, args: Message = CommandArg()) -> None:
    """取消当前会话中的作者订阅。"""

    value = args.extract_plain_text().strip()
    if not value:
        await UniMessage("用法：爱播取消订阅 <作者主页链接或作品链接>").finish()
    try:
        platform, author = await _resolve_author(value)
    except Exception as exc:
        await UniMessage(f"取消失败：{exc}").finish()

    target = get_target(event, bot)
    removed, target_removed = await store.remove_subscriber(
        platform, author.author_id, _target_key(target), event.get_user_id()
    )
    if removed and target_removed:
        await UniMessage("已取消订阅。").send(target=target, bot=bot)
    elif removed:
        await UniMessage("已取消你的更新提醒，其他订阅人仍会收到通知。").send(
            target=target, bot=bot
        )
    else:
        await UniMessage("当前会话没有你的这个订阅。").send(target=target, bot=bot)


@on_command("爱播订阅列表", aliases={"爱播列表"}, priority=5, block=True).handle()
async def list_subscriptions(event: Event, bot: Bot) -> None:
    """列出当前会话的爱播订阅。"""

    target = get_target(event, bot)
    target_key = _target_key(target)
    authors = await store.snapshot()
    items = [author for author in authors.values() if target_key in author.targets]
    if not items:
        message = "当前会话没有爱播订阅。"
    else:
        lines: list[str] = []
        for index, author in enumerate(items, 1):
            handle = _format_handle(author)
            updated_at = _format_update_time(author.last_update_time)
            lines.append(
                f"{index}. {_platform_name(author.platform)} {author.author_name}"
                f"（{handle}）{updated_at}"
            )
        message = "当前会话的爱播订阅：\n序号 平台 昵称（标识） 最近更新\n" + "\n".join(
            lines
        )
    await UniMessage(message).send(target=target, bot=bot)


@scheduler.scheduled_job(
    "interval",
    seconds=config.ifollow_subscribe_interval,
    id="nonebot-plugin-ifollow-poll",
)
async def poll_subscriptions() -> None:
    """轮询所有平台作者并向订阅目标发送新内容。"""

    if poll_lock.locked():
        logger.warning("上一次爱播订阅轮询尚未完成，本次跳过")
        return
    async with poll_lock:
        authors = await store.snapshot()
        active_author_ids = set(authors)
        for author_key in set(author_poll_states) - active_author_ids:
            author_poll_states.pop(author_key, None)
        request_delay = max(
            1.0, config.ifollow_subscribe_interval / max(len(authors), 1)
        )
        for index, author in enumerate(authors.values()):
            state = author_poll_states.setdefault(author.key, AuthorPollState())
            if time.monotonic() < state.retry_at:
                continue
            try:
                await _poll_author(author)
            except (DouyinRequestError, BilibiliRequestError) as exc:
                attempt, delay = _record_author_failure(
                    author.key, retryable=exc.retryable
                )
                logger.warning(
                    f"{_platform_name(author.platform)}作者 {author.author_id} "
                    f"轮询失败：{exc}；"
                    f"第 {attempt} 次重试将在 {delay:.0f} 秒后进行"
                )
            except Exception:
                attempt, delay = _record_author_failure(author.key)
                logger.opt(exception=True).warning(
                    f"{_platform_name(author.platform)}作者 {author.author_id} "
                    "轮询失败；"
                    f"第 {attempt} 次重试将在 {delay:.0f} 秒后进行"
                )
            else:
                author_poll_states.pop(author.key, None)
            if index < len(authors) - 1:
                await asyncio.sleep(request_delay)


async def _poll_author(author: AuthorSubscription) -> None:
    """轮询单个作者的各个 feed。"""

    feed_results: dict[str, list[PlatformWork]] = {}
    errors: list[Exception] = []
    for feed in _feeds_for_platform(author.platform):
        cursor = _cursor_for_all_targets(author, feed)
        try:
            feed_results[feed] = await _fetch_feed(author, feed, cursor)
        except (DouyinRequestError, BilibiliRequestError) as exc:
            errors.append(exc)
            logger.warning(
                f"{_platform_name(author.platform)}作者 {author.author_id} "
                f"{feed} feed 请求失败：{exc}"
            )
    if not feed_results and errors:
        raise errors[0]
    latest = max(
        (work for works in feed_results.values() for work in works),
        key=lambda work: (work.create_time, work.work_id),
        default=None,
    )
    if latest:
        await store.update_author_metadata(
            author.platform,
            author.author_id,
            latest.author.name,
            latest.author.handle,
            latest.create_time,
        )
    video_bvids = {work.bvid for work in feed_results.get("video", []) if work.bvid}
    dedupe_bvids = {target_key: set(video_bvids) for target_key in author.targets}
    for feed in _feeds_for_platform(author.platform):
        works = feed_results.get(feed)
        if works is None:
            continue
        for target_key, target_state in author.targets.items():
            await _deliver_new_works(
                author,
                target_key,
                target_state,
                feed,
                works,
                dedupe_bvids[target_key],
            )
    if errors and not feed_results:
        raise errors[0]


async def _deliver_new_works(
    author: AuthorSubscription,
    target_key: str,
    target_state: TargetSubscription,
    feed: str,
    works: list[PlatformWork],
    sent_bvids: set[str],
) -> None:
    """发送一个 feed 中的新内容，并在成功后推进游标。"""

    if not works:
        return
    cursor = target_state.cursors.get(feed)
    if cursor is None:
        await store.update_cursor(
            author.platform, author.author_id, target_key, feed, works[0].work_id
        )
        return
    unseen: list[PlatformWork] = []
    for work in works:
        if work.work_id == cursor:
            break
        unseen.append(work)
    target = Target.load(target_state.target.copy())
    for work in reversed(unseen):
        retry_key = (
            author.platform,
            author.author_id,
            target_key,
            feed,
            work.work_id,
        )
        if time.monotonic() < send_retry_until.get(retry_key, 0.0):
            return
        if feed == "dynamic" and work.bvid and work.bvid in sent_bvids:
            await store.update_cursor(
                author.platform, author.author_id, target_key, feed, work.work_id
            )
            continue
        try:
            await _send_work(work.url, work.platform, target)
        except Exception:
            attempt = send_retry_attempts.get(retry_key, 0) + 1
            send_retry_attempts[retry_key] = attempt
            delay = _exponential_backoff_seconds(
                attempt,
                base_seconds=SEND_BACKOFF_BASE_SECONDS,
                max_seconds=SEND_BACKOFF_MAX_SECONDS,
            )
            send_retry_until[retry_key] = time.monotonic() + delay
            logger.opt(exception=True).warning(
                f"向目标 {target.id} 发送{_platform_name(work.platform)}内容 "
                f"{work.work_id} 失败，第 {attempt} 次重试将在 {delay:.0f} 秒后进行"
            )
            return
        send_retry_attempts.pop(retry_key, None)
        send_retry_until.pop(retry_key, None)
        if work.bvid:
            sent_bvids.add(work.bvid)
        await _notify_subscribers(target, target_state.subscriber_ids)
        await store.update_cursor(
            author.platform, author.author_id, target_key, feed, work.work_id
        )


async def _resolve_author(value: str) -> tuple[Platform, AuthorInfo]:
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


async def _fetch_author_feeds(
    platform: Platform, author_id: str
) -> dict[str, list[PlatformWork]]:
    feeds: dict[str, list[PlatformWork]] = {}
    for feed in _feeds_for_platform(platform):
        feeds[feed] = await _fetch_feed(platform, author_id, feed, None)
    return feeds


async def _fetch_feed(
    author_or_platform: AuthorSubscription | Platform,
    feed_or_author_id: str,
    cursor_or_feed: str | None,
    maybe_cursor: str | None = None,
) -> list[PlatformWork]:
    """统一调用平台 feed，兼容作者对象和显式参数。"""

    if isinstance(author_or_platform, AuthorSubscription):
        platform = author_or_platform.platform
        author_id = author_or_platform.author_id
        feed = feed_or_author_id
        cursor = cursor_or_feed
    else:
        platform = author_or_platform
        author_id = feed_or_author_id
        feed = cast(str, cursor_or_feed)
        cursor = maybe_cursor
    if platform == "douyin":
        if feed != "video":
            return []
        return await fetch_latest_works(author_id)
    if feed == "video":
        return await fetch_video_works(author_id, cursor)
    if feed == "dynamic":
        return await fetch_dynamic_works(author_id, cursor)
    return []


def _feeds_for_platform(platform: Platform) -> tuple[str, ...]:
    return ("video",) if platform == "douyin" else ("video", "dynamic")


def _cursor_for_all_targets(author: AuthorSubscription, feed: str) -> str | None:
    """返回作者当前 feed 的最早游标，用于有限追赶分页。"""

    cursors: list[str] = []
    for target in author.targets.values():
        cursor = target.cursors.get(feed)
        if cursor:
            cursors.append(cursor)
    return cursors[0] if cursors else None


async def _send_work(url: str, platform: str, target: Target) -> None:
    """使用对应平台 parser 解析并发送内容。"""

    parser = (
        IFollowBilibiliParser() if platform == "bilibili" else DouyinSubscribeParser()
    )
    keyword, searched = parser.search_url(url)
    result = await parser.parse(keyword, searched)
    renderer = get_renderer(result.platform.name)(result)
    messages = [message async for message in renderer.render_messages()]
    for message in messages:
        await message.send(target=target)


async def _notify_subscribers(target: Target, subscriber_ids: list[str]) -> None:
    """提醒订阅人有新内容。"""

    if not subscriber_ids:
        return
    message = UniMessage()
    for subscriber_id in subscriber_ids:
        message += UniMessage.at(subscriber_id)
    message += " 你订阅的爱播有新内容了"
    try:
        await message.send(target=target)
    except Exception:
        logger.opt(exception=True).warning("爱播订阅人提醒发送失败")


def _record_author_failure(
    author_key: str, *, retryable: bool = True
) -> tuple[int, float]:
    state = author_poll_states.setdefault(author_key, AuthorPollState())
    state.failure_count += 1
    if retryable:
        delay = _exponential_backoff_seconds(
            state.failure_count,
            base_seconds=POLL_BACKOFF_BASE_SECONDS,
            max_seconds=POLL_BACKOFF_MAX_SECONDS,
        )
    else:
        delay = max(config.ifollow_subscribe_interval, POLL_BACKOFF_MAX_SECONDS)
    state.retry_at = time.monotonic() + delay
    return state.failure_count, delay


def _exponential_backoff_seconds(
    attempt: int, *, base_seconds: float, max_seconds: float
) -> float:
    return min(base_seconds * (2 ** max(attempt - 1, 0)), max_seconds)


def _target_key(target: Target) -> str:
    return make_target_key(target.dump())


def _platform_name(platform: str) -> str:
    return "抖音" if platform == "douyin" else "B站"


def _format_handle(author: AuthorSubscription) -> str:
    if author.author_handle:
        return (
            f"@{author.author_handle}"
            if author.platform == "douyin"
            else f"UID:{author.author_handle}"
        )
    return "未知"


def _format_update_time(timestamp: int) -> str:
    if timestamp <= 0:
        return "未知"
    return datetime.fromtimestamp(timestamp).strftime("%m月%d日 %H:%M")


async def _get_replied_message_text(event: Event, bot: Bot) -> str:
    reply = getattr(event, "reply", None)
    if reply is not None:
        reply_text = _extract_message_text(getattr(reply, "message", None))
        if reply_text:
            return reply_text
    reply_id = _extract_reply_id(event.get_message())
    if reply_id is None:
        return ""
    try:
        result = await bot.call_api("get_msg", message_id=reply_id)
    except Exception as exc:
        logger.warning(f"读取引用消息 {reply_id} 失败: {exc}")
        return ""
    if not isinstance(result, dict):
        return ""
    return _extract_message_text(result.get("message"))


def _extract_reply_id(message: Message) -> str | None:
    for segment in message:
        if getattr(segment, "type", None) != "reply":
            continue
        data = getattr(segment, "data", {})
        if isinstance(data, dict):
            message_id = data.get("id") or data.get("message_id")
            if message_id is not None:
                return str(message_id)
    return None


def _extract_message_text(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, (list, tuple)):
        parts: list[str] = []
        for segment in message:
            if isinstance(segment, dict):
                data = segment.get("data")
                if isinstance(data, dict) and isinstance(data.get("text"), str):
                    parts.append(data["text"])
            else:
                data = getattr(segment, "data", {})
                if isinstance(data, dict) and isinstance(data.get("text"), str):
                    parts.append(data["text"])
        return "".join(parts)
    return ""
