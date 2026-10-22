from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, Mock

import pytest
from nonebot.adapters.onebot.v11 import Message
from nonebot.exception import FinishedException
from nonebot_plugin_alconna.uniseg import Target

from nonebot_plugin_ifollow import app, commands
from nonebot_plugin_ifollow.models import (
    AuthorSubscription,
    SubscriptionStore,
    TargetSubscription,
    make_target_key,
)
from nonebot_plugin_ifollow.platforms import (
    AuthorInfo,
    Cursor,
    PlatformWork,
    ResolvedAuthor,
)
from nonebot_plugin_ifollow.poller import Poller
from nonebot_plugin_ifollow.settings import Settings

SETTINGS = Settings(
    interval=1800,
    max_items=5,
    media_max_bytes=0,
    use_base64=False,
)


class FakeUniMessage:
    """记录发送内容的消息替身。"""

    sent: ClassVar[list[str]] = []

    def __init__(self, message: str = "") -> None:
        self.message = message

    async def send(self, *, target: Any = None, bot: Any = None) -> None:
        del target, bot
        FakeUniMessage.sent.append(self.message)

    async def finish(self) -> None:
        await self.send()
        raise FinishedException


def make_work(work_id: str = "w1", timestamp: int = 100) -> PlatformWork:
    return PlatformWork(
        platform="douyin",
        work_id=work_id,
        url=f"https://www.douyin.com/video/{work_id}",
        title=work_id,
        description="",
        create_time=timestamp,
        author=AuthorInfo("sec", "作者", "handle"),
    )


class FakeAdapter:
    platform = "douyin"

    def __init__(
        self,
        works: list[PlatformWork] | None = None,
        author: AuthorInfo | None = None,
    ) -> None:
        self.works = works if works is not None else [make_work()]
        self.author = author or AuthorInfo("sec", "作者", "handle")
        self.resolve_calls = 0

    def matches(self, _value: str) -> bool:
        return True

    def feeds(self, _settings: Settings) -> tuple[str, ...]:
        return ("video",)

    async def resolve(self, _settings: Settings, _value: str) -> ResolvedAuthor:
        self.resolve_calls += 1
        return ResolvedAuthor(self.author, {"video": self.works})

    async def fetch(
        self, _settings: Settings, _author_id: str, _feed: str, _cursor: Any = None
    ) -> list[PlatformWork]:
        return self.works


@pytest.fixture(autouse=True)
def reset_sent() -> None:
    FakeUniMessage.sent = []


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    store = SubscriptionStore(tmp_path)
    poller = Poller(store, SETTINGS)
    app.install(app.Runtime(SETTINGS, store, poller))
    monkeypatch.setattr(commands, "UniMessage", FakeUniMessage)
    monkeypatch.setattr(
        commands, "get_target", lambda _event, _bot: Target.group("group")
    )
    return store, poller


def make_event(user_id: str = "42") -> Mock:
    event = Mock()
    event.get_user_id.return_value = user_id
    event.get_message.return_value = []
    event.reply = None
    return event


def args(value: str) -> Any:
    return SimpleNamespace(extract_plain_text=lambda: value)


def target_key() -> str:
    return make_target_key(Target.group("group").dump())


async def seed_subscription(
    store: SubscriptionStore, *, author_id: str = "sec", name: str = "作者"
) -> str:
    key = target_key()
    await store.add_target(
        "douyin",
        author_id,
        name,
        key,
        Target.group("group").dump(),
        {"video": Cursor("old", 1)},
        subscriber_id="42",
        author_handle="handle",
    )
    return key


class FakeForwardBot:
    """记录合并转发调用；fail=True 时模拟协议端不支持。"""

    def __init__(self, fail: bool = False) -> None:
        self.self_id = "3763502110"
        self.fail = fail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_api(self, api: str, **kwargs: Any) -> None:
        if self.fail:
            raise RuntimeError("api not supported")
        self.calls.append((api, kwargs))


async def seed_many(store: SubscriptionStore, target: Target, count: int) -> None:
    key = make_target_key(target.dump())
    for index in range(count):
        await store.add_target(
            "douyin",
            f"sec{index}",
            f"作者{index}",
            key,
            target.dump(),
            {"video": Cursor("old", 1)},
            subscriber_id="42",
            author_handle="handle",
        )


@pytest.mark.asyncio
async def test_list_over_limit_uses_forward(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    target = Target.group("456654102")
    monkeypatch.setattr(commands, "get_target", lambda _event, _bot: target)
    await seed_many(store, target, 8)
    bot = FakeForwardBot()

    await commands.list_subscriptions(make_event(), bot)

    assert FakeUniMessage.sent == []
    api, kwargs = bot.calls[0]
    assert api == "send_group_forward_msg"
    assert kwargs["group_id"] == 456654102
    assert kwargs["messages"][0]["data"]["content"][0]["data"]["text"]


@pytest.mark.asyncio
async def test_list_at_limit_stays_plain(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    target = Target.group("456654102")
    monkeypatch.setattr(commands, "get_target", lambda _event, _bot: target)
    await seed_many(store, target, 7)
    bot = FakeForwardBot()

    await commands.list_subscriptions(make_event(), bot)

    assert bot.calls == []
    assert len(FakeUniMessage.sent) == 1


@pytest.mark.asyncio
async def test_list_forward_failure_falls_back(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    target = Target.group("456654102")
    monkeypatch.setattr(commands, "get_target", lambda _event, _bot: target)
    await seed_many(store, target, 8)
    bot = FakeForwardBot(fail=True)

    await commands.list_subscriptions(make_event(), bot)

    assert len(FakeUniMessage.sent) == 1
    assert FakeUniMessage.sent[0].startswith("爱播列表（8 个）")


def test_format_handle() -> None:
    from nonebot_plugin_ifollow.formatting import format_handle

    douyin = AuthorSubscription("douyin", "sec", "作者", author_handle="handle")
    bili = AuthorSubscription("bilibili", "123", "作者", author_handle="123")

    assert format_handle(douyin) == "@handle"
    assert format_handle(bili) == "UID:123"
    assert format_handle(AuthorSubscription("douyin", "sec", "作者")) == ""


@pytest.mark.asyncio
async def test_help_command_sends_help_message(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await commands.help_command(make_event(), Mock())

    assert FakeUniMessage.sent == [commands.formatting.help_message()]


@pytest.mark.asyncio
async def test_subscribe_records_cursor_with_publish_time(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    adapter = FakeAdapter([make_work("w9", 900)])
    monkeypatch.setattr(commands, "detect", lambda _value: adapter)

    await commands.subscribe(make_event(), Mock(), args("https://example.com/author"))

    snapshot = await store.snapshot()
    cursors = snapshot["douyin:sec"].targets[target_key()].cursors
    assert cursors["video"] == Cursor("w9", 900)
    assert FakeUniMessage.sent[0].startswith("已订阅 抖音 · 作者")


@pytest.mark.asyncio
async def test_subscribe_requires_a_value(env: Any) -> None:
    with pytest.raises(FinishedException):
        await commands.subscribe(make_event(), Mock(), args(""))

    assert FakeUniMessage.sent[0].startswith("用法：/爱播订阅")


@pytest.mark.asyncio
async def test_subscribe_existing_target_adds_subscriber(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    await seed_subscription(store)
    monkeypatch.setattr(commands, "detect", lambda _value: FakeAdapter())

    await commands.subscribe(make_event(user_id="43"), Mock(), args("sec"))

    snapshot = await store.snapshot()
    subscribers = snapshot["douyin:sec"].targets[target_key()].subscriber_ids
    assert subscribers == ["42", "43"]
    assert FakeUniMessage.sent[0] == "已为 抖音 · 作者 开启更新提醒。"


@pytest.mark.asyncio
async def test_subscribe_duplicate_reports_noop(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    await seed_subscription(store)
    monkeypatch.setattr(commands, "detect", lambda _value: FakeAdapter())

    await commands.subscribe(make_event(), Mock(), args("sec"))

    assert FakeUniMessage.sent[0] == "本会话已订阅 抖音 · 作者，无需重复。"


@pytest.mark.asyncio
async def test_subscribe_duplicate_refreshes_metadata(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    await seed_subscription(store)
    adapter = FakeAdapter(author=AuthorInfo("sec", "新名字", "新抖音号"))
    monkeypatch.setattr(commands, "detect", lambda _value: adapter)

    await commands.subscribe(make_event(), Mock(), args("sec"))

    snapshot = await store.snapshot()
    author = snapshot["douyin:sec"]
    assert author.author_name == "新名字"
    assert author.author_handle == "新抖音号"
    assert FakeUniMessage.sent[0] == "本会话已订阅 抖音 · 新名字，无需重复。"


@pytest.mark.asyncio
async def test_subscribe_failure_reports_error(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenAdapter(FakeAdapter):
        async def resolve(self, _settings: Settings, _value: str) -> ResolvedAuthor:
            raise ValueError("链接无法识别")

    monkeypatch.setattr(commands, "detect", lambda _value: BrokenAdapter())

    with pytest.raises(FinishedException):
        await commands.subscribe(make_event(), Mock(), args("???"))

    assert FakeUniMessage.sent[0] == (
        "订阅失败：没能识别这个链接\n"
        "请发送作者主页链接、作品链接，或直接发作者 ID。\n"
        "错误码 E-DY-INPUT"
    )


@pytest.mark.asyncio
async def test_unsubscribe_by_index(env: Any) -> None:
    store, _ = env
    await seed_subscription(store)

    await commands.unsubscribe(make_event(), Mock(), args("1"))

    assert FakeUniMessage.sent == ["已退订 抖音 · 作者。"]
    assert await store.snapshot() == {}


@pytest.mark.asyncio
async def test_unsubscribe_rejects_invalid_index(env: Any) -> None:
    store, _ = env
    await seed_subscription(store)

    with pytest.raises(FinishedException):
        await commands.unsubscribe(make_event(), Mock(), args("9"))

    assert "序号无效" in FakeUniMessage.sent[0]


@pytest.mark.asyncio
async def test_unsubscribe_without_subscriptions(env: Any) -> None:
    with pytest.raises(FinishedException):
        await commands.unsubscribe(make_event(), Mock(), args("1"))

    assert FakeUniMessage.sent == [
        "当前会话没有爱播订阅。\n发送 /爱播订阅 <作者主页链接> 开始订阅。"
    ]


@pytest.mark.asyncio
async def test_unsubscribe_matches_local_id_without_network(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    await seed_subscription(store)

    def boom(_value: str) -> None:
        raise AssertionError("不应联网解析作者")

    monkeypatch.setattr(commands, "detect", boom)

    await commands.unsubscribe(make_event(), Mock(), args("sec"))

    assert FakeUniMessage.sent == ["已退订 抖音 · 作者。"]


@pytest.mark.asyncio
async def test_unsubscribe_falls_back_to_network_for_short_links(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    await seed_subscription(store)
    adapter = FakeAdapter()
    monkeypatch.setattr(commands, "detect", lambda _value: adapter)

    await commands.unsubscribe(make_event(), Mock(), args("https://v.douyin.com/x"))

    assert adapter.resolve_calls == 1
    assert FakeUniMessage.sent == ["已退订 抖音 · 作者。"]


@pytest.mark.asyncio
async def test_unsubscribe_reports_missing_subscription(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    await seed_subscription(store)
    monkeypatch.setattr(commands, "detect", lambda _value: FakeAdapter())

    await commands.unsubscribe(make_event(user_id="99"), Mock(), args("sec"))

    assert FakeUniMessage.sent == [
        "「抖音 · 作者」不是你订阅的，只有订阅人、群管理或机器人管理员可以退订。"
    ]


@pytest.mark.asyncio
async def test_unsubscribe_member_cannot_cancel_others(env: Any) -> None:
    """普通群员只能退订自己建的订阅。"""

    store, _ = env
    await seed_subscription(store)  # 订阅人是 42

    await commands.unsubscribe(make_event(user_id="99"), Mock(), args("1"))

    assert "不是你订阅的" in FakeUniMessage.sent[0]
    assert len(await store.snapshot()) == 1


@pytest.mark.asyncio
async def test_unsubscribe_group_admin_cancels_others(env: Any) -> None:
    """群管理员可以退订别人的订阅，整条删掉。"""

    store, _ = env
    await seed_subscription(store)
    event = make_event(user_id="99")
    event.sender.role = "admin"

    await commands.unsubscribe(event, Mock(), args("1"))

    assert FakeUniMessage.sent == ["已退订 抖音 · 作者。"]
    assert await store.snapshot() == {}


@pytest.mark.asyncio
async def test_unsubscribe_group_owner_cancels_others(env: Any) -> None:
    """群主同样算管理员。"""

    store, _ = env
    await seed_subscription(store)
    event = make_event(user_id="99")
    event.sender.role = "owner"

    await commands.unsubscribe(event, Mock(), args("1"))

    assert await store.snapshot() == {}


@pytest.mark.asyncio
async def test_unsubscribe_member_cannot_delete_legacy_entry(env: Any) -> None:
    """订阅人列表为空的老数据，普通群员不能删。"""

    store, _ = env
    key = target_key()
    await store.add_target(
        "douyin",
        "sec",
        "作者",
        key,
        Target.group("group").dump(),
        {"video": Cursor("old", 1)},
    )

    await commands.unsubscribe(make_event(user_id="42"), Mock(), args("1"))

    assert "不是你订阅的" in FakeUniMessage.sent[0]
    assert len(await store.snapshot()) == 1


@pytest.mark.asyncio
async def test_unsubscribe_superuser_cancels_legacy_entry(env: Any) -> None:
    """机器人超级用户可以删老数据。"""

    store, _ = env
    key = target_key()
    await store.add_target(
        "douyin",
        "sec",
        "作者",
        key,
        Target.group("group").dump(),
        {"video": Cursor("old", 1)},
    )
    from nonebot import get_driver

    get_driver().config.superusers.add("99")

    await commands.unsubscribe(make_event(user_id="99"), Mock(), args("1"))

    assert await store.snapshot() == {}


class FakeReply:
    """只带 message 字段的引用消息，和 OneBot 的 Reply 一致。"""

    def __init__(self, text: str) -> None:
        self.message = Message(text)


def test_quoted_author_parses_notice() -> None:
    """引用通知时能从首行认出平台和作者。"""

    text = (
        "抖音 · 01年的小女孩 发布了新作品\n"
        "────────────\n"
        "#泳装搭配指南👙\n"
        "\n"
        "00:06 · 720×1280 · 2.5 MB\n"
        "2026-10-19 01:39"
    )

    assert commands.quoted_author(FakeReply(text)) == ("抖音", "01年的小女孩")


def test_quoted_author_returns_none_for_other_text() -> None:
    """引用的不是爱播通知时认不出来。"""

    assert commands.quoted_author(FakeReply("随便聊两句")) is None
    assert commands.quoted_author(None) is None


@pytest.mark.asyncio
async def test_unsubscribe_from_quoted_notice(env: Any) -> None:
    """引用通知、不带参数也能退订。"""

    store, _ = env
    await seed_subscription(store)  # 订阅人是 42
    event = make_event(user_id="42")
    event.reply = FakeReply("抖音 · 作者 发布了新作品\n────")

    await commands.unsubscribe(event, Mock(), args(""))

    assert FakeUniMessage.sent == ["已退订 抖音 · 作者。"]
    assert await store.snapshot() == {}


@pytest.mark.asyncio
async def test_unsubscribe_from_quoted_notice_unknown_author(env: Any) -> None:
    """引用的通知里那个作者没订阅时给出提示。"""

    store, _ = env
    await seed_subscription(store)
    event = make_event(user_id="42")
    event.reply = FakeReply("抖音 · 别人 发布了新作品\n────")

    with pytest.raises(FinishedException):
        await commands.unsubscribe(event, Mock(), args(""))

    assert "「别人」当前没有订阅" in FakeUniMessage.sent[0]
    assert len(await store.snapshot()) == 1


def test_target_subscriptions_group_by_platform() -> None:
    """列表按平台分组；组内保持订阅先后。"""

    target = Target.group("group")
    key = make_target_key(target.dump())
    bili = AuthorSubscription(
        "bilibili", "b1", "B站作者", {key: TargetSubscription(target.dump())}
    )
    douyin_first = AuthorSubscription(
        "douyin", "d1", "抖音先", {key: TargetSubscription(target.dump())}
    )
    douyin_second = AuthorSubscription(
        "douyin", "d2", "抖音后", {key: TargetSubscription(target.dump())}
    )
    authors = {
        bili.key: bili,
        douyin_first.key: douyin_first,
        douyin_second.key: douyin_second,
    }

    items = commands.formatting.target_subscriptions(authors, key)

    assert [author.author_id for author in items] == ["d1", "d2", "b1"]


def test_relative_time_buckets() -> None:
    now = 1_700_000_000.0

    assert commands.formatting.format_relative_time(0) == "未知"
    assert commands.formatting.format_relative_time(int(now) - 5, now=now) == "刚刚"
    assert (
        commands.formatting.format_relative_time(int(now) - 300, now=now) == "5 分钟前"
    )
    assert (
        commands.formatting.format_relative_time(int(now) - 7200, now=now) == "2 小时前"
    )
    assert (
        commands.formatting.format_relative_time(int(now) - 86400 * 3, now=now)
        == "3 天前"
    )
    old = int(now) - 86400 * 200
    expected = datetime.fromtimestamp(old).strftime("%Y-%m-%d")
    assert commands.formatting.format_relative_time(old, now=now) == expected


@pytest.mark.asyncio
async def test_unsubscribe_index_matches_displayed_order(env: Any) -> None:
    """列表里第 1 条就是「退订 1」删掉的那条，即使 B 站先订阅。"""

    store, _ = env
    key = target_key()
    bili = AuthorSubscription(
        "bilibili",
        "b1",
        "B站作者",
        {key: TargetSubscription(Target.group("group").dump())},
    )
    douyin = AuthorSubscription(
        "douyin",
        "d1",
        "抖音作者",
        {key: TargetSubscription(Target.group("group").dump())},
    )
    authors = {bili.key: bili, douyin.key: douyin}
    ordered = commands.formatting.target_subscriptions(authors, key)

    await store.add_target(
        "bilibili", "b1", "B站作者", key, Target.group("group").dump(), {}
    )
    await store.add_target(
        "douyin", "d1", "抖音作者", key, Target.group("group").dump(), {}
    )

    # 这两条是老数据（没有订阅人列表），普通群员删不了，这里用管理员身份验顺序
    event = make_event()
    event.sender.role = "admin"

    await commands.unsubscribe(event, Mock(), args("1"))

    snapshot = await store.snapshot()
    assert ordered[0].author_id == "d1"
    assert "douyin:d1" not in snapshot
    assert "bilibili:b1" in snapshot


def test_match_subscription_uses_id_handle_and_url() -> None:
    douyin = AuthorSubscription(
        "douyin",
        "MS4wLjABAAAA-x",
        "小葵",
        {"k": TargetSubscription({})},
        author_handle="kui",
    )
    bili = AuthorSubscription(
        "bilibili", "123", "呆呆丸", {"k": TargetSubscription({})}, author_handle="123"
    )
    items = [douyin, bili]

    assert commands.match_subscription(items, "MS4wLjABAAAA-x") is douyin
    assert commands.match_subscription(items, "kui") is douyin
    assert (
        commands.match_subscription(items, "https://www.douyin.com/user/MS4wLjABAAAA-x")
        is douyin
    )
    assert commands.match_subscription(items, "https://space.bilibili.com/123") is bili
    assert commands.match_subscription(items, "呆呆丸") is bili
    assert commands.match_subscription(items, "别人") is None


def test_match_subscription_rejects_ambiguous_nickname() -> None:
    first = AuthorSubscription("douyin", "a", "同名", {"k": TargetSubscription({})})
    second = AuthorSubscription("douyin", "b", "同名", {"k": TargetSubscription({})})

    assert commands.match_subscription([first, second], "同名") is None


@pytest.mark.asyncio
async def test_list_subscriptions_shows_authors(env: Any) -> None:
    store, _ = env
    await seed_subscription(store)

    await commands.list_subscriptions(make_event(), Mock())

    assert FakeUniMessage.sent[0] == (
        "爱播列表（1 个）\n"
        f"{commands.formatting.SEPARATOR}\n"
        "抖音\n"
        "1. 作者 @handle · 未知\n"
        "\n"
        "退订：/爱播退订 <序号>"
    )


@pytest.mark.asyncio
async def test_list_subscriptions_reports_empty(env: Any) -> None:
    await commands.list_subscriptions(make_event(), Mock())

    assert FakeUniMessage.sent == [
        "当前会话没有爱播订阅。\n发送 /爱播订阅 <作者主页链接> 开始订阅。"
    ]


@pytest.mark.asyncio
async def test_status_command_rejects_non_superuser(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        commands,
        "get_driver",
        lambda: SimpleNamespace(config=SimpleNamespace(superusers={"999"})),
    )

    with pytest.raises(FinishedException):
        await commands.status_command(make_event(), Mock())

    assert FakeUniMessage.sent == ["仅超级用户可以使用 /爱播状态。"]


@pytest.mark.asyncio
async def test_status_command_sends_status_text(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, poller = env
    await seed_subscription(store)
    monkeypatch.setattr(
        commands,
        "get_driver",
        lambda: SimpleNamespace(config=SimpleNamespace(superusers={"42"})),
    )

    await commands.status_command(make_event(), Mock())

    assert FakeUniMessage.sent[0].startswith("爱播状态")
    assert poller.stats.authors == 0


@pytest.mark.asyncio
async def test_replied_text_reads_quoted_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nonebot_plugin_ifollow import reply

    event = make_event()
    event.get_message.return_value = [SimpleNamespace(type="reply", data={"id": "77"})]
    bot = Mock()
    bot.call_api = AsyncMock(
        return_value={"message": [{"data": {"text": "链接在引用里"}}]}
    )

    text = await reply.replied_text(event, bot)

    assert text == "链接在引用里"
    bot.call_api.assert_awaited_once_with("get_msg", message_id="77")


@pytest.mark.asyncio
async def test_replied_text_prefers_event_reply() -> None:
    from nonebot_plugin_ifollow import reply

    event = make_event()
    event.reply = SimpleNamespace(message=[SimpleNamespace(data={"text": "直接拿到"})])

    assert await reply.replied_text(event, Mock()) == "直接拿到"


@pytest.mark.asyncio
async def test_replied_text_returns_empty_when_nothing_quoted() -> None:
    from nonebot_plugin_ifollow import reply

    assert await reply.replied_text(make_event(), Mock()) == ""


@pytest.mark.asyncio
async def test_replied_text_swallows_api_failure() -> None:
    from nonebot_plugin_ifollow import reply

    event = make_event()
    event.get_message.return_value = [SimpleNamespace(type="reply", data={"id": "1"})]
    bot = Mock()

    async def boom(**_kwargs: Any) -> None:
        raise RuntimeError("接口不可用")

    bot.call_api = boom

    assert await reply.replied_text(event, bot) == ""


def test_describe_failure_maps_categories_and_codes() -> None:
    from nonebot_plugin_ifollow import formatting
    from nonebot_plugin_ifollow.errors import PermanentError, TransientError

    assert formatting.describe_failure(ValueError("坏链接")).code == "E-INPUT"
    assert (
        formatting.describe_failure(
            PermanentError("抖音作者不存在或已注销：x"), platform="douyin"
        ).code
        == "E-DY-GONE"
    )
    assert (
        formatting.describe_failure(
            TransientError("抖音接口被风控拦截"), platform="douyin"
        ).code
        == "E-DY-RISK"
    )
    assert (
        formatting.describe_failure(
            TransientError("B 站投稿列表请求失败：超时"), platform="bilibili"
        ).code
        == "E-BILI-NET"
    )
    assert formatting.describe_failure(RuntimeError("???"), platform="douyin").code == (
        "E-DY-UNKNOWN"
    )


def test_render_failure_has_no_stack_details() -> None:
    from nonebot_plugin_ifollow import formatting

    report = formatting.describe_failure(ValueError("x"))

    text = formatting.render_failure("订阅", report)

    assert "Traceback" not in text
    assert text.endswith("错误码 E-INPUT")


@pytest.mark.asyncio
async def test_subscribe_rejects_unrecognised_input(env: Any) -> None:
    """识别不出平台时直接给 E-INPUT，而不是把异常字符串丢进群。"""

    with pytest.raises(FinishedException):
        await commands.subscribe(make_event(), Mock(), args("随便一段文字"))

    assert FakeUniMessage.sent[0].startswith("订阅失败：没能识别这个链接")
    assert FakeUniMessage.sent[0].endswith("错误码 E-INPUT")


@pytest.mark.asyncio
async def test_subscribe_race_falls_back_to_duplicate_message(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """add_target 期间被别人抢先写入时，退回「无需重复」。"""

    store, _ = env
    monkeypatch.setattr(commands, "detect", lambda _value: FakeAdapter())
    monkeypatch.setattr(store, "add_target", AsyncMock(return_value=False))
    monkeypatch.setattr(store, "add_subscriber", AsyncMock(return_value=False))

    await commands.subscribe(make_event(), Mock(), args("sec"))

    assert FakeUniMessage.sent[0] == "本会话已订阅 抖音 · 作者，无需重复。"


@pytest.mark.asyncio
async def test_unsubscribe_requires_a_value(env: Any) -> None:
    with pytest.raises(FinishedException):
        await commands.unsubscribe(make_event(), Mock(), args(""))

    assert FakeUniMessage.sent == [
        "用法：/爱播退订 <序号、作者 ID、昵称或链接>\n"
        "也可以引用一条爱播通知后直接发 /爱播退订。"
    ]


@pytest.mark.asyncio
async def test_unsubscribe_keeps_other_subscribers(env: Any) -> None:
    """只删掉自己的提醒，其他订阅人仍保留。"""

    store, _ = env
    key = await seed_subscription(store)
    await store.add_subscriber("douyin", "sec", key, "43")

    await commands.unsubscribe(make_event(), Mock(), args("sec"))

    assert FakeUniMessage.sent == [
        "已退订你在 抖音 · 作者 的提醒，其他订阅人仍会收到通知。"
    ]
    snapshot = await store.snapshot()
    assert snapshot["douyin:sec"].targets[key].subscriber_ids == ["43"]


@pytest.mark.asyncio
async def test_unsubscribe_rejects_unrecognised_input(env: Any) -> None:
    store, _ = env
    await seed_subscription(store)

    with pytest.raises(FinishedException):
        await commands.unsubscribe(make_event(), Mock(), args("随便一段文字"))

    assert FakeUniMessage.sent[0].endswith("错误码 E-INPUT")


@pytest.mark.asyncio
async def test_unsubscribe_network_failure_uses_platform_code(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nonebot_plugin_ifollow.errors import TransientError

    store, _ = env
    await seed_subscription(store)

    class FailingAdapter(FakeAdapter):
        async def resolve(self, _settings: Settings, _value: str) -> ResolvedAuthor:
            raise TransientError("抖音接口被风控拦截：HTTP 403")

    monkeypatch.setattr(commands, "detect", lambda _value: FailingAdapter())

    with pytest.raises(FinishedException):
        await commands.unsubscribe(make_event(), Mock(), args("https://v.douyin.com/x"))

    assert FakeUniMessage.sent[0].startswith("退订失败：平台暂时拦截了请求")
    assert FakeUniMessage.sent[0].endswith("错误码 E-DY-RISK")
