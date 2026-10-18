"""爱播统一命令和启动装配；轮询逻辑在 :mod:`polling`。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nonebot import get_driver, logger, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg import Target, get_target

from .bilibili_platform import bilibili_dynamics_enabled, warm_up_bilibili
from .models import (
    AuthorSubscription,
    Cursor,
    SubscriptionStore,
    make_target_key,
)
from .polling import (
    fetch_author_feeds,
    platform_name,
    resolve_author,
    start,
    status_text,
    stop,
)

store = SubscriptionStore()
driver = get_driver()
HELP_MESSAGE = (
    "爱播帮助：\n"
    "爱播订阅 <抖音或 B 站作者主页/作品链接>\n"
    "爱播订阅列表（别名：爱播列表）\n"
    "爱播取消订阅 <序号或作者主页/作品链接>\n"
    "爱播状态（超级用户，查看轮询健康度）\n"
    "也可引用包含链接的消息后发送 爱播订阅。\n"
    "支持抖音和 B 站；首次订阅不会补发历史内容。"
)


@driver.on_startup
async def load_subscriptions_on_startup() -> None:
    """加载订阅、输出统计信息并启动轮询。"""

    await store.load()
    authors = await store.snapshot()
    target_count = sum(len(author.targets) for author in authors.values())
    logger.info(f"成功加载 {len(authors)} 个爱播作者订阅，覆盖 {target_count} 个会话。")
    start(store)


@driver.on_shutdown
async def stop_polling_on_shutdown() -> None:
    """停止轮询协程。"""

    await stop()


@driver.on_startup
async def warm_up_bilibili_on_startup() -> None:
    """订阅含 B 站作者时预热匿名指纹，未配置 Cookie 时跳过动态。"""

    authors = await store.snapshot()
    if not any(author.platform == "bilibili" for author in authors.values()):
        return
    if not bilibili_dynamics_enabled():
        logger.info("未配置 B 站 Cookie，动态轮询已跳过，仅推送视频")
        return
    await warm_up_bilibili()


@on_command("爱播帮助", aliases={"帮助爱播"}, priority=5, block=True).handle()
async def help_command(event: Event, bot: Bot) -> None:
    """显示爱播命令帮助。"""

    await UniMessage(HELP_MESSAGE).send(target=get_target(event, bot), bot=bot)


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
        platform, author = await resolve_author(value)
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
                f"已加入{platform_name(platform)}作者 "
                f"{existing_author.author_name} 的更新提醒。"
            ).send(target=target, bot=bot)
            return
        await UniMessage(
            f"当前会话已订阅{platform_name(platform)}作者 "
            f"{existing_author.author_name}，无需重复订阅。"
        ).send(target=target, bot=bot)
        return

    try:
        feeds = await fetch_author_feeds(platform, author.author_id)
    except Exception as exc:
        logger.opt(exception=True).warning("爱播订阅获取作品失败")
        await UniMessage(f"订阅失败：{exc}").finish()

    cursors: dict[str, Cursor] = {
        feed: Cursor(works[0].work_id, works[0].create_time)
        for feed, works in feeds.items()
        if works
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
            await UniMessage(f"已加入{platform_name(platform)}作者更新提醒。").send(
                target=target, bot=bot
            )
            return
        await UniMessage("当前会话已订阅该作者，无需重复订阅。").send(
            target=target, bot=bot
        )
        return
    await UniMessage(
        f"已订阅{platform_name(platform)}作者 {author.name}。\n"
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
        await UniMessage("用法：爱播取消订阅 <序号、作者主页链接或作品链接>").finish()
    target = get_target(event, bot)
    target_key = _target_key(target)
    if value.isdecimal():
        authors = await store.snapshot()
        items = _target_subscriptions(authors, target_key)
        if not items:
            await UniMessage("当前会话没有爱播订阅。").finish()
        index = int(value)
        if not 1 <= index <= len(items):
            await UniMessage(f"序号无效，请输入 1 到 {len(items)}。").finish()
        selected = items[index - 1]
        platform = selected.platform
        author_id = selected.author_id
    else:
        try:
            platform, author = await resolve_author(value)
        except Exception as exc:
            await UniMessage(f"取消失败：{exc}").finish()
        author_id = author.author_id

    removed, target_removed = await store.remove_subscriber(
        platform, author_id, target_key, event.get_user_id()
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
    items = _target_subscriptions(authors, target_key)
    if not items:
        message = "当前会话没有爱播订阅。"
    else:
        lines: list[str] = []
        for index, author in enumerate(items, 1):
            handle = _format_handle(author)
            updated_at = _format_update_time(author.last_update_time)
            lines.append(
                f"{index}. {platform_name(author.platform)} {author.author_name}"
                f"（{handle}）{updated_at}"
            )
        message = "当前会话的爱播订阅：\n序号 平台 昵称（标识） 最近更新\n" + "\n".join(
            lines
        )
    await UniMessage(message).send(target=target, bot=bot)


@on_command("爱播状态", priority=5, block=True).handle()
async def status_command(event: Event, bot: Bot) -> None:
    """显示轮询健康度，仅超级用户可用。"""

    if event.get_user_id() not in get_driver().config.superusers:
        await UniMessage("仅超级用户可以使用 爱播状态。").finish()
    authors = await store.snapshot()
    await UniMessage(status_text(authors)).send(target=get_target(event, bot), bot=bot)


def _target_key(target: Target) -> str:
    return make_target_key(target.dump())


def _target_subscriptions(
    authors: dict[str, AuthorSubscription], target_key: str
) -> list[AuthorSubscription]:
    """返回当前会话的订阅，顺序与订阅列表命令一致。"""

    return [author for author in authors.values() if target_key in author.targets]


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
