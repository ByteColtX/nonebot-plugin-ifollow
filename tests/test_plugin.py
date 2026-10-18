from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from nonebot_plugin_alconna.uniseg import Target

from nonebot_plugin_ifollow import plugin
from nonebot_plugin_ifollow.models import (
    AuthorSubscription,
    SubscriptionStore,
    TargetSubscription,
    make_target_key,
)
from nonebot_plugin_ifollow.platforms import AuthorInfo, PlatformWork


def make_work(
    work_id: str,
    *,
    bvid: str = "",
    kind: str = "video",
    timestamp: int = 0,
) -> PlatformWork:
    return PlatformWork(
        platform="bilibili",
        work_id=work_id,
        bvid=bvid,
        url=f"https://www.bilibili.com/video/{bvid or work_id}",
        title=work_id,
        description="",
        create_time=timestamp,
        author=AuthorInfo("123", "作者", "123"),
        kind=kind,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_deliver_new_works_oldest_first_and_updates_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SubscriptionStore(tmp_path)
    monkeypatch.setattr(plugin, "store", store)
    target = Target.group("87654321")
    target_key = make_target_key(target.dump())
    await store.upsert_target(
        "bilibili", "123", "作者", target_key, target.dump(), {"video": "old"}
    )
    author = AuthorSubscription(
        "bilibili",
        "123",
        "作者",
        {target_key: TargetSubscription(target.dump(), {"video": "old"})},
    )
    sent: list[str] = []

    async def send(url: str, _platform: str, _target: Target) -> None:
        sent.append(url)

    monkeypatch.setattr(plugin, "_send_work", send)
    works = [make_work("new-2", timestamp=3), make_work("new-1", timestamp=2)]
    await plugin._deliver_new_works(
        author,
        target_key,
        author.targets[target_key],
        "video",
        [*works, make_work("old", timestamp=1)],
        set(),
    )
    assert sent == [
        "https://www.bilibili.com/video/new-1",
        "https://www.bilibili.com/video/new-2",
    ]
    assert (await store.snapshot())["bilibili:123"].targets[target_key].cursors[
        "video"
    ] == "new-2"


@pytest.mark.asyncio
async def test_send_failure_keeps_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SubscriptionStore(tmp_path)
    monkeypatch.setattr(plugin, "store", store)
    target = Target.group("group")
    target_key = make_target_key(target.dump())
    await store.upsert_target(
        "bilibili", "123", "作者", target_key, target.dump(), {"video": "old"}
    )
    author = AuthorSubscription(
        "bilibili",
        "123",
        "作者",
        {target_key: TargetSubscription(target.dump(), {"video": "old"})},
    )
    monkeypatch.setattr(plugin, "_send_work", AsyncMock(side_effect=RuntimeError()))
    await plugin._deliver_new_works(
        author,
        target_key,
        author.targets[target_key],
        "video",
        [make_work("new")],
        set(),
    )
    assert (await store.snapshot())["bilibili:123"].targets[target_key].cursors[
        "video"
    ] == "old"


@pytest.mark.asyncio
async def test_dynamic_video_is_deduplicated_by_bvid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SubscriptionStore(tmp_path)
    monkeypatch.setattr(plugin, "store", store)
    target = Target.group("group")
    target_key = make_target_key(target.dump())
    await store.upsert_target(
        "bilibili",
        "123",
        "作者",
        target_key,
        target.dump(),
        {"video": "old-video", "dynamic": "old-dynamic"},
    )
    author = AuthorSubscription(
        "bilibili",
        "123",
        "作者",
        {
            target_key: TargetSubscription(
                target.dump(), {"video": "old-video", "dynamic": "old-dynamic"}
            )
        },
    )
    sent = AsyncMock()
    monkeypatch.setattr(plugin, "_send_work", sent)
    sent_bvids = {"BVsame"}
    await plugin._deliver_new_works(
        author,
        target_key,
        author.targets[target_key],
        "dynamic",
        [make_work("dynamic-new", bvid="BVsame", kind="video")],
        sent_bvids,
    )
    sent.assert_not_awaited()
    assert (await store.snapshot())["bilibili:123"].targets[target_key].cursors[
        "dynamic"
    ] == "dynamic-new"


def test_format_handle() -> None:
    douyin = AuthorSubscription("douyin", "sec", "作者", author_handle="handle")
    bili = AuthorSubscription("bilibili", "123", "作者", author_handle="123")
    assert plugin._format_handle(douyin) == "@handle"
    assert plugin._format_handle(bili) == "UID:123"


@pytest.mark.asyncio
async def test_help_command_sends_help_message(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    class FakeUniMessage:
        def __init__(self, message: str) -> None:
            sent.append(message)

        async def send(self, *, target: Target, bot: object) -> None:
            del target, bot

    monkeypatch.setattr(plugin, "UniMessage", FakeUniMessage)
    monkeypatch.setattr(
        plugin, "get_target", lambda _event, _bot: Target.group("group")
    )

    await plugin.help_command(Mock(), Mock())

    assert sent == [plugin.HELP_MESSAGE]
    assert "爱播取消订阅 <序号或作者主页/作品链接>" in sent[0]


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
    await store.upsert_target(
        "douyin", "123", "作者", target_key, target.dump(), {"video": "old"}
    )
    startup_logger = Mock()
    monkeypatch.setattr(plugin, "store", store)
    monkeypatch.setattr(plugin, "logger", startup_logger)

    await plugin.load_subscriptions_on_startup()

    startup_logger.info.assert_called_once_with(
        "成功加载 1 个爱播作者订阅，覆盖 1 个会话。"
    )


def test_backoff_is_capped() -> None:
    delays = [
        plugin._exponential_backoff_seconds(attempt, base_seconds=30, max_seconds=300)
        for attempt in range(1, 7)
    ]
    assert delays == [30, 60, 120, 240, 300, 300]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("platform", "factory_name", "url"),
    [
        ("bilibili", "bilibili_media_messages", "https://t.bilibili.com/1"),
        ("douyin", "douyin_media_messages", "https://www.douyin.com/video/1"),
    ],
)
async def test_send_work_dispatches_to_platform_media(
    platform: str,
    factory_name: str,
    url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Target]] = []

    class FakeMessage:
        def __init__(self, message_url: str) -> None:
            self.message_url = message_url

        async def send(self, target: Target) -> None:
            calls.append((self.message_url, target))

    @asynccontextmanager
    async def fake_media(media_url: str):
        yield [FakeMessage(media_url)]

    monkeypatch.setattr(plugin, factory_name, fake_media)
    target = Target.group("group")

    await plugin._send_work(url, platform, target)

    assert calls == [(url, target)]
