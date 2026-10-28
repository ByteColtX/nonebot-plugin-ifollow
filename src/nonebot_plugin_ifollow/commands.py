"""爱播命令：帮助、订阅、退订、列表、提醒与状态。"""

from __future__ import annotations

import re
from typing import Any

from nonebot import get_driver, logger, on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.params import CommandArg
from nonebot_plugin_alconna import UniMessage
from nonebot_plugin_alconna.uniseg import Target, get_target

from . import formatting, reply
from .adapters import detect
from .app import Runtime, runtime
from .models import AuthorSubscription, Cursor, make_target_key
from .platforms import FeedKind, Platform, PlatformWork
from .status import platform_name
from .style import (
    HELP_COMMAND,
    LIST_COMMAND,
    NOTIFY_COMMAND,
    STATUS_COMMAND,
    SUBSCRIBE_COMMAND,
    UNSUBSCRIBE_COMMAND,
    command_text,
)

HELP = on_command(HELP_COMMAND, priority=5, block=True)
SUBSCRIBE = on_command(SUBSCRIBE_COMMAND, priority=5, block=True)
UNSUBSCRIBE = on_command(UNSUBSCRIBE_COMMAND, priority=5, block=True)
LIST = on_command(LIST_COMMAND, priority=5, block=True)
NOTIFY = on_command(NOTIFY_COMMAND, priority=5, block=True)
STATUS = on_command(STATUS_COMMAND, priority=5, block=True)


LINE_LIMIT = 12
"""订阅列表超过这个行数就改用合并转发。"""


def _needs_forward(text: str) -> bool:
    """订阅列表的行数是否超过上限。"""

    return len(text.rstrip("\n").splitlines()) > LINE_LIMIT


async def _send_forward(bot: Bot, target: Any, text: str) -> bool:
    """把整段列表发成一条合并转发；协议端不支持时返回 False。"""

    private = bool(getattr(target, "private", False))
    api = "send_private_forward_msg" if private else "send_group_forward_msg"
    key = "user_id" if private else "group_id"
    nickname = get_driver().config.nickname
    name = next(iter(nickname), None) if nickname else None
    node = {
        "type": "node",
        "data": {
            "name": str(name or LIST_COMMAND),
            "uin": int(bot.self_id),
            "content": [{"type": "text", "data": {"text": text}}],
        },
    }
    try:
        await bot.call_api(api, messages=[node], **{key: int(target.id)})
    except Exception as exc:  # 转发不可用时退回文本
        logger.debug(f"合并转发失败，改发文本：{exc}")
        return False
    return True


@HELP.handle()
async def help_command(event: Event, bot: Bot) -> None:
    """显示爱播命令帮助。"""

    await UniMessage(formatting.help_message()).send(
        target=get_target(event, bot), bot=bot
    )


@SUBSCRIBE.handle()
async def subscribe(event: Event, bot: Bot, args: Message = CommandArg()) -> None:
    """订阅一个抖音或 B 站作者。"""

    context = runtime()
    value = args.extract_plain_text().strip() or await reply.replied_text(event, bot)
    if not value:
        usage = command_text(SUBSCRIBE_COMMAND, "<作者主页、作品链接或作者 ID>")
        await UniMessage(f"用法：{usage}\n也可以引用带链接的消息再发这条命令").finish()
    target = get_target(event, bot)
    target_key = make_target_key(target.dump())
    subscriber_id = event.get_user_id()
    usage = command_text(SUBSCRIBE_COMMAND, "<作者主页、作品链接或作者 ID>")
    try:
        adapter = detect(value)
    except ValueError as exc:
        report = formatting.describe_failure(exc, value=value, usage=usage)
        logger.warning(f"{SUBSCRIBE_COMMAND}解析失败 value={value!r}：{exc}")
        await UniMessage(formatting.render_failure("订阅", report)).finish()
    try:
        resolved = await adapter.resolve(context.settings, value)
    except Exception as exc:
        report = formatting.describe_failure(exc, value=value, usage=usage)
        logger.opt(exception=True).warning(
            f"{SUBSCRIBE_COMMAND}解析失败 value={value!r}：{exc}"
        )
        await UniMessage(formatting.render_failure("订阅", report)).finish()

    author = resolved.author
    authors = await context.store.snapshot()
    author_key = f"{adapter.platform}:{author.author_id}"
    existing = authors.get(author_key)
    if existing and target_key in existing.targets:
        async with context.store.transaction():
            await context.store.update_author_metadata(
                adapter.platform,
                author.author_id,
                author.name,
                author.handle,
                existing.last_update_time,
            )
            joined = await context.store.add_subscriber(
                adapter.platform, author.author_id, target_key, subscriber_id
            )
        label = formatting.author_label(
            adapter.platform, author.name or existing.author_name
        )
        if joined:
            await UniMessage(f"你已经订阅了 {label}，已把你加为订阅人").send(
                target=target, bot=bot
            )
            return
        await UniMessage(f"你已经订阅了 {label}").send(target=target, bot=bot)
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
        await UniMessage(f"你已经订阅了 {label}").send(target=target, bot=bot)
        return
    await UniMessage(
        f"已订阅 {label}\n"
        "有新作品会自动推给你\n"
        f"发送 {command_text(LIST_COMMAND)} 查看全部订阅"
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


_NOTICE_SUFFIXES = (" 更新了", " 发布了新作品")
"""通知首行后缀；旧卡片仍按旧后缀认，历史消息还能引用退订。"""


def _reply_text(reply: Any) -> str:
    """取出被引用消息的纯文本。

    OneBot v11 的 ``event.reply`` 是 nonebot 自己的 ``Reply``，被引用消息的
    正文就在 ``message`` 字段里，不需要回查 ``get_msg``。

    Args:
        reply: 被引用的消息。

    Returns:
        纯文本；没有引用时返回空串。
    """

    if reply is None:
        return ""
    return reply.message.extract_plain_text()


def quoted_author(reply: Any) -> tuple[str, str] | None:
    """从引用的爱播通知里认出作者。

    通知首行形如 ``抖音 · 作者 更新了``；逐行找后缀，所以正文里出现同样的字样
    也不会认错。旧卡片的 ``发布了新作品`` 仍然认。

    Args:
        reply: 被引用的消息。

    Returns:
        ``(平台名, 作者名)``；平台名可能是空串（通知没带平台）。认不出时返回
        ``None``。
    """

    text = _reply_text(reply)
    for line in text.splitlines():
        line = line.strip()
        suffix = next((item for item in _NOTICE_SUFFIXES if line.endswith(item)), None)
        if suffix is None:
            continue
        head = line[: -len(suffix)].strip()
        if not head:
            continue
        platform_label, sep, name = head.partition(" · ")
        return (platform_label, name) if sep and name else ("", head)
    return None


_INDEX_SEPARATORS = re.compile(r"[\s,，、]+")


def parse_indexes(value: str) -> list[int] | None:
    """解析「1 3」「1,2」「1、2」这类序号列表。

    Args:
        value: 用户输入。

    Returns:
        去重后的序号；不是纯序号时返回 ``None``。
    """

    tokens = [token for token in _INDEX_SEPARATORS.split(value.strip()) if token]
    if not tokens or not all(token.isdecimal() for token in tokens):
        return None
    indexes: list[int] = []
    for token in tokens:
        index = int(token)
        if index not in indexes:
            indexes.append(index)
    return indexes


def _invalid_index_message(indexes: list[int], total: int) -> str:
    """生成越界序号的提示，支持一次列出多个。"""

    joined = "、".join(str(index) for index in indexes)
    hint = "只能输入 1" if total == 1 else f"输入 1-{total}"
    return f"没有第 {joined} 个订阅；当前共 {total} 个，{hint}"


def _batch_unsubscribe_text(
    whole: list[AuthorSubscription],
    partial: list[AuthorSubscription],
    denied: list[AuthorSubscription],
) -> str:
    """拼出批量退订结果：按结果分组，每组先计数再列作者。"""

    def labels(authors: list[AuthorSubscription]) -> list[str]:
        return [
            formatting.author_label(author.platform, author.author_name)
            for author in authors
        ]

    sections: list[list[str]] = []
    if whole:
        sections.append([f"已退订 {len(whole)} 个订阅", *labels(whole)])
    if partial:
        sections.append(
            [
                f"已关闭 {len(partial)} 个订阅的 @ 提醒，其他订阅人仍会收到推送",
                *labels(partial),
            ]
        )
    if denied:
        sections.append(
            [
                f"有 {len(denied)} 个不是你订阅的；只有订阅人、群主、群管理和"
                "机器人超级用户能退订",
                *labels(denied),
            ]
        )
    return "\n\n".join("\n".join(section) for section in sections)


async def _unsubscribe_many(
    context: Runtime,
    items: list[AuthorSubscription],
    indexes: list[int],
    event: Event,
    target: Target,
    bot: Bot,
) -> None:
    """按序号批量退订，结果合并成一条消息。"""

    invalid = [index for index in indexes if not 1 <= index <= len(items)]
    if invalid:
        await UniMessage(_invalid_index_message(invalid, len(items))).finish()
    target_key = make_target_key(target.dump())
    user_id = event.get_user_id()
    force = can_manage(event)
    whole: list[AuthorSubscription] = []
    partial: list[AuthorSubscription] = []
    denied: list[AuthorSubscription] = []
    async with context.store.transaction():
        for index in indexes:
            author = items[index - 1]
            removed, target_removed = await context.store.remove_subscriber(
                author.platform,
                author.author_id,
                target_key,
                user_id,
                force=force,
            )
            if removed and target_removed:
                whole.append(author)
            elif removed:
                partial.append(author)
            else:
                denied.append(author)
    await UniMessage(_batch_unsubscribe_text(whole, partial, denied)).send(
        target=target, bot=bot
    )


@UNSUBSCRIBE.handle()
async def unsubscribe(event: Event, bot: Bot, args: Message = CommandArg()) -> None:
    """退订当前会话的订阅。

    不带参数时按引用的爱播通知认作者。

    权限：订阅人本人、群主 / 群管理员、机器人超级用户。
    管理员退订会直接删掉整条订阅（不管里面有几个订阅人）。
    """

    context = runtime()
    value = args.extract_plain_text().strip()
    quoted = quoted_author(getattr(event, "reply", None)) if not value else None
    if not value and quoted is None:
        usage = command_text(UNSUBSCRIBE_COMMAND, "<序号、作者 ID、昵称或链接>")
        await UniMessage(
            f"用法：{usage}\n"
            "序号可以一次写多个，例如 1 3 5\n"
            f"也可以引用一条推送再发 {command_text(UNSUBSCRIBE_COMMAND)}"
        ).finish()
    target = get_target(event, bot)
    target_key = make_target_key(target.dump())
    authors = await context.store.snapshot()
    items = formatting.target_subscriptions(authors, target_key)
    if not items:
        await UniMessage(formatting.empty_subscription_message()).finish()
    if quoted is not None:
        platform_label, name = quoted
        matched = [
            author
            for author in items
            if author.author_name == name
            and (not platform_label or platform_name(author.platform) == platform_label)
        ]
        if len(matched) != 1:
            await UniMessage(f"{name} 不在本会话的订阅列表里，可能已经退订过").finish()
        platform, author_id, author_name = (
            matched[0].platform,
            matched[0].author_id,
            matched[0].author_name,
        )
    else:
        indexes = parse_indexes(value)
        if indexes is not None:
            if len(indexes) > 1:
                await _unsubscribe_many(context, items, indexes, event, target, bot)
                return
            value = str(indexes[0])
        platform, author_id, author_name = await _resolve_target(context, items, value)
    label = formatting.author_label(platform, author_name)
    force = can_manage(event)
    async with context.store.transaction():
        removed, target_removed = await context.store.remove_subscriber(
            platform, author_id, target_key, event.get_user_id(), force=force
        )
    if removed and target_removed:
        await UniMessage(f"已退订 {label}").send(target=target, bot=bot)
    elif removed:
        await UniMessage(f"已关闭你在 {label} 的 @ 提醒；其他订阅人仍会收到推送").send(
            target=target, bot=bot
        )
    else:
        await UniMessage(
            f"你没有订阅 {label}；只有订阅人、群主、群管理和机器人超级用户能退订"
        ).send(target=target, bot=bot)


_SWITCH_VALUES: dict[str, bool] = {
    "开": True,
    "开启": True,
    "启用": True,
    "on": True,
    "true": True,
    "关": False,
    "关闭": False,
    "停用": False,
    "off": False,
    "false": False,
}


def parse_switch(value: str) -> bool | None:
    """把「开 / 关」解析成布尔值；无法识别时返回 ``None``。"""

    return _SWITCH_VALUES.get(value.strip().lower())


def _notify_status(items: list[AuthorSubscription], target_key: str) -> str:
    """生成提醒开关的当前状态。"""

    total = len(items)
    off = sum(1 for author in items if not author.targets[target_key].mention)
    if off == 0:
        state = f"@ 提醒已开启（本会话 {total} 个订阅）"
    elif off == total:
        state = f"@ 提醒已关闭（本会话 {total} 个订阅）"
    else:
        state = f"@ 提醒有 {off}/{total} 个订阅已关闭"
    return f"{state}\n修改：{command_text(NOTIFY_COMMAND, '开|关')}"


@NOTIFY.handle()
async def notify_command(event: Event, bot: Bot, args: Message = CommandArg()) -> None:
    """开关当前会话新作品通知里的 @ 提醒。

    群聊仅群主、群管理员和机器人超级用户可设置；私聊由订阅人自己设置。
    不带参数时查看当前状态。
    """

    context = runtime()
    raw = args.extract_plain_text().strip()
    target = get_target(event, bot)
    target_key = make_target_key(target.dump())
    authors = await context.store.snapshot()
    items = formatting.target_subscriptions(authors, target_key)
    if not items:
        await UniMessage(formatting.empty_subscription_message()).finish()
    if not raw:
        await UniMessage(_notify_status(items, target_key)).send(target=target, bot=bot)
        return
    switch = parse_switch(raw)
    if switch is None:
        usage = command_text(NOTIFY_COMMAND, "开|关")
        await UniMessage(f"用法：{usage}").finish()
    user_id = event.get_user_id()
    private = bool(getattr(target, "private", False))
    manage = can_manage(event)
    if private and not manage:
        subscribed = any(
            user_id in author.targets[target_key].subscriber_ids for author in items
        )
        if not subscribed:
            await UniMessage("只有订阅人能修改 @ 提醒").finish()
    elif not private and not manage:
        await UniMessage("只有群主、群管理和机器人超级用户能修改 @ 提醒").finish()
    async with context.store.transaction():
        changed = await context.store.set_mention(target_key, switch)
    if changed:
        if switch:
            text = "已开启 @ 提醒，新作品发布时会 @ 本会话的订阅人"
        else:
            text = "已关闭 @ 提醒，新作品仍会正常推送"
    else:
        text = "@ 提醒本来就开着" if switch else "@ 提醒本来就关着"
    await UniMessage(text).send(target=target, bot=bot)


@LIST.handle()
async def list_subscriptions(event: Event, bot: Bot) -> None:
    """列出当前会话的爱播订阅。"""

    context = runtime()
    target = get_target(event, bot)
    authors = await context.store.snapshot()
    text = formatting.subscription_list(authors, make_target_key(target.dump()))
    if _needs_forward(text) and await _send_forward(bot, target, text):
        return
    await UniMessage(text).send(target=target, bot=bot)


@STATUS.handle()
async def status_command(event: Event, bot: Bot) -> None:
    """显示轮询健康度，仅超级用户可用。"""

    if event.get_user_id() not in get_driver().config.superusers:
        await UniMessage(f"{command_text(STATUS_COMMAND)} 只对超级用户开放").finish()
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
            await UniMessage(_invalid_index_message([index], len(items))).finish()
        selected = items[index - 1]
        return selected.platform, selected.author_id, selected.author_name
    matched = match_subscription(items, value)
    if matched is not None:
        return matched.platform, matched.author_id, matched.author_name
    usage = command_text(UNSUBSCRIBE_COMMAND, "<序号、作者 ID、昵称或链接>")
    try:
        adapter = detect(value)
    except ValueError as exc:
        report = formatting.describe_failure(exc, value=value, usage=usage)
        logger.warning(f"{UNSUBSCRIBE_COMMAND}失败 value={value!r}：{exc}")
        await UniMessage(formatting.render_failure("退订", report)).finish()
    try:
        resolved = await adapter.resolve(context.settings, value)
    except Exception as exc:
        report = formatting.describe_failure(exc, value=value, usage=usage)
        logger.opt(exception=True).warning(
            f"{UNSUBSCRIBE_COMMAND}失败 value={value!r}：{exc}"
        )
        await UniMessage(formatting.render_failure("退订", report)).finish()
    author = resolved.author
    return adapter.platform, author.author_id, author.name


def match_subscription(
    items: list[AuthorSubscription], value: str
) -> AuthorSubscription | None:
    """按作者 ID、handle、链接里的 ID 或唯一昵称匹配本地订阅。

    命中时不需要联网，作者已注销或接口被风控也能退订。

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
