from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot_plugin_alconna.uniseg import Target

from nonebot_plugin_ifollow import polling
from nonebot_plugin_ifollow.models import (
    AuthorSubscription,
    SubscriptionStore,
    TargetSubscription,
    make_target_key,
)
from nonebot_plugin_ifollow.platforms import AuthorInfo, Cursor, PlatformWork


class ContentUnavailableError(Exception):
    """与 parsehub 同名的作品不可用异常，用来验证按类名分类。"""


def make_work(
    work_id: str,
    timestamp: int = 0,
    *,
    bvid: str = "",
    kind: str = "video",
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


class FakeMessage:
    """只记录发送目标的消息替身。"""

    def __init__(
        self,
        work: PlatformWork,
        sent: list[str],
        fail_for: str | None,
        error: Exception,
    ) -> None:
        self.work = work
        self.sent = sent
        self.fail_for = fail_for
        self.error = error

    async def send(self, *, target: Target) -> None:
        if self.fail_for is not None and target.id == self.fail_for:
            raise self.error
        self.sent.append(self.work.work_id)


def patch_media(
    monkeypatch: pytest.MonkeyPatch,
    sent: list[str],
    *,
    error: Exception | None = None,
    fail_for: str | None = None,
    fail_works: set[str] | None = None,
) -> list[str]:
    """替换解析下载上下文，返回「准备过哪些作品」的记录。

    ``fail_for`` 让指定目标的发送失败，``fail_works`` 让指定作品的解析失败。
    """

    prepared: list[str] = []

    @asynccontextmanager
    async def fake_media(work: PlatformWork) -> AsyncIterator[list[FakeMessage]]:
        prepared.append(work.work_id)
        if (
            error is not None
            and fail_for is None
            and (fail_works is None or work.work_id in fail_works)
        ):
            raise error
        yield [FakeMessage(work, sent, fail_for, error or RuntimeError())]

    monkeypatch.setattr(polling, "_media_for", fake_media)
    return prepared


@pytest.fixture(autouse=True)
def reset_runtime() -> None:
    polling._states.clear()
    polling._work_attempts.clear()
    polling._permanent_targets.clear()
    polling._in_flight.clear()
    polling._recent_abandoned.clear()
    polling._stats = polling.RoundStats()
    polling._last_round_at = 0.0


async def make_author(
    tmp_path: Path,
    *targets: str,
    cursors: dict[str, Cursor] | None = None,
) -> tuple[SubscriptionStore, AuthorSubscription, list[str]]:
    """建立一个作者，返回存储、作者对象和每个目标的键。"""

    store = SubscriptionStore(tmp_path)
    cursors = cursors or {"video": Cursor("old", 1)}
    target_states: dict[str, TargetSubscription] = {}
    keys: list[str] = []
    for name in targets:
        target = Target.group(name)
        target_key = make_target_key(target.dump())
        keys.append(target_key)
        target_states[target_key] = TargetSubscription(target.dump(), dict(cursors))
        await store.upsert_target(
            "bilibili", "123", "作者", target_key, target.dump(), dict(cursors)
        )
    author = AuthorSubscription("bilibili", "123", "作者", target_states)
    return store, author, keys


def cursor_of(
    snapshot: dict[str, AuthorSubscription], target_key: str, feed: str = "video"
) -> Cursor | None:
    return snapshot["bilibili:123"].targets[target_key].cursors.get(feed)


@pytest.mark.asyncio
async def test_deliver_feed_sends_oldest_first_and_advances_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(tmp_path, "group")
    sent: list[str] = []
    prepared = patch_media(monkeypatch, sent)
    works = [make_work("new-2", 4), make_work("new-1", 3), make_work("old", 1)]

    await polling._deliver_feed(store, author, "video", works, {target_key: set()})

    assert sent == ["new-1", "new-2"]
    assert prepared == ["new-1", "new-2"]
    assert cursor_of(await store.snapshot(), target_key) == Cursor("new-2", 4)


@pytest.mark.asyncio
async def test_work_is_prepared_once_for_all_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (first, second) = await make_author(tmp_path, "group-a", "group-b")
    sent: list[str] = []
    prepared = patch_media(monkeypatch, sent)

    await polling._deliver_feed(
        store, author, "video", [make_work("new", 2)], {first: set(), second: set()}
    )

    assert prepared == ["new"]
    assert sent == ["new", "new"]
    snapshot = await store.snapshot()
    assert cursor_of(snapshot, first) == Cursor("new", 2)
    assert cursor_of(snapshot, second) == Cursor("new", 2)


@pytest.mark.asyncio
async def test_unavailable_work_is_abandoned_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(tmp_path, "group")
    patch_media(
        monkeypatch,
        [],
        error=ContentUnavailableError("作品不可用: 视频已删除"),
    )

    await polling._deliver_feed(
        store,
        author,
        "video",
        [make_work("new", 3), make_work("old", 1)],
        {target_key: set()},
    )

    assert cursor_of(await store.snapshot(), target_key) == Cursor("new", 3)
    assert polling._stats.abandoned == 1
    assert polling._work_attempts == {}


@pytest.mark.asyncio
async def test_transient_failure_retries_then_abandons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(tmp_path, "group")
    patch_media(monkeypatch, [], error=RuntimeError("network down"))
    works = [make_work("new", 3), make_work("old", 1)]

    for _ in range(polling.WORK_RETRY_LIMIT - 1):
        await polling._deliver_feed(store, author, "video", works, {target_key: set()})
        assert cursor_of(await store.snapshot(), target_key) == Cursor("old", 1)
        assert polling._stats.abandoned == 0

    await polling._deliver_feed(store, author, "video", works, {target_key: set()})

    assert cursor_of(await store.snapshot(), target_key) == Cursor("new", 3)
    assert polling._stats.abandoned == 1


@pytest.mark.asyncio
async def test_kicked_group_only_skips_that_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (kicked_key, healthy_key) = await make_author(
        tmp_path, "kicked", "healthy"
    )
    kicked = ActionFailed(
        retcode=1200, message="发送失败，你已被移出该群，请重新加群。"
    )
    sent: list[str] = []
    patch_media(monkeypatch, sent, error=kicked, fail_for="kicked")

    await polling._deliver_feed(
        store,
        author,
        "video",
        [make_work("new", 3), make_work("old", 1)],
        {kicked_key: set(), healthy_key: set()},
    )

    snapshot = await store.snapshot()
    assert cursor_of(snapshot, kicked_key) == Cursor("old", 1)
    assert cursor_of(snapshot, healthy_key) == Cursor("new", 3)
    assert sent == ["new"]
    assert polling._permanent_targets == {kicked_key}


def test_unseen_uses_publish_time_when_cursor_work_is_gone() -> None:
    works = [
        make_work("new", 4),
        make_work("mid", 3),
        make_work("deleted", 2),
        make_work("older", 1),
    ]

    assert polling._unseen(works, Cursor("deleted", 2)) == [
        make_work("mid", 3),
        make_work("new", 4),
    ]


def test_unseen_falls_back_to_newest_one_without_time() -> None:
    works = [make_work("new", 4), make_work("mid", 3), make_work("old", 2)]

    assert polling._unseen(works, Cursor("missing", 0)) == [make_work("new", 4)]


@pytest.mark.asyncio
async def test_dynamic_video_is_deduplicated_by_bvid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(
        tmp_path, "group", cursors={"dynamic": Cursor("old", 1)}
    )
    sent: list[str] = []
    prepared = patch_media(monkeypatch, sent)
    work = make_work("dynamic-new", 3, bvid="BVsame")

    await polling._deliver_feed(
        store, author, "dynamic", [work], {target_key: {"BVsame"}}
    )

    assert sent == []
    assert prepared == []
    assert cursor_of(await store.snapshot(), target_key, "dynamic") == Cursor(
        "dynamic-new", 3
    )


@pytest.mark.asyncio
async def test_poll_round_only_runs_due_authors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, _ = await make_author(tmp_path, "group")
    called: list[str] = []

    async def fake_poll(_store: SubscriptionStore, item: AuthorSubscription) -> None:
        called.append(item.key)

    monkeypatch.setattr(polling, "_poll_author", fake_poll)
    polling._state(author.key).next_poll_at = 0.0

    await polling._poll_round(store)

    assert called == [author.key]

    called.clear()
    polling._state(author.key).next_poll_at = float("inf")
    await polling._poll_round(store)

    assert called == []


@pytest.mark.asyncio
async def test_poll_author_failure_backs_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, author, _ = await make_author(tmp_path, "group")

    async def failing(_author: AuthorSubscription) -> dict[str, list[PlatformWork]]:
        raise polling.DouyinRequestError("接口被风控拦截", retryable=True)

    monkeypatch.setattr(polling, "_fetch_feeds", failing)

    await polling._poll_author(SubscriptionStore(tmp_path), author)

    state = polling._state(author.key)
    assert state.failure_count == 1
    assert state.next_poll_at > 0
    assert "风控" in state.last_error


@pytest.mark.asyncio
async def test_poll_author_success_resets_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, author, _ = await make_author(tmp_path, "group")
    polling._state(author.key).failure_count = 2

    async def empty(_author: AuthorSubscription) -> dict[str, list[PlatformWork]]:
        return {}

    monkeypatch.setattr(polling, "_fetch_feeds", empty)

    await polling._poll_author(SubscriptionStore(tmp_path), author)

    state = polling._state(author.key)
    assert state.failure_count == 0
    assert state.last_success_at > 0


def test_backoff_is_capped() -> None:
    delays = [
        polling._exponential_backoff_seconds(attempt, base_seconds=30, max_seconds=300)
        for attempt in range(1, 7)
    ]
    assert delays == [30, 60, 120, 240, 300, 300]


def test_permanent_target_error_detects_kicked_group() -> None:
    kicked = ActionFailed(
        retcode=1200,
        message="发送消息失败 (code=110): 发送失败，你已被移出该群，请重新加群。",
    )
    missing = ActionFailed(retcode=100, message="群不存在")

    assert polling._is_permanent_target_error(kicked)
    assert polling._is_permanent_target_error(missing)
    assert not polling._is_permanent_target_error(RuntimeError("boom"))


def test_permanent_work_error_covers_unavailable_fallback() -> None:
    class ContentUnavailableFallbackError(ContentUnavailableError, RuntimeError):
        """模拟 parsehub 的双继承异常。"""

    class UnknownPlatform(Exception):
        """模拟 parsehub 的不支持平台异常。"""

    assert polling._is_permanent_work_error(ContentUnavailableError("已删除"))
    assert polling._is_permanent_work_error(ContentUnavailableFallbackError("全部失败"))
    assert polling._is_permanent_work_error(UnknownPlatform("x"))
    assert not polling._is_permanent_work_error(RuntimeError("network"))


def test_status_text_lists_failing_authors() -> None:
    target = Target.group("group")
    target_key = make_target_key(target.dump())
    author = AuthorSubscription(
        "bilibili", "123", "作者", {target_key: TargetSubscription(target.dump())}
    )
    polling._states[author.key] = polling.AuthorPollState(
        next_poll_at=float("inf"),
        failure_count=3,
        last_error="B 站 APP 接口返回 -509",
    )

    text = polling.status_text({author.key: author})

    assert "上次轮询：尚未运行" in text
    assert "连续失败 3 次" in text
    assert "-509" in text


@pytest.mark.asyncio
async def test_deliver_author_refreshes_latest_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(tmp_path, "group")
    patch_media(monkeypatch, [])

    await polling._deliver_author(store, author, {"video": [make_work("new", 5)]})

    snapshot = await store.snapshot()
    assert snapshot["bilibili:123"].last_update_time == 5
    assert cursor_of(snapshot, target_key) == Cursor("new", 5)


@pytest.mark.asyncio
async def test_abandoned_work_does_not_block_newer_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一个作品坏掉时，更新的作品在同一轮里照样发出去。"""

    store, author, (target_key,) = await make_author(tmp_path, "group")
    sent: list[str] = []
    patch_media(
        monkeypatch,
        sent,
        error=ContentUnavailableError("作品不可用: 视频已删除"),
        fail_works={"bad"},
    )

    await polling._deliver_feed(
        store,
        author,
        "video",
        [make_work("new", 3), make_work("bad", 2), make_work("old", 1)],
        {target_key: set()},
    )

    assert sent == ["new"]
    assert cursor_of(await store.snapshot(), target_key) == Cursor("new", 3)
    assert polling._stats.abandoned == 1


def test_tick_scales_with_author_period(monkeypatch: pytest.MonkeyPatch) -> None:
    """扫描间隔跟着作者周期伸缩，不写死成小常量。"""

    monkeypatch.setattr(polling.config, "ifollow_subscribe_interval", 1800)
    assert polling.tick_seconds() == polling.TICK_MAX_SECONDS

    monkeypatch.setattr(polling.config, "ifollow_subscribe_interval", 300)
    assert polling.tick_seconds() == 75

    monkeypatch.setattr(polling.config, "ifollow_subscribe_interval", 30)
    assert polling.tick_seconds() == polling.TICK_MIN_SECONDS
