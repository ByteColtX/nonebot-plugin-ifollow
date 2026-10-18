import json
from pathlib import Path

import pytest

from nonebot_plugin_ifollow.models import SubscriptionStore, make_target_key
from nonebot_plugin_ifollow.platforms import Cursor


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
        {"video": Cursor("aweme-1", 100)},
        "douyin-id",
        100,
    )
    await store.upsert_target(
        "bilibili",
        "same-id",
        "B站作者",
        target_key,
        target,
        {"video": Cursor("BV1", 200), "dynamic": Cursor("dynamic-1", 200)},
        "same-id",
        200,
    )

    stored = json.loads((tmp_path / "subscriptions.json").read_text(encoding="utf-8"))
    assert stored["version"] == 2
    assert len(stored["subscriptions"]) == 2
    assert stored["subscriptions"][0]["targets"][0]["cursors"] == {
        "video": {"id": "aweme-1", "time": 100}
    }

    loaded = SubscriptionStore(tmp_path)
    snapshot = await loaded.snapshot()
    assert snapshot["douyin:same-id"].targets[target_key].cursors == {
        "video": Cursor("aweme-1", 100)
    }
    assert snapshot["bilibili:same-id"].targets[target_key].cursors == {
        "video": Cursor("BV1", 200),
        "dynamic": Cursor("dynamic-1", 200),
    }

    await loaded.update_cursor(
        "bilibili", "same-id", target_key, "dynamic", Cursor("dynamic-2", 300)
    )
    assert (await loaded.snapshot())["bilibili:same-id"].targets[target_key].cursors[
        "dynamic"
    ] == Cursor("dynamic-2", 300)


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
        {"video": Cursor("BV1", 1)},
        subscriber_id="111",
    )
    assert not await store.add_target(
        "bilibili",
        "123",
        "作者",
        target_key,
        target,
        {"video": Cursor("BV2", 2)},
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
async def test_v1_string_cursors_are_migrated_on_write(tmp_path: Path) -> None:
    """v1 的字符串游标读入后补上 ``time=0``，写回时升级成 v2 结构。"""

    target = {"id": "group"}
    target_key = make_target_key(target)
    (tmp_path / "subscriptions.json").write_text(
        json.dumps(
            {
                "version": 1,
                "subscriptions": [
                    {
                        "platform": "bilibili",
                        "author_id": "123",
                        "name": "作者",
                        "targets": [
                            {
                                "target": target,
                                "cursors": {"video": "BV1", "dynamic": None},
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    store = SubscriptionStore(tmp_path)
    snapshot = await store.snapshot()

    assert snapshot["bilibili:123"].targets[target_key].cursors == {
        "video": Cursor("BV1", 0)
    }

    await store.update_cursor("bilibili", "123", target_key, "video", Cursor("BV2", 9))

    stored = json.loads((tmp_path / "subscriptions.json").read_text(encoding="utf-8"))
    assert stored["version"] == 2
    assert stored["subscriptions"][0]["targets"][0]["cursors"] == {
        "video": {"id": "BV2", "time": 9}
    }


@pytest.mark.asyncio
async def test_unchanged_metadata_does_not_rewrite_file(tmp_path: Path) -> None:
    """内容没变时不落盘：每轮 34 次空写是 SD 卡的写入来源。"""

    store = SubscriptionStore(tmp_path)
    target = {"id": "group"}
    target_key = make_target_key(target)
    await store.add_target(
        "bilibili",
        "123",
        "作者",
        target_key,
        target,
        {"video": Cursor("BV1", 1)},
        author_handle="123",
        last_update_time=5,
    )
    path = tmp_path / "subscriptions.json"
    before = path.stat().st_ino

    await store.update_author_metadata("bilibili", "123", "作者", "123", 5)

    assert path.stat().st_ino == before

    await store.update_author_metadata("bilibili", "123", "作者", "123", 6)

    assert path.stat().st_ino != before


@pytest.mark.asyncio
async def test_legacy_authors_storage_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "subscriptions.json").write_text(
        json.dumps({"version": 2, "authors": []}), encoding="utf-8"
    )
    assert await SubscriptionStore(tmp_path).snapshot() == {}
