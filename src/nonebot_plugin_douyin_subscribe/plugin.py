"""NoneBot 事件处理和定时轮询。"""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime
from typing import Any

from nonebot import get_driver, logger, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg import Target, get_target
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_parser.parsers import DouyinParser
from nonebot_plugin_parser.renders import get_renderer

from .config import config
from .douyin import (
    DouyinRequestError,
    DouyinWork,
    fetch_latest_works,
    resolve_sec_user_id,
)
from .models import AuthorSubscription, SubscriptionStore, TargetSubscription

store = SubscriptionStore()
poll_lock = asyncio.Lock()
poll_backoff_until = 0.0
poll_failure_count = 0
send_retry_attempts: dict[tuple[str, str, str], int] = {}
send_retry_until: dict[tuple[str, str, str], float] = {}
POLL_BACKOFF_BASE_SECONDS = 30.0
POLL_BACKOFF_MAX_SECONDS = 300.0
SEND_BACKOFF_BASE_SECONDS = 30.0
SEND_BACKOFF_MAX_SECONDS = 300.0


@get_driver().on_startup
async def load_subscriptions() -> None:
    await store.load()


@on_command("抖音订阅", aliases={"订阅抖音"}, priority=5, block=True).handle()
async def subscribe(event: Event, bot: Bot, args: Message = CommandArg()) -> None:
    """订阅一个抖音作者。"""

    value = args.extract_plain_text().strip()
    if not value:
        value = await _get_replied_message_text(event, bot)
    if not value:
        await UniMessage(
            "用法：抖音订阅 <作者主页链接、作品分享链接或 sec_user_id>"
        ).finish()

    target = get_target(event, bot)
    target_key = _target_key(target)
    subscriber_id = event.get_user_id()
    try:
        sec_user_id = await resolve_sec_user_id(value)
    except Exception as exc:
        logger.opt(exception=True).warning("抖音订阅解析失败")
        await UniMessage(f"订阅失败：{exc}").finish()

    authors = await store.snapshot()
    existing_author = authors.get(sec_user_id)
    if existing_author and target_key in existing_author.targets:
        if await store.add_subscriber(sec_user_id, target_key, subscriber_id):
            await UniMessage(
                f"已加入抖音作者 {existing_author.author_name} 的更新提醒。"
            ).send(target=target, bot=bot)
            return
        await UniMessage(
            f"当前会话已订阅抖音作者 {existing_author.author_name}，无需重复订阅。"
        ).send(target=target, bot=bot)
        return

    try:
        works = await fetch_latest_works(sec_user_id)
    except Exception as exc:
        logger.opt(exception=True).warning("抖音订阅解析失败")
        await UniMessage(f"订阅失败：{exc}").finish()

    author_name = works[0].author_name if works else sec_user_id
    author_unique_id = works[0].author_unique_id if works else ""
    latest_id = works[0].aweme_id if works else None
    latest_update_time = works[0].create_time if works else 0
    added = await store.add_target(
        sec_user_id,
        author_name,
        target_key,
        target.dump(),
        latest_id,
        subscriber_id=subscriber_id,
        author_unique_id=author_unique_id,
        last_update_time=latest_update_time,
    )
    if not added:
        if await store.add_subscriber(sec_user_id, target_key, subscriber_id):
            await UniMessage(f"已加入抖音作者 {author_name} 的更新提醒。").send(
                target=target, bot=bot
            )
            return
        await UniMessage(f"当前会话已订阅抖音作者 {author_name}，无需重复订阅。").send(
            target=target, bot=bot
        )
        return
    message = (
        f"已订阅抖音作者 {author_name}。\n"
        "首次订阅不会补发历史作品，有新作品时会自动发送。"
    )
    await UniMessage(message).send(target=target, bot=bot)


@on_command("抖音取消订阅", aliases={"取消抖音订阅"}, priority=5, block=True).handle()
async def unsubscribe(event: Event, bot: Bot, args: Message = CommandArg()) -> None:
    """取消当前会话中的抖音作者订阅。"""

    value = args.extract_plain_text().strip()
    target = get_target(event, bot)
    if not value:
        await UniMessage("用法：抖音取消订阅 <作者主页链接或 sec_user_id>").finish()
    try:
        sec_user_id = await resolve_sec_user_id(value)
    except (ValueError, DouyinRequestError) as exc:
        await UniMessage(f"取消失败：{exc}").finish()

    removed, target_removed = await store.remove_subscriber(
        sec_user_id, _target_key(target), event.get_user_id()
    )
    if removed and target_removed:
        await UniMessage("已取消订阅。").send(target=target, bot=bot)
    elif removed:
        await UniMessage("已取消你的更新提醒，其他订阅人仍会收到通知。").send(
            target=target, bot=bot
        )
    else:
        await UniMessage("当前会话没有你的这个订阅。").send(target=target, bot=bot)


@on_command("抖音订阅列表", aliases={"订阅列表"}, priority=5, block=True).handle()
async def list_subscriptions(event: Event, bot: Bot) -> None:
    """列出当前会话的抖音订阅。"""

    target = get_target(event, bot)
    target_key = _target_key(target)
    authors = await store.snapshot()
    items = [author for author in authors.values() if target_key in author.targets]
    if not items:
        message = "当前会话没有抖音订阅。"
    else:
        lines: list[str] = []
        for index, author in enumerate(items, 1):
            author_name = author.author_name
            unique_id = author.author_unique_id
            updated_at = _format_update_time(author.last_update_time)
            handle = f"@{unique_id}" if unique_id else "@未知"
            lines.append(f"{index}. {author_name}（{handle}）{updated_at}")
        message = "当前会话的抖音订阅：\n序号 昵称（抖音号） 最近更新\n" + "\n".join(
            lines
        )
    await UniMessage(message).send(target=target, bot=bot)


@scheduler.scheduled_job(
    "interval",
    seconds=config.douyin_subscribe_interval,
    id="nonebot-plugin-douyin-subscribe-poll",
)
async def poll_subscriptions() -> None:
    """轮询作者作品并向订阅目标发送新作品。"""

    global poll_backoff_until, poll_failure_count

    now = time.monotonic()
    if now < poll_backoff_until:
        remaining = math.ceil(poll_backoff_until - now)
        logger.warning(f"抖音接口暂时受限，轮询暂停，预计 {remaining} 秒后重试")
        return
    if poll_lock.locked():
        logger.warning("上一次抖音订阅轮询尚未完成，本次跳过")
        return
    async with poll_lock:
        authors = await store.snapshot()
        request_delay = max(
            1.0, config.douyin_subscribe_interval / max(len(authors), 1)
        )
        for index, author in enumerate(authors.values()):
            try:
                works = await fetch_latest_works(author.sec_user_id)
            except DouyinRequestError as exc:
                logger.warning(f"抖音作者 {author.sec_user_id} 轮询失败：{exc}")
                delay = _record_poll_failure()
                logger.warning(
                    f"抖音轮询第 {poll_failure_count} 次失败，{delay:.0f} 秒后重试"
                )
                break
            except Exception:
                logger.opt(exception=True).warning(
                    f"抖音作者 {author.sec_user_id} 轮询失败"
                )
                delay = _record_poll_failure()
                logger.warning(
                    f"抖音轮询第 {poll_failure_count} 次失败，{delay:.0f} 秒后重试"
                )
                break
            if works:
                latest = works[0]
                await store.update_author_metadata(
                    author.sec_user_id,
                    latest.author_name,
                    latest.author_unique_id,
                    latest.create_time,
                )
                for target_key, target_state in author.targets.items():
                    await _deliver_new_works(author, target_key, target_state, works)
            if index < len(authors) - 1:
                await asyncio.sleep(request_delay)
        else:
            poll_failure_count = 0
            poll_backoff_until = 0.0


def _record_poll_failure() -> float:
    """记录一次轮询失败并返回下一次重试前的等待时间。"""

    global poll_backoff_until, poll_failure_count

    poll_failure_count += 1
    delay = _exponential_backoff_seconds(
        poll_failure_count,
        base_seconds=POLL_BACKOFF_BASE_SECONDS,
        max_seconds=POLL_BACKOFF_MAX_SECONDS,
    )
    poll_backoff_until = time.monotonic() + delay
    return delay


def _exponential_backoff_seconds(
    attempt: int, *, base_seconds: float, max_seconds: float
) -> float:
    """计算指数退避时间，等待时间不会超过上限。"""

    return min(base_seconds * (2 ** max(attempt - 1, 0)), max_seconds)


async def _deliver_new_works(
    author: AuthorSubscription,
    target_key: str,
    target_state: TargetSubscription,
    works: list[DouyinWork],
) -> None:
    target = Target.load(target_state.target.copy())
    if target_state.last_aweme_id is None:
        await store.update_cursor(author.sec_user_id, target_key, works[0].aweme_id)
        return
    unseen: list[DouyinWork] = []
    for work in works:
        if work.aweme_id == target_state.last_aweme_id:
            break
        unseen.append(work)
    for work in reversed(unseen):
        retry_key = (author.sec_user_id, target_key, work.aweme_id)
        retry_until = send_retry_until.get(retry_key, 0.0)
        if time.monotonic() < retry_until:
            return
        try:
            await _send_through_parser(work.share_url, target)
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
                f"向目标 {target.id} 发送抖音作品 {work.aweme_id} "
                f"失败，第 {attempt} 次重试将在 {delay:.0f} 秒后进行"
            )
            break
        send_retry_attempts.pop(retry_key, None)
        send_retry_until.pop(retry_key, None)
        await _notify_subscribers(target, target_state.subscriber_ids)
        await store.update_cursor(author.sec_user_id, target_key, work.aweme_id)


async def _send_through_parser(url: str, target: Target) -> None:
    """把抖音分享链接交给 parser，然后把 parser 的媒体消息发到目标。"""

    parser = DouyinParser()
    keyword, searched = parser.search_url(url)
    result = await parser.parse(keyword, searched)
    renderer = get_renderer(result.platform.name)(result)
    messages = [message async for message in renderer.render_messages()]
    for message in messages:
        await message.send(target=target)


async def _notify_subscribers(target: Target, subscriber_ids: list[str]) -> None:
    """提醒订阅人作者有新作品。"""

    if not subscriber_ids:
        return
    message = UniMessage()
    for subscriber_id in subscriber_ids:
        message += UniMessage.at(subscriber_id)
    message += " 你订阅的爱播更新了"
    try:
        await message.send(target=target)
    except Exception:
        logger.opt(exception=True).warning("抖音订阅人提醒发送失败")


def _target_key(target: Target) -> str:
    """生成不依赖对象地址的订阅目标键。"""

    dumped = target.dump()
    return json.dumps(
        {
            key: dumped.get(key, "")
            for key in (
                "id",
                "parent_id",
                "channel",
                "private",
                "adapter",
                "scope",
                "platforms",
            )
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _format_update_time(timestamp: int) -> str:
    """把作品时间戳格式化为月日时分。"""

    if timestamp <= 0:
        return "未知"
    return datetime.fromtimestamp(timestamp).strftime("%m月%d日 %H:%M")


async def _get_replied_message_text(event: Event, bot: Bot) -> str:
    """读取当前消息引用的原始消息文本。"""

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
    """从适配器消息中提取 reply 消息 ID。"""

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
    """从 OneBot 风格的消息数组或文本中提取文字。"""

    if isinstance(message, str):
        return message.strip()
    if not isinstance(message, list):
        return ""
    parts: list[str] = []
    for segment in message:
        if isinstance(segment, dict):
            segment_type = segment.get("type")
            data = segment.get("data")
        else:
            segment_type = getattr(segment, "type", None)
            data = getattr(segment, "data", None)
        if segment_type != "text":
            continue
        if isinstance(data, dict) and isinstance(data.get("text"), str):
            parts.append(data["text"])
    return "".join(parts).strip()
