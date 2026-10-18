from pathlib import Path

import pytest

from nonebot_plugin_ifollow.models import (
    STORAGE_VERSION,
    AuthorSubscription,
    SubscriptionStore,
    make_target_key,
)
from nonebot_plugin_ifollow.platforms import Cursor


def target() -> dict[str, object]:
    return {"id": "group-1", "channel": True, "scope": "group"}


def test_make_target_key_is_stable_and_ignores_defaults() -> None:
    first = make_target_key({"id": "g", "channel": True})
    second = make_target_key({"id": "g", "channel": True, "private": False})

    assert first == second
    assert first != make_target_key({"id": "g", "private": True})


@pytest.mark.asyncio
async def test_store_roundtrip_and_flush(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    target_key = make_target_key(target())
    await store.add_target(
        "douyin",
        "sec",
        "作者",
        target_key,
        target(),
        {"video": Cursor("w1", 100)},
        subscriber_id="42",
        author_handle="handle",
        last_update_time=100,
    )
    await store.flush()

    reloaded = SubscriptionStore(tmp_path)
    snapshot = await reloaded.snapshot()
    author = snapshot["douyin:sec"]

    assert author.author_name == "作者"
    assert author.author_handle == "handle"
    assert author.targets[target_key].cursors["video"] == Cursor("w1", 100)
    assert author.targets[target_key].subscriber_ids == ["42"]


@pytest.mark.asyncio
async def test_cursor_updates_are_batched_until_flush(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    target_key = make_target_key(target())
    await store.add_target(
        "bilibili", "1", "作者", target_key, target(), {"video": Cursor("w1", 1)}
    )
    await store.flush()
    path = tmp_path / "subscriptions.json"
    before = path.read_text(encoding="utf-8")

    await store.update_cursor("bilibili", "1", target_key, "video", Cursor("w2", 2))
    await store.update_cursor("bilibili", "1", target_key, "video", Cursor("w3", 3))

    assert path.read_text(encoding="utf-8") == before

    await store.flush()

    assert "w3" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_snapshot_is_a_deep_copy(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    target_key = make_target_key(target())
    await store.add_target(
        "bilibili", "1", "作者", target_key, target(), {"video": Cursor("w1", 1)}
    )

    snapshot = await store.snapshot()
    snapshot["bilibili:1"].targets[target_key].cursors["video"] = Cursor("hack", 9)

    fresh = await store.snapshot()
    assert fresh["bilibili:1"].targets[target_key].cursors["video"] == Cursor("w1", 1)


@pytest.mark.asyncio
async def test_add_target_rejects_duplicate(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    target_key = make_target_key(target())
    await store.add_target("bilibili", "1", "作者", target_key, target(), {})

    assert not await store.add_target("bilibili", "1", "作者", target_key, target(), {})


@pytest.mark.asyncio
async def test_remove_subscriber_drops_target_when_last_one_leaves(
    tmp_path: Path,
) -> None:
    store = SubscriptionStore(tmp_path)
    target_key = make_target_key(target())
    await store.add_target(
        "bilibili", "1", "作者", target_key, target(), {}, subscriber_id="42"
    )

    assert await store.remove_subscriber("bilibili", "1", target_key, "42") == (
        True,
        True,
    )
    assert await store.snapshot() == {}


@pytest.mark.asyncio
async def test_remove_subscriber_keeps_target_for_others(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    target_key = make_target_key(target())
    await store.add_target(
        "bilibili", "1", "作者", target_key, target(), {}, subscriber_id="42"
    )
    await store.add_subscriber("bilibili", "1", target_key, "43")

    assert await store.remove_subscriber("bilibili", "1", target_key, "42") == (
        True,
        False,
    )
    snapshot = await store.snapshot()
    assert snapshot["bilibili:1"].targets[target_key].subscriber_ids == ["43"]


@pytest.mark.asyncio
async def test_unknown_version_falls_back_to_empty_store(tmp_path: Path) -> None:
    (tmp_path / "subscriptions.json").write_text(
        '{"version": 2, "subscriptions": []}', encoding="utf-8"
    )

    store = SubscriptionStore(tmp_path)

    assert await store.snapshot() == {}


@pytest.mark.asyncio
async def test_corrupt_json_falls_back_to_empty_store(tmp_path: Path) -> None:
    (tmp_path / "subscriptions.json").write_text("{not json", encoding="utf-8")

    store = SubscriptionStore(tmp_path)

    assert await store.snapshot() == {}


@pytest.mark.asyncio
async def test_author_metadata_is_updated_once(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    target_key = make_target_key(target())
    await store.add_target("bilibili", "1", "旧名", target_key, target(), {})

    await store.update_author_metadata("bilibili", "1", "新名", "handle", 100)
    await store.flush()
    snapshot = await store.snapshot()

    assert snapshot["bilibili:1"].author_name == "新名"
    assert snapshot["bilibili:1"].author_handle == "handle"
    assert snapshot["bilibili:1"].last_update_time == 100


@pytest.mark.asyncio
async def test_transaction_flushes_on_exit(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    target_key = make_target_key(target())

    async with store.transaction():
        await store.add_target("bilibili", "1", "作者", target_key, target(), {})

    assert (tmp_path / "subscriptions.json").exists()


def test_storage_version_is_current() -> None:
    assert STORAGE_VERSION == 3


def test_author_key_is_namespaced() -> None:
    author = AuthorSubscription("douyin", "sec", "作者")

    assert author.key == "douyin:sec"


@pytest.mark.asyncio
async def test_transaction_flush_failure_keeps_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """磁盘写入失败不能盖掉业务异常，且改动保持脏状态以便重试。"""

    store = SubscriptionStore(tmp_path)
    writes: list[str] = []

    def boom(text: str) -> None:
        writes.append(text)
        raise OSError("磁盘已满")

    monkeypatch.setattr(store, "_write", boom)

    async def failing_transaction() -> None:
        async with store.transaction():
            await store.add_target("bilibili", "1", "作者", "k", {"id": "g"}, {})
            raise ValueError("业务异常")

    with pytest.raises(ValueError, match="业务异常"):
        await failing_transaction()

    monkeypatch.setattr(store, "_write", lambda text: writes.append(text))
    await store.flush()

    assert len(writes) == 2
