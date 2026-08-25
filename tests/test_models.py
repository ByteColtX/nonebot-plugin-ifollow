import json
from pathlib import Path

import pytest

from nonebot_plugin_ifollow.models import SubscriptionStore, make_target_key


@pytest.mark.asyncio
async def test_multi_platform_round_trip_and_independent_cursors(
    tmp_path: Path,
) -> None:
    store = SubscriptionStore(tmp_path)
    target = {"id": "87654321", "private": False}
    target_key = make_target_key(target)

    await store.upsert_target(
        "douyin",
        "same-id",
        "抖音作者",
        target_key,
        target,
        {"video": "aweme-1"},
        "douyin-id",
        100,
    )
    await store.upsert_target(
        "bilibili",
        "same-id",
        "B站作者",
        target_key,
        target,
        {"video": "BV1", "dynamic": "dynamic-1"},
        "same-id",
        200,
    )

    stored = json.loads((tmp_path / "subscriptions.json").read_text(encoding="utf-8"))
    assert stored["version"] == 1
    assert len(stored["subscriptions"]) == 2

    loaded = SubscriptionStore(tmp_path)
    snapshot = await loaded.snapshot()
    assert snapshot["douyin:same-id"].targets[target_key].cursors == {
        "video": "aweme-1"
    }
    assert snapshot["bilibili:same-id"].targets[target_key].cursors == {
        "video": "BV1",
        "dynamic": "dynamic-1",
    }

    await loaded.update_cursor(
        "bilibili", "same-id", target_key, "dynamic", "dynamic-2"
    )
    assert (await loaded.snapshot())["bilibili:same-id"].targets[target_key].cursors[
        "dynamic"
    ] == "dynamic-2"


@pytest.mark.asyncio
async def test_add_target_and_subscribers(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    target = {"id": "group"}
    target_key = make_target_key(target)
    assert await store.add_target(
        "bilibili",
        "123",
        "作者",
        target_key,
        target,
        {"video": "BV1"},
        subscriber_id="111",
    )
    assert not await store.add_target(
        "bilibili", "123", "作者", target_key, target, {"video": "BV2"}
    )
    assert await store.add_subscriber("bilibili", "123", target_key, "222")
    assert not await store.add_subscriber("bilibili", "123", target_key, "222")

    assert await store.remove_subscriber("bilibili", "123", target_key, "111") == (
        True,
        False,
    )
    assert await store.remove_subscriber("bilibili", "123", target_key, "222") == (
        True,
        True,
    )
    assert await store.snapshot() == {}


@pytest.mark.asyncio
async def test_old_douyin_storage_is_not_migrated(tmp_path: Path) -> None:
    (tmp_path / "subscriptions.json").write_text(
        json.dumps({"version": 2, "authors": []}), encoding="utf-8"
    )
    assert await SubscriptionStore(tmp_path).snapshot() == {}
