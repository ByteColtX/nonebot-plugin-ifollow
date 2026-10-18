from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import Mock

import pytest
from nonebot.exception import FinishedException
from nonebot_plugin_alconna.uniseg import Target

from nonebot_plugin_ifollow import plugin, polling
from nonebot_plugin_ifollow.models import (
    AuthorSubscription,
    SubscriptionStore,
    TargetSubscription,
    make_target_key,
)
from nonebot_plugin_ifollow.platforms import AuthorInfo, Cursor, PlatformWork


class FakeUniMessage:
    """记录发送内容的消息替身。"""

    sent: ClassVar[list[str]] = []

    def __init__(self, message: str) -> None:
        self.message = message

    async def send(self, *, target: Target, bot: object) -> None:
        del target, bot
        FakeUniMessage.sent.append(self.message)

    async def finish(self) -> None:
        await self.send(target=Target.group("group"), bot=Mock())
        raise FinishedException


@pytest.fixture(autouse=True)
def reset_sent() -> None:
    FakeUniMessage.sent = []


def test_format_handle() -> None:
    douyin = AuthorSubscription("douyin", "sec", "作者", author_handle="handle")
    bili = AuthorSubscription("bilibili", "123", "作者", author_handle="123")
    assert plugin._format_handle(douyin) == "@handle"
    assert plugin._format_handle(bili) == "UID:123"


@pytest.mark.asyncio
async def test_help_command_sends_help_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin, "UniMessage", FakeUniMessage)
    monkeypatch.setattr(
        plugin, "get_target", lambda _event, _bot: Target.group("group")
    )

    await plugin.help_command(Mock(), Mock())

    assert FakeUniMessage.sent == [plugin.HELP_MESSAGE]
    assert (
        "爱播取消订阅 <序号、作者 ID、昵称或作者主页/作品链接>"
        in FakeUniMessage.sent[0]
    )


def test_target_subscriptions_preserve_list_order() -> None:
    target = Target.group("group")
    target_key = make_target_key(target.dump())
    authors = {
        "douyin:1": AuthorSubscription(
            "douyin", "1", "第一个", {target_key: TargetSubscription(target.dump())}
        ),
        "bilibili:2": AuthorSubscription(
            "bilibili", "2", "第二个", {target_key: TargetSubscription(target.dump())}
        ),
        "douyin:3": AuthorSubscription("douyin", "3", "其他会话"),
    }

    items = plugin._target_subscriptions(authors, target_key)

    assert [author.author_id for author in items] == ["1", "2"]


@pytest.mark.asyncio
async def test_load_subscriptions_on_startup_logs_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SubscriptionStore(tmp_path)
    target = Target.group("group")
    target_key = make_target_key(target.dump())
    await store.upsert_target("douyin", "123", "作者", target_key, target.dump(), {})
    startup_logger = Mock()
    started: list[SubscriptionStore] = []
    monkeypatch.setattr(plugin, "store", store)
    monkeypatch.setattr(plugin, "logger", startup_logger)
    monkeypatch.setattr(plugin, "start", started.append)

    await plugin.load_subscriptions_on_startup()

    startup_logger.info.assert_called_once_with(
        "成功加载 1 个爱播作者订阅，覆盖 1 个会话。"
    )
    assert started == [store]


@pytest.mark.asyncio
async def test_stop_polling_on_shutdown_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[bool] = []

    async def fake_stop() -> None:
        calls.append(True)

    monkeypatch.setattr(plugin, "stop", fake_stop)

    await plugin.stop_polling_on_shutdown()

    assert calls == [True]


@pytest.mark.asyncio
async def test_warm_up_on_startup_only_with_bilibili_authors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SubscriptionStore(tmp_path)
    target = Target.group("group")
    target_key = make_target_key(target.dump())
    calls: list[bool] = []

    async def fake_warm_up() -> None:
        calls.append(True)

    monkeypatch.setattr(plugin, "store", store)
    monkeypatch.setattr(plugin, "warm_up_bilibili", fake_warm_up)
    monkeypatch.setattr(plugin, "logger", Mock())
    monkeypatch.setattr(plugin, "bilibili_dynamics_enabled", lambda: False)

    await plugin.warm_up_bilibili_on_startup()
    assert calls == []

    await store.upsert_target("bilibili", "2", "作者", target_key, target.dump(), {})
    await plugin.warm_up_bilibili_on_startup()
    assert calls == []

    monkeypatch.setattr(plugin, "bilibili_dynamics_enabled", lambda: True)
    await plugin.warm_up_bilibili_on_startup()
    assert calls == [True]


@pytest.mark.asyncio
async def test_status_command_rejects_non_superuser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(plugin, "UniMessage", FakeUniMessage)
    monkeypatch.setattr(
        plugin,
        "get_driver",
        lambda: SimpleNamespace(config=SimpleNamespace(superusers={"999"})),
    )
    event = Mock()
    event.get_user_id.return_value = "123"

    with pytest.raises(FinishedException):
        await plugin.status_command(event, Mock())

    assert FakeUniMessage.sent == ["仅超级用户可以使用 爱播状态。"]


@pytest.mark.asyncio
async def test_status_command_sends_status_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SubscriptionStore(tmp_path)
    monkeypatch.setattr(plugin, "store", store)
    monkeypatch.setattr(plugin, "UniMessage", FakeUniMessage)
    monkeypatch.setattr(
        plugin,
        "get_driver",
        lambda: SimpleNamespace(config=SimpleNamespace(superusers={"123"})),
    )
    monkeypatch.setattr(
        plugin, "get_target", lambda _event, _bot: Target.group("group")
    )
    monkeypatch.setattr(plugin, "status_text", lambda _authors: "爱播状态正文")
    event = Mock()
    event.get_user_id.return_value = "123"

    await plugin.status_command(event, Mock())

    assert FakeUniMessage.sent == ["爱播状态正文"]
    assert polling.tick_seconds() > 0


@pytest.mark.asyncio
async def test_subscribe_records_cursor_with_publish_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """首次订阅只记录最新作品，并带上发布时间供后续按时间窗口补发。"""

    store = SubscriptionStore(tmp_path)
    target = Target.group("group")
    work = PlatformWork(
        platform="bilibili",
        work_id="BV1",
        bvid="BV1",
        url="https://www.bilibili.com/video/BV1",
        title="作品",
        description="",
        create_time=100,
        author=AuthorInfo("123", "作者", "123"),
    )
    monkeypatch.setattr(plugin, "store", store)
    monkeypatch.setattr(plugin, "UniMessage", FakeUniMessage)
    monkeypatch.setattr(
        plugin, "get_target", lambda _event, _bot: Target.group("group")
    )

    async def fake_resolve(_value: str) -> tuple[str, AuthorInfo]:
        return "bilibili", AuthorInfo("123", "作者", "123")

    async def fake_feeds(
        _platform: str, _author_id: str
    ) -> dict[str, list[PlatformWork]]:
        return {"video": [work]}

    monkeypatch.setattr(plugin, "resolve_author", fake_resolve)
    monkeypatch.setattr(plugin, "fetch_author_feeds", fake_feeds)
    event = Mock()
    event.get_user_id.return_value = "111"
    args = Mock()
    args.extract_plain_text.return_value = "https://space.bilibili.com/123"

    await plugin.subscribe(event, Mock(), args)

    target_key = make_target_key(target.dump())
    snapshot = await store.snapshot()
    assert snapshot["bilibili:123"].targets[target_key].cursors == {
        "video": Cursor("BV1", 100)
    }
    assert FakeUniMessage.sent[0].startswith("已订阅B站作者 作者。")


def _subscription(
    platform: str, author_id: str, name: str, handle: str
) -> AuthorSubscription:
    target = Target.group("group")
    target_key = make_target_key(target.dump())
    return AuthorSubscription(
        platform,  # type: ignore[arg-type]
        author_id,
        name,
        {target_key: TargetSubscription(target.dump())},
        author_handle=handle,
    )


def test_match_subscription_uses_id_handle_and_url() -> None:
    douyin = _subscription("douyin", "MS4wLjABAAAA-x", "小葵", "kui")
    bili = _subscription("bilibili", "123", "呆呆丸", "123")
    items = [douyin, bili]

    assert plugin._match_subscription(items, "MS4wLjABAAAA-x") is douyin
    assert plugin._match_subscription(items, "kui") is douyin
    assert (
        plugin._match_subscription(items, "https://www.douyin.com/user/MS4wLjABAAAA-x")
        is douyin
    )
    assert plugin._match_subscription(items, "https://space.bilibili.com/123") is bili
    assert plugin._match_subscription(items, "呆呆丸") is bili
    assert plugin._match_subscription(items, "别人") is None


def test_match_subscription_rejects_ambiguous_nickname() -> None:
    """昵称重名时不猜，让用户改用序号。"""

    first = _subscription("douyin", "a", "同名", "a")
    second = _subscription("douyin", "b", "同名", "b")

    assert plugin._match_subscription([first, second], "同名") is None


@pytest.mark.asyncio
async def test_unsubscribe_matches_local_id_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """按作者 ID 取消订阅不联网，作者注销或接口被风控也能取消。"""

    store = SubscriptionStore(tmp_path)
    target = Target.group("group")
    target_key = make_target_key(target.dump())
    await store.upsert_target(
        "douyin", "MS4wLjABAAAA-x", "小葵", target_key, target.dump(), {}
    )

    async def boom(_value: str) -> None:
        raise AssertionError("不应联网解析作者")

    monkeypatch.setattr(plugin, "store", store)
    monkeypatch.setattr(plugin, "resolve_author", boom)
    monkeypatch.setattr(plugin, "UniMessage", FakeUniMessage)
    monkeypatch.setattr(plugin, "get_target", lambda _event, _bot: target)
    event = Mock()
    event.get_user_id.return_value = "123"
    args = SimpleNamespace(extract_plain_text=lambda: "MS4wLjABAAAA-x")

    await plugin.unsubscribe(event, Mock(), args)

    assert FakeUniMessage.sent == ["已取消订阅。"]
    assert await store.snapshot() == {}
