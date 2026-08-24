from pathlib import Path

import pytest

from nonebot_plugin_douyin_subscribe.models import SubscriptionStore


@pytest.mark.asyncio
async def test_subscription_store_round_trip_and_remove(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    target = {"id": "87654321", "private": False}

    await store.upsert_target("user-id", "作者", "group-key", target, "100")

    loaded = SubscriptionStore(tmp_path)
    snapshot = await loaded.snapshot()
    assert snapshot["user-id"].author_name == "作者"
    assert snapshot["user-id"].targets["group-key"].last_aweme_id == "100"
    assert snapshot["user-id"].targets["group-key"].target == target

    await loaded.update_cursor("user-id", "group-key", "101")
    assert (await loaded.snapshot())["user-id"].targets[
        "group-key"
    ].last_aweme_id == "101"

    assert await loaded.remove_target("user-id", "group-key")
    assert await loaded.snapshot() == {}
    assert not await loaded.remove_target("user-id", "group-key")
