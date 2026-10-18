"""爱播命令：帮助、订阅、取消订阅、列表与状态。"""

from __future__ import annotations

from typing import Any

from nonebot import get_driver, logger, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg import get_target

from . import formatting, reply
from .adapters import detect
from .app import Runtime, runtime
from .models import AuthorSubscription, Cursor, make_target_key
from .platforms import FeedKind, Platform, PlatformWork
from .status import platform_name

HELP = on_command("爱播帮助", aliases={"帮助爱播"}, priority=5, block=True)
SUBSCRIBE = on_command("爱播订阅", aliases={"订阅爱播"}, priority=5, block=True)
UNSUBSCRIBE = on_command(
    "爱播取消订阅", aliases={"取消爱播订阅", "爱播订阅取消"}, priority=5, block=True
)
LIST = on_command("爱播订阅列表", aliases={"爱播列表"}, priority=5, block=True)
STATUS = on_command("爱播状态", priority=5, block=True)


@HELP.handle()
async def help_command(event: Event, bot: Bot) -> None:
    """显示爱播命令帮助。"""

    await UniMessage(formatting.HELP_MESSAGE).send(
        target=get_target(event, bot), bot=bot
    )


@SUBSCRIBE.handle()
async def subscribe(event: Event, bot: Bot, args: Message = CommandArg()) -> None:
    """订阅一个抖音或 B 站作者。"""

    context = runtime()
    value = args.extract_plain_text().strip() or await reply.replied_text(event, bot)
    if not value:
        await UniMessage(
            "用法：爱播订阅 <作者主页或作品链接>，也可引用含链接的消息后发送。"
        ).finish()
    target = get_target(event, bot)
    target_key = make_target_key(target.dump())
    subscriber_id = event.get_user_id()
    try:
        adapter = detect(value)
    except ValueError as exc:
        report = formatting.describe_failure(exc)
        logger.warning(f"爱播订阅解析失败 code={report.code} value={value!r}")
        await UniMessage(formatting.render_failure("订阅", report)).finish()
    try:
        resolved = await adapter.resolve(context.settings, value)
    except Exception as exc:
        report = formatting.describe_failure(exc, platform=adapter.platform)
        logger.opt(exception=True).warning(
            f"爱播订阅解析失败 code={report.code} value={value!r}"
        )
        await UniMessage(formatting.render_failure("订阅", report)).finish()

    author = resolved.author
    authors = await context.store.snapshot()
    author_key = f"{adapter.platform}:{author.author_id}"
    existing = authors.get(author_key)
    if existing and target_key in existing.targets:
        async with context.store.transaction():
            joined = await context.store.add_subscriber(
                adapter.platform, author.author_id, target_key, subscriber_id
            )
        label = formatting.author_label(adapter.platform, existing.author_name)
        if joined:
            await UniMessage(f"已为 {label} 开启更新提醒。").send(
                target=target, bot=bot
            )
            return
        await UniMessage(f"本会话已订阅 {label}，无需重复。").send(
            target=target, bot=bot
        )
        return

    cursors = {
        feed: Cursor(works[0].work_id, works[0].create_time)
        for feed, works in resolved.feeds.items()
        if works
    }
    latest = _latest_work(resolved.feeds)
    async with context.store.transaction():
        added = await context.store.add_target(
            adapter.platform,
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
            added = await context.store.add_subscriber(
                adapter.platform, author.author_id, target_key, subscriber_id
            )
    label = formatting.author_label(adapter.platform, author.name)
    if not added:
        await UniMessage(f"本会话已订阅 {label}，无需重复。").send(
            target=target, bot=bot
        )
        return
    await UniMessage(
        f"已订阅 {label}\n"
        "首次订阅不补发历史内容，有新作品会自动发送。\n"
        "发送 爱播订阅列表 查看当前订阅。"
    ).send(target=target, bot=bot)


def can_manage(event: Event) -> bool:
    """判断发言人是否有管理权限。

    机器人超级用户在任何会话都有；群里则看发送者的角色（群主 / 管理员）。
    普通群员只能动自己的订阅。

    Args:
        event: 消息事件。

    Returns:
        有管理权限时返回 True。
    """

    if event.get_user_id() in get_driver().config.superusers:
        return True
    sender = getattr(event, "sender", None)
    return getattr(sender, "role", None) in ("owner", "admin")


_NOTICE_SUFFIX = " 发布了新作品"


def _text_of(msg: Any) -> str:
    """把 ``Message`` / ``str`` 统一成纯文本。"""

    if msg is None:
        return ""
    if isinstance(msg, str):
        return msg
    extract = getattr(msg, "extract_plain_text", None)
    return extract() if callable(extract) else str(msg)


async def _reply_text(reply: Any, bot: Bot) -> str:
    """取出被引用消息的纯文本。

    alconna 把 ``event.reply`` 包成 uniseg 的 ``Reply``，OneBot 的 reply 段里
    只有消息 ID，正文要回查 ``get_msg``。

    Args:
        reply: 被引用的消息。
        bot: 当前 bot。

    Returns:
        纯文本；回查失败时返回空串并记一条 warning。
    """

    message_id = getattr(reply, "id", None)
    if not message_id:
        return ""
    try:
        fetched = await bot.call_api("get_msg", message_id=int(message_id))
    except Exception as exc:  # 协议端不支持或消息已过期
        logger.warning(f"回查被引用消息失败：{message_id} {type(exc).__name__}: {exc}")
        return ""
    return _text_of(fetched.get("message") or fetched.get("raw_message"))


async def quoted_author(reply: Any, bot: Bot) -> tuple[str, str] | None:
    """从引用的爱播通知里认出作者。

    通知首行形如 ``抖音 · 作者 发布了新作品``；逐行找这个后缀，所以正文里
    出现同样的字样也不会认错。

    Args:
        reply: 被引用的消息。
        bot: 当前 bot，用于回查被引用的消息。

    Returns:
        ``(平台名, 作者名)``；平台名可能是空串（通知没带平台）。认不出时返回
        ``None``。
    """

    text = await _reply_text(reply, bot)
    for line in text.splitlines():
        line = line.strip()
        if not line.endswith(_NOTICE_SUFFIX):
            continue
        head = line[: -len(_NOTICE_SUFFIX)].strip()
        platform_label, sep, name = head.partition(" · ")
        if sep and name:
            return platform_label, name
        if head:
            return "", head
    return None


@UNSUBSCRIBE.handle()
async def unsubscribe(event: Event, bot: Bot, args: Message = CommandArg()) -> None:
    """取消当前会话的订阅。

    不带参数时按引用的爱播通知认作者。

    权限：订阅人本人、群主 / 群管理员、机器人超级用户。
    管理员取消会直接删掉整条订阅（不管里面有几个订阅人）。
    """

    context = runtime()
    value = args.extract_plain_text().strip()
    quoted = (
        await quoted_author(getattr(event, "reply", None), bot) if not value else None
    )
    if not value and quoted is None:
        await UniMessage(
            "用法：爱播取消订阅 <序号、作者 ID、昵称或链接>\n"
            "也可以引用一条爱播通知后直接发 爱播取消订阅。"
        ).finish()
    target = get_target(event, bot)
    target_key = make_target_key(target.dump())
    authors = await context.store.snapshot()
    items = formatting.target_subscriptions(authors, target_key)
    if not items:
        await UniMessage(
            "当前会话没有爱播订阅。\n发送 爱播订阅 <作者主页链接> 开始订阅。"
        ).finish()
    if quoted is not None:
        platform_label, name = quoted
        matched = [
            author
            for author in items
            if author.author_name == name
            and (not platform_label or platform_name(author.platform) == platform_label)
        ]
        if len(matched) != 1:
            await UniMessage(
                f"引用的通知里没找到「{name}」的订阅，请直接发 爱播取消订阅 <序号>。"
            ).finish()
        platform, author_id, author_name = (
            matched[0].platform,
            matched[0].author_id,
            matched[0].author_name,
        )
    else:
        platform, author_id, author_name = await _resolve_target(context, items, value)
    label = formatting.author_label(platform, author_name)
    force = can_manage(event)
    async with context.store.transaction():
        removed, target_removed = await context.store.remove_subscriber(
            platform, author_id, target_key, event.get_user_id(), force=force
        )
    if removed and target_removed:
        await UniMessage(f"已取消 {label} 的订阅。").send(target=target, bot=bot)
    elif removed:
        await UniMessage(f"已取消你在 {label} 的提醒，其他订阅人仍会收到通知。").send(
            target=target, bot=bot
        )
    else:
        await UniMessage(
            f"「{label}」不是你订阅的，只有订阅人、群管理或机器人管理员可以取消。"
        ).send(target=target, bot=bot)


@LIST.handle()
async def list_subscriptions(event: Event, bot: Bot) -> None:
    """列出当前会话的爱播订阅。"""

    context = runtime()
    target = get_target(event, bot)
    authors = await context.store.snapshot()
    text = formatting.subscription_list(authors, make_target_key(target.dump()))
    await UniMessage(text).send(target=target, bot=bot)


@STATUS.handle()
async def status_command(event: Event, bot: Bot) -> None:
    """显示轮询健康度，仅超级用户可用。"""

    if event.get_user_id() not in get_driver().config.superusers:
        await UniMessage("仅超级用户可以使用 爱播状态。").finish()
    context = runtime()
    authors = await context.store.snapshot()
    await UniMessage(context.poller.status_text(authors)).send(
        target=get_target(event, bot), bot=bot
    )


async def _resolve_target(
    context: Runtime, items: list[AuthorSubscription], value: str
) -> tuple[Platform, str, str]:
    """把用户输入解析成（平台, 作者 ID, 昵称）：先本地匹配，再联网。"""

    if value.isdecimal():
        index = int(value)
        if not 1 <= index <= len(items):
            await UniMessage(
                f"序号无效：当前共 {len(items)} 个订阅，请输入 1-{len(items)}。"
            ).finish()
        selected = items[index - 1]
        return selected.platform, selected.author_id, selected.author_name
    matched = match_subscription(items, value)
    if matched is not None:
        return matched.platform, matched.author_id, matched.author_name
    try:
        adapter = detect(value)
    except ValueError as exc:
        report = formatting.describe_failure(exc)
        logger.warning(f"爱播取消失败 code={report.code} value={value!r}")
        await UniMessage(formatting.render_failure("取消", report)).finish()
    try:
        resolved = await adapter.resolve(context.settings, value)
    except Exception as exc:
        report = formatting.describe_failure(exc, platform=adapter.platform)
        logger.opt(exception=True).warning(
            f"爱播取消失败 code={report.code} value={value!r}"
        )
        await UniMessage(formatting.render_failure("取消", report)).finish()
    author = resolved.author
    return adapter.platform, author.author_id, author.name


def match_subscription(
    items: list[AuthorSubscription], value: str
) -> AuthorSubscription | None:
    """按作者 ID、handle、链接里的 ID 或唯一昵称匹配本地订阅。

    命中时不需要联网，作者已注销或接口被风控也能取消订阅。

    Args:
        items: 当前会话的订阅。
        value: 用户输入。

    Returns:
        唯一命中的订阅；没命中或昵称重名时返回 ``None``。
    """

    from .bilibili_platform import extract_bilibili_mid
    from .douyin_platform import extract_sec_user_id

    key = value.strip()
    for author in items:
        if key in (author.author_id, author.author_handle):
            return author
    for extractor in (extract_sec_user_id, extract_bilibili_mid):
        if candidate := extractor(key):
            for author in items:
                if candidate in (author.author_id, author.author_handle):
                    return author
    matched = [author for author in items if author.author_name == key]
    return matched[0] if len(matched) == 1 else None


def _latest_work(
    feeds: dict[FeedKind, list[PlatformWork]],
) -> PlatformWork | None:
    return max(
        (work for works in feeds.values() for work in works),
        key=lambda work: work.order,
        default=None,
    )
