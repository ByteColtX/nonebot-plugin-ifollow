"""爱播命令：帮助、订阅、取消订阅、列表与状态。"""

from __future__ import annotations

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
        await UniMessage("用法：爱播订阅 <抖音或 B 站作者主页链接、作品链接>").finish()
    target = get_target(event, bot)
    target_key = make_target_key(target.dump())
    subscriber_id = event.get_user_id()
    try:
        adapter = detect(value)
        resolved = await adapter.resolve(context.settings, value)
    except Exception as exc:
        logger.opt(exception=True).warning("爱播订阅解析失败")
        await UniMessage(f"订阅失败：{exc}").finish()

    author = resolved.author
    authors = await context.store.snapshot()
    author_key = f"{adapter.platform}:{author.author_id}"
    existing = authors.get(author_key)
    if existing and target_key in existing.targets:
        async with context.store.transaction():
            joined = await context.store.add_subscriber(
                adapter.platform, author.author_id, target_key, subscriber_id
            )
        if joined:
            await UniMessage(
                f"已加入{platform_name(adapter.platform)}作者 "
                f"{existing.author_name} 的更新提醒。"
            ).send(target=target, bot=bot)
            return
        await UniMessage(
            f"当前会话已订阅{platform_name(adapter.platform)}作者 "
            f"{existing.author_name}，无需重复订阅。"
        ).send(target=target, bot=bot)
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
    if not added:
        await UniMessage("当前会话已订阅该作者，无需重复订阅。").send(
            target=target, bot=bot
        )
        return
    await UniMessage(
        f"已订阅{platform_name(adapter.platform)}作者 {author.name}。\n"
        "首次订阅不会补发历史内容，有新作品或动态时会自动发送。"
    ).send(target=target, bot=bot)


@UNSUBSCRIBE.handle()
async def unsubscribe(event: Event, bot: Bot, args: Message = CommandArg()) -> None:
    """取消当前会话中的作者订阅。"""

    context = runtime()
    value = args.extract_plain_text().strip()
    if not value:
        await UniMessage("用法：爱播取消订阅 <序号、作者 ID、昵称或主页链接>").finish()
    target = get_target(event, bot)
    target_key = make_target_key(target.dump())
    authors = await context.store.snapshot()
    items = formatting.target_subscriptions(authors, target_key)
    if not items:
        await UniMessage("当前会话没有爱播订阅。").finish()
    platform, author_id = await _resolve_target(context, items, value)
    async with context.store.transaction():
        removed, target_removed = await context.store.remove_subscriber(
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
) -> tuple[Platform, str]:
    """把用户输入解析成（平台, 作者 ID）：先本地匹配，再联网。"""

    if value.isdecimal():
        index = int(value)
        if not 1 <= index <= len(items):
            await UniMessage(f"序号无效，请输入 1 到 {len(items)}。").finish()
        selected = items[index - 1]
        return selected.platform, selected.author_id
    matched = match_subscription(items, value)
    if matched is not None:
        return matched.platform, matched.author_id
    try:
        adapter = detect(value)
        resolved = await adapter.resolve(context.settings, value)
    except Exception as exc:
        await UniMessage(f"取消失败：{exc}").finish()
    return adapter.platform, resolved.author.author_id


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
