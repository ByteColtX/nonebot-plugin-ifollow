from datetime import datetime
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock

import pytest
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot_plugin_alconna.uniseg import Target

from nonebot_plugin_douyin_subscribe import douyin
from nonebot_plugin_douyin_subscribe import plugin as subscribe_plugin
from nonebot_plugin_douyin_subscribe.models import (
    AuthorSubscription,
    SubscriptionStore,
    TargetSubscription,
)


@pytest.mark.asyncio
async def test_deliver_new_works_sends_oldest_first_and_updates_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SubscriptionStore(tmp_path)
    monkeypatch.setattr(subscribe_plugin, "store", store)
    target = Target.group("87654321")
    target_key = subscribe_plugin._target_key(target)
    author = AuthorSubscription(
        sec_user_id="user-id",
        author_name="作者",
        targets={target_key: TargetSubscription(target.dump(), "100")},
    )
    await store.upsert_target("user-id", "作者", target_key, target.dump(), "100")
    works = [
        douyin.DouyinWork("102", "https://www.douyin.com/video/102", "", 102, "作者"),
        douyin.DouyinWork("101", "https://www.douyin.com/video/101", "", 101, "作者"),
        douyin.DouyinWork("100", "https://www.douyin.com/video/100", "", 100, "作者"),
    ]
    sent_urls: list[str] = []

    async def send(url: str, _target: Target) -> None:
        sent_urls.append(url)

    monkeypatch.setattr(subscribe_plugin, "_send_through_parser", send)

    await subscribe_plugin._deliver_new_works(
        author, target_key, author.targets[target_key], works
    )

    assert sent_urls == [
        "https://www.douyin.com/video/101",
        "https://www.douyin.com/video/102",
    ]
    snapshot = await store.snapshot()
    assert snapshot["user-id"].targets[target_key].last_aweme_id == "102"


@pytest.mark.asyncio
async def test_deliver_new_works_keeps_cursor_when_parser_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SubscriptionStore(tmp_path)
    monkeypatch.setattr(subscribe_plugin, "store", store)
    target = Target.group("87654321")
    target_key = subscribe_plugin._target_key(target)
    author = AuthorSubscription(
        "user-id",
        "作者",
        {target_key: TargetSubscription(target.dump(), "100")},
    )
    await store.upsert_target("user-id", "作者", target_key, target.dump(), "100")
    send = AsyncMock(side_effect=RuntimeError("parser failed"))
    monkeypatch.setattr(subscribe_plugin, "_send_through_parser", send)
    works = [
        douyin.DouyinWork("101", "https://www.douyin.com/video/101", "", 101, "作者")
    ]

    await subscribe_plugin._deliver_new_works(
        author, target_key, author.targets[target_key], works
    )

    send.assert_awaited_once()
    assert (await store.snapshot())["user-id"].targets[
        target_key
    ].last_aweme_id == "100"


@pytest.mark.asyncio
async def test_poll_continues_when_an_author_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authors = {
        "failed-author": AuthorSubscription("failed-author", "失败作者"),
        "healthy-author": AuthorSubscription("healthy-author", "正常作者"),
    }

    class FakeStore:
        async def snapshot(self) -> dict[str, AuthorSubscription]:
            return authors

    calls: list[str] = []

    async def poll_author(author: AuthorSubscription) -> None:
        calls.append(author.sec_user_id)
        if author.sec_user_id == "failed-author":
            raise subscribe_plugin.DouyinRequestError(
                "temporary failure", retryable=True
            )

    async def skip_delay(_delay: float) -> None:
        return

    monkeypatch.setattr(subscribe_plugin, "store", FakeStore())
    monkeypatch.setattr(subscribe_plugin, "_poll_author", poll_author)
    monkeypatch.setattr(subscribe_plugin.asyncio, "sleep", skip_delay)
    subscribe_plugin.author_poll_states.clear()

    await subscribe_plugin.poll_subscriptions()

    assert calls == ["failed-author", "healthy-author"]
    assert subscribe_plugin.author_poll_states["failed-author"].failure_count == 1
    assert "healthy-author" not in subscribe_plugin.author_poll_states


@pytest.mark.asyncio
async def test_poll_skips_only_author_in_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authors = {
        "failed-author": AuthorSubscription("failed-author", "失败作者"),
        "healthy-author": AuthorSubscription("healthy-author", "正常作者"),
    }

    class FakeStore:
        async def snapshot(self) -> dict[str, AuthorSubscription]:
            return authors

    calls: list[str] = []

    async def poll_author(author: AuthorSubscription) -> None:
        calls.append(author.sec_user_id)

    monkeypatch.setattr(subscribe_plugin, "store", FakeStore())
    monkeypatch.setattr(subscribe_plugin, "_poll_author", poll_author)
    subscribe_plugin.author_poll_states.clear()
    subscribe_plugin.author_poll_states["failed-author"] = (
        subscribe_plugin.AuthorPollState(
            failure_count=2,
            retry_at=subscribe_plugin.time.monotonic() + 60,
        )
    )

    await subscribe_plugin.poll_subscriptions()

    assert calls == ["healthy-author"]


def test_extract_reply_id_and_message_text() -> None:
    message = Message([MessageSegment.reply(800001)])

    assert subscribe_plugin._extract_reply_id(message) == "800001"
    assert (
        subscribe_plugin._extract_message_text(
            [
                {"type": "text", "data": {"text": "抖音作品 "}},
                {"type": "image", "data": {"url": "https://example.com/a.jpg"}},
                {"type": "text", "data": {"text": "https://v.douyin.com/abc/"}},
            ]
        )
        == "抖音作品 https://v.douyin.com/abc/"
    )


@pytest.mark.asyncio
async def test_get_replied_message_text_calls_get_msg() -> None:
    class FakeEvent:
        def get_message(self) -> Message:
            return Message([MessageSegment.reply(800001)])

    class FakeBot:
        async def call_api(self, api: str, **data: object) -> object:
            assert api == "get_msg"
            assert data == {"message_id": "800001"}
            return {
                "message": [
                    {
                        "type": "text",
                        "data": {"text": "https://v.douyin.com/abc123/"},
                    }
                ]
            }

    assert (
        await subscribe_plugin._get_replied_message_text(
            cast(Event, FakeEvent()), cast(Bot, FakeBot())
        )
        == "https://v.douyin.com/abc123/"
    )


@pytest.mark.asyncio
async def test_get_replied_message_text_uses_onebot_reply_field() -> None:
    class FakeReply:
        message = Message("https://v.douyin.com/abc123/")

    class FakeEvent:
        reply = FakeReply()

        def get_message(self) -> Message:
            return Message()

    class FakeBot:
        async def call_api(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("不应再次调用 get_msg")

    assert (
        await subscribe_plugin._get_replied_message_text(
            cast(Event, FakeEvent()), cast(Bot, FakeBot())
        )
        == "https://v.douyin.com/abc123/"
    )


@pytest.mark.asyncio
async def test_notify_subscribers_mentions_all_subscriber_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[list[object]] = []

    class FakeMessage:
        def __init__(self, *parts: object) -> None:
            self.parts = list(parts)

        def __iadd__(self, other: object) -> "FakeMessage":
            if isinstance(other, FakeMessage):
                self.parts.extend(other.parts)
            else:
                self.parts.append(other)
            return self

        async def send(self, **_kwargs: object) -> None:
            sent.append(self.parts)

    class FakeUniMessage:
        def __new__(cls) -> FakeMessage:
            return FakeMessage()

        @classmethod
        def at(cls, user_id: str) -> FakeMessage:
            return FakeMessage(("at", user_id))

    monkeypatch.setattr(subscribe_plugin, "UniMessage", FakeUniMessage)

    await subscribe_plugin._notify_subscribers(Target.group("87654321"), ["111", "222"])

    assert sent == [[("at", "111"), ("at", "222"), " 你订阅的爱播更新了"]]


def test_format_update_time() -> None:
    timestamp = int(datetime(2026, 8, 25, 12, 34).timestamp())

    assert subscribe_plugin._format_update_time(timestamp) == "08月25日 12:34"
    assert subscribe_plugin._format_update_time(0) == "未知"


def test_exponential_backoff_is_capped() -> None:
    delays = [
        subscribe_plugin._exponential_backoff_seconds(
            attempt,
            base_seconds=30,
            max_seconds=300,
        )
        for attempt in range(1, 7)
    ]

    assert delays == [30, 60, 120, 240, 300, 300]


@pytest.mark.asyncio
async def test_get_author_metadata_falls_back_to_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = douyin.DouyinWork(
        "100", "https://www.douyin.com/video/100", "", 100, "作品昵称"
    )
    fetch_profile = AsyncMock(return_value=("资料昵称", "douyin_id"))
    monkeypatch.setattr(subscribe_plugin, "fetch_author_profile", fetch_profile)

    result = await subscribe_plugin._get_author_metadata("sec-user-id", work)

    assert result == ("资料昵称", "douyin_id")
    fetch_profile.assert_awaited_once_with("sec-user-id")


@pytest.mark.asyncio
async def test_get_author_metadata_uses_cached_unique_id_without_profile_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = douyin.DouyinWork(
        "100", "https://www.douyin.com/video/100", "", 100, "作品昵称"
    )
    fetch_profile = AsyncMock()
    monkeypatch.setattr(subscribe_plugin, "fetch_author_profile", fetch_profile)

    result = await subscribe_plugin._get_author_metadata(
        "sec-user-id", work, "cached_id"
    )

    assert result == ("作品昵称", "cached_id")
    fetch_profile.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_subscriptions_uses_cached_json_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SubscriptionStore(tmp_path)
    monkeypatch.setattr(subscribe_plugin, "store", store)
    target = Target.group("87654321")
    await store.upsert_target(
        "user-id",
        "呆呆丸",
        subscribe_plugin._target_key(target),
        target.dump(),
        "100",
        "douyingid",
        int(datetime(2026, 8, 25, 0, 20).timestamp()),
    )

    class FakeEvent:
        pass

    class FakeMessage:
        def __init__(self, message: str) -> None:
            self.message = message

        async def send(self, **_kwargs: object) -> None:
            sent.append(self.message)

    class FakeUniMessage:
        def __new__(cls, message: str) -> FakeMessage:
            return FakeMessage(message)

    sent: list[str] = []
    monkeypatch.setattr(subscribe_plugin, "get_target", lambda *_args: target)
    monkeypatch.setattr(subscribe_plugin, "UniMessage", FakeUniMessage)

    async def fail_if_requested(_sec_user_id: str) -> list[douyin.DouyinWork]:
        raise AssertionError("订阅列表不应发起网络请求")

    monkeypatch.setattr(subscribe_plugin, "fetch_latest_works", fail_if_requested)

    await subscribe_plugin.list_subscriptions(FakeEvent(), cast(Bot, object()))

    assert sent == [
        "当前会话的抖音订阅：\n"
        "序号 昵称（抖音号） 最近更新\n"
        "1. 呆呆丸（@douyingid）08月25日 00:20"
    ]
