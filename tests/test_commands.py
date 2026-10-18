from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, Mock

import pytest
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

    def __init__(self, works: list[PlatformWork] | None = None) -> None:
        self.works = works if works is not None else [make_work()]
        self.resolve_calls = 0

    def matches(self, _value: str) -> bool:
        return True

    def feeds(self, _settings: Settings) -> tuple[str, ...]:
        return ("video",)

    async def resolve(self, _settings: Settings, _value: str) -> ResolvedAuthor:
        self.resolve_calls += 1
        author = AuthorInfo("sec", "作者", "handle")
        return ResolvedAuthor(author, {"video": self.works})

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


def test_format_handle() -> None:
    from nonebot_plugin_ifollow.formatting import format_handle

    douyin = AuthorSubscription("douyin", "sec", "作者", author_handle="handle")
    bili = AuthorSubscription("bilibili", "123", "作者", author_handle="123")

    assert format_handle(douyin) == "@handle"
    assert format_handle(bili) == "UID:123"
    assert format_handle(AuthorSubscription("douyin", "sec", "作者")) == "未知"


@pytest.mark.asyncio
async def test_help_command_sends_help_message(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await commands.help_command(make_event(), Mock())

    assert FakeUniMessage.sent == [commands.formatting.HELP_MESSAGE]


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
    assert FakeUniMessage.sent[0].startswith("已订阅抖音作者 作者。")


@pytest.mark.asyncio
async def test_subscribe_requires_a_value(env: Any) -> None:
    with pytest.raises(FinishedException):
        await commands.subscribe(make_event(), Mock(), args(""))

    assert FakeUniMessage.sent[0].startswith("用法：爱播订阅")


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
    assert "已加入抖音作者" in FakeUniMessage.sent[0]


@pytest.mark.asyncio
async def test_subscribe_duplicate_reports_noop(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    await seed_subscription(store)
    monkeypatch.setattr(commands, "detect", lambda _value: FakeAdapter())

    await commands.subscribe(make_event(), Mock(), args("sec"))

    assert "无需重复订阅" in FakeUniMessage.sent[0]


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
    assert FakeUniMessage.sent[0] == "订阅失败：链接无法识别"


@pytest.mark.asyncio
async def test_unsubscribe_by_index(env: Any) -> None:
    store, _ = env
    await seed_subscription(store)

    await commands.unsubscribe(make_event(), Mock(), args("1"))

    assert FakeUniMessage.sent == ["已取消订阅。"]
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

    assert FakeUniMessage.sent == ["当前会话没有爱播订阅。"]


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

    assert FakeUniMessage.sent == ["已取消订阅。"]


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
    assert FakeUniMessage.sent == ["已取消订阅。"]


@pytest.mark.asyncio
async def test_unsubscribe_reports_missing_subscription(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _ = env
    await seed_subscription(store)
    monkeypatch.setattr(commands, "detect", lambda _value: FakeAdapter())

    await commands.unsubscribe(make_event(user_id="99"), Mock(), args("sec"))

    assert FakeUniMessage.sent == ["当前会话没有你的这个订阅。"]


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

    assert "当前会话的爱播订阅" in FakeUniMessage.sent[0]
    assert "抖音 作者（@handle）" in FakeUniMessage.sent[0]


@pytest.mark.asyncio
async def test_list_subscriptions_reports_empty(env: Any) -> None:
    await commands.list_subscriptions(make_event(), Mock())

    assert FakeUniMessage.sent == ["当前会话没有爱播订阅。"]


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

    assert FakeUniMessage.sent == ["仅超级用户可以使用 爱播状态。"]


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
