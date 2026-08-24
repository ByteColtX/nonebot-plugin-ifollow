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


def test_format_update_time() -> None:
    timestamp = int(datetime(2026, 8, 25, 12, 34).timestamp())

    assert subscribe_plugin._format_update_time(timestamp) == "08月25日 12:34"
    assert subscribe_plugin._format_update_time(0) == "未知"
