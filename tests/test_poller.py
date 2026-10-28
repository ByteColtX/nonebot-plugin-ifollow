from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from nonebot.adapters.onebot.v11.exception import ActionFailed
from nonebot_plugin_alconna.uniseg import Target

from nonebot_plugin_ifollow import delivery
from nonebot_plugin_ifollow.errors import PermanentError, TransientError
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


class ContentUnavailableError(Exception):
    """与 parsehub 同名的作品不可用异常，用来验证按类名分类。"""


def make_work(
    work_id: str, timestamp: int = 0, *, bvid: str = "", kind: str = "video"
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
    async def fake_media(
        work: PlatformWork, _settings: Settings, _author_name: str = ""
    ) -> AsyncIterator[list[FakeMessage]]:
        prepared.append(work.work_id)
        if (
            error is not None
            and fail_for is None
            and (fail_works is None or work.work_id in fail_works)
        ):
            raise error
        yield [FakeMessage(work, sent, fail_for, error or RuntimeError())]

    monkeypatch.setattr(delivery, "media_for", fake_media)
    return prepared


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
        await store.add_target(
            "bilibili", "123", "作者", target_key, target.dump(), dict(cursors)
        )
    author = AuthorSubscription("bilibili", "123", "作者", target_states)
    return store, author, keys


def cursor_of(
    snapshot: dict[str, AuthorSubscription], target_key: str, feed: str = "video"
) -> Cursor | None:
    return snapshot["bilibili:123"].targets[target_key].cursors.get(feed)


class FakeAdapter:
    """可控的平台适配器替身。"""

    platform = "bilibili"

    def __init__(self, works: list[PlatformWork] | None = None) -> None:
        self.works = works or []
        self.error: Exception | None = None
        self.calls: list[tuple[str, str, Cursor | None]] = []

    def matches(self, _value: str) -> bool:
        return True

    def feeds(self, settings: Settings) -> tuple[str, ...]:
        return ("video",)

    async def resolve(self, _settings: Settings, _value: str) -> ResolvedAuthor:
        return ResolvedAuthor(AuthorInfo("123", "作者", "123"), {"video": self.works})

    async def fetch(
        self,
        _settings: Settings,
        author_id: str,
        feed: str,
        cursor: Cursor | None = None,
    ) -> list[PlatformWork]:
        self.calls.append((author_id, feed, cursor))
        if self.error is not None:
            raise self.error
        return list(self.works)


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> FakeAdapter:
    fake = FakeAdapter()
    monkeypatch.setattr(
        "nonebot_plugin_ifollow.poller.adapter_for", lambda _platform: fake
    )
    return fake


@pytest.mark.asyncio
async def test_deliver_feed_sends_oldest_first_and_advances_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(tmp_path, "group")
    sent: list[str] = []
    prepared = patch_media(monkeypatch, sent)
    poller = Poller(store, SETTINGS)
    works = [make_work("new-2", 4), make_work("new-1", 3), make_work("old", 1)]

    await delivery.deliver_feed(poller.context, author, "video", works)

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
    poller = Poller(store, SETTINGS)

    await delivery.deliver_feed(poller.context, author, "video", [make_work("new", 2)])

    assert prepared == ["new"]
    assert sent == ["new", "new"]
    snapshot = await store.snapshot()
    assert cursor_of(snapshot, first) == Cursor("new", 2)
    assert cursor_of(snapshot, second) == Cursor("new", 2)


@pytest.mark.asyncio
async def test_catch_up_is_capped_to_newest_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(tmp_path, "group")
    sent: list[str] = []
    patch_media(monkeypatch, sent)
    poller = Poller(store, SETTINGS)
    works = [make_work(f"w{index}", 10 + index) for index in range(6)][::-1]

    await delivery.deliver_feed(poller.context, author, "video", works)

    assert sent == ["w1", "w2", "w3", "w4", "w5"]
    assert cursor_of(await store.snapshot(), target_key) == Cursor("w5", 15)


@pytest.mark.asyncio
async def test_unavailable_work_is_abandoned_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(tmp_path, "group")
    sent: list[str] = []
    patch_media(monkeypatch, sent, error=ContentUnavailableError("作品已删除"))
    poller = Poller(store, SETTINGS)

    await delivery.deliver_feed(poller.context, author, "video", [make_work("new", 2)])

    assert sent == []
    assert cursor_of(await store.snapshot(), target_key) == Cursor("new", 2)
    assert poller.context.stats.abandoned == 1


@pytest.mark.asyncio
async def test_transient_failure_retries_then_abandons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(tmp_path, "group")
    sent: list[str] = []
    patch_media(monkeypatch, sent, error=TransientError("下载失败"))
    poller = Poller(store, SETTINGS)

    for _ in range(3):
        await delivery.deliver_feed(
            poller.context, author, "video", [make_work("new", 2)]
        )

    assert sent == []
    assert poller.context.attempts == {}
    assert cursor_of(await store.snapshot(), target_key) == Cursor("new", 2)


@pytest.mark.asyncio
async def test_successful_retry_clears_attempt_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(tmp_path, "group")
    sent: list[str] = []
    patch_media(monkeypatch, sent)
    poller = Poller(store, SETTINGS)
    work = make_work("new", 2)
    poller.context.attempts[delivery.attempt_key(author, target_key, "video", work)] = 2

    await delivery.deliver_feed(poller.context, author, "video", [work])

    assert sent == ["new"]
    assert poller.context.attempts == {}


@pytest.mark.asyncio
async def test_kicked_group_only_skips_that_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (first, second) = await make_author(tmp_path, "kicked", "ok")
    sent: list[str] = []
    kicked = Target.group("kicked")
    error = ActionFailed(retcode=100, msg="机器人已被移出该群")
    patch_media(monkeypatch, sent, error=error, fail_for=kicked.id)
    poller = Poller(store, SETTINGS)

    await delivery.deliver_feed(poller.context, author, "video", [make_work("new", 2)])

    assert sent == ["new"]
    snapshot = await store.snapshot()
    assert cursor_of(snapshot, first) == Cursor("old", 1)
    assert cursor_of(snapshot, second) == Cursor("new", 2)
    assert first in poller.context.permanent_targets


class FakeBot:
    """只实现群列表与好友列表的机器人替身。"""

    def __init__(
        self,
        groups: set[str],
        friends: set[str],
        error: Exception | None = None,
    ) -> None:
        self.groups = groups
        self.friends = friends
        self.error = error
        self.calls = 0

    async def get_group_list(self) -> list[dict[str, str]]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [{"group_id": group} for group in self.groups]

    async def get_friend_list(self) -> list[dict[str, str]]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [{"user_id": friend} for friend in self.friends]


@pytest.mark.asyncio
async def test_round_skips_dead_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter: FakeAdapter
) -> None:
    """退群和私聊非好友都跳过，且只扫描一次。"""

    store = SubscriptionStore(tmp_path)
    targets = {
        "alive": Target.group("alive"),
        "gone": Target.group("gone"),
        "private": Target.user("42"),
    }
    for target in targets.values():
        await store.add_target(
            "bilibili",
            "123",
            "作者",
            make_target_key(target.dump()),
            target.dump(),
            {"video": Cursor("old", 1)},
        )
    adapter.works = [make_work("new", 2)]
    sent: list[str] = []
    patch_media(monkeypatch, sent)
    bot = FakeBot({"alive"}, set())
    monkeypatch.setattr(delivery, "get_bots", lambda: {"1": bot})
    poller = Poller(store, SETTINGS)

    await poller.run_round()
    await poller.run_round()

    assert sent == ["new"]
    snapshot = await store.snapshot()
    for name in ("gone", "private"):
        key = make_target_key(targets[name].dump())
        assert cursor_of(snapshot, key) == Cursor("old", 1)
    alive_key = make_target_key(targets["alive"].dump())
    assert cursor_of(snapshot, alive_key) == Cursor("new", 2)
    assert bot.calls == 2


@pytest.mark.asyncio
async def test_scan_failure_keeps_all_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter: FakeAdapter
) -> None:
    """扫描失败时不做过滤，下一轮继续尝试。"""

    store, _author, (target_key,) = await make_author(tmp_path, "group")
    adapter.works = [make_work("new", 2)]
    sent: list[str] = []
    patch_media(monkeypatch, sent)
    bot = FakeBot(set(), set(), RuntimeError("api failed"))
    monkeypatch.setattr(delivery, "get_bots", lambda: {"1": bot})
    poller = Poller(store, SETTINGS)

    await poller.run_round()

    assert sent == ["new"]
    assert cursor_of(await store.snapshot(), target_key) == Cursor("new", 2)
    assert not poller.context.scanned


@pytest.mark.asyncio
async def test_dynamic_sharing_bvid_is_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(
        tmp_path,
        "group",
        cursors={"video": Cursor("old", 1), "dynamic": Cursor("old", 1)},
    )
    sent: list[str] = []
    patch_media(monkeypatch, sent)
    poller = Poller(store, SETTINGS)
    video = make_work("BV1", 5, bvid="BV1")
    dynamic = make_work("dyn", 6, bvid="BV1", kind="dynamic")

    await delivery.deliver_feed(poller.context, author, "video", [video])
    await delivery.deliver_feed(poller.context, author, "dynamic", [dynamic])

    assert sent == ["BV1"]
    snapshot = await store.snapshot()
    assert cursor_of(snapshot, target_key, "dynamic") == Cursor("dyn", 6)


@pytest.mark.asyncio
async def test_poll_round_only_runs_due_authors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter: FakeAdapter
) -> None:
    store = SubscriptionStore(tmp_path)
    for author_id, name in (("1", "到期"), ("2", "未到期")):
        target = Target.group(name)
        await store.add_target(
            "bilibili",
            author_id,
            name,
            make_target_key(target.dump()),
            target.dump(),
            {"video": Cursor("old", 1)},
        )
    adapter.works = [make_work("new", 2)]
    patch_media(monkeypatch, [])
    poller = Poller(store, SETTINGS)
    poller.state("bilibili:2").next_poll_at = time.monotonic() + 600

    await poller.run_round()

    assert [call[0] for call in adapter.calls] == ["1"]


@pytest.mark.asyncio
async def test_poll_author_failure_backs_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter: FakeAdapter
) -> None:
    store, author, _ = await make_author(tmp_path, "group")
    adapter.error = TransientError("风控")
    poller = Poller(store, SETTINGS)

    await poller.poll_author(author)

    state = poller.state(author.key)
    assert state.failure_count == 1
    assert state.next_poll_at > time.monotonic()
    assert "风控" in state.last_error


@pytest.mark.asyncio
async def test_permanent_failure_disables_author(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter: FakeAdapter
) -> None:
    store, author, _ = await make_author(tmp_path, "group")
    adapter.error = PermanentError("作者已注销")
    poller = Poller(store, SETTINGS)

    await poller.poll_author(author)

    state = poller.state(author.key)
    assert state.disabled
    assert state.next_poll_at == float("inf")

    adapter.calls.clear()
    await poller.run_round()
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_poll_author_success_resets_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter: FakeAdapter
) -> None:
    store, author, _ = await make_author(tmp_path, "group")
    poller = Poller(store, SETTINGS)
    state = poller.state(author.key)
    state.failure_count = 2
    state.last_error = "旧错误"
    patch_media(monkeypatch, [])

    await poller.poll_author(author)

    assert state.failure_count == 0
    assert state.last_error == ""
    assert state.next_poll_at > time.monotonic()


def test_backoff_is_capped() -> None:
    from nonebot_plugin_ifollow.failures import backoff_seconds

    assert backoff_seconds(1) == 30
    assert backoff_seconds(2) == 60
    assert backoff_seconds(10) == 300


def test_tick_scales_with_author_period() -> None:
    fast = Poller(
        SubscriptionStore(),
        Settings(interval=60, max_items=5, media_max_bytes=0, use_base64=False),
    )
    slow = Poller(
        SubscriptionStore(),
        Settings(interval=3600, max_items=5, media_max_bytes=0, use_base64=False),
    )

    assert fast.tick_seconds() == 15
    assert slow.tick_seconds() == 300


@pytest.mark.asyncio
async def test_status_text_lists_failing_and_disabled_authors(
    tmp_path: Path,
) -> None:
    store, author, _ = await make_author(tmp_path, "group")
    poller = Poller(store, SETTINGS)
    poller.state(author.key).failure_count = 2
    poller.state(author.key).last_error = "风控"
    poller.state(author.key).next_poll_at = time.monotonic() + 30
    other = AuthorSubscription("douyin", "2", "已停用作者")
    poller.state(other.key).disabled = True
    poller.state(other.key).last_error = "作者已注销"

    text = poller.status_text({"bilibili:123": author, other.key: other})

    assert "连续失败 2 次" in text
    assert "已停用" in text


@pytest.mark.asyncio
async def test_prune_drops_states_of_removed_authors(
    tmp_path: Path, adapter: FakeAdapter
) -> None:
    store, _, _ = await make_author(tmp_path, "group")
    poller = Poller(store, SETTINGS)
    poller.state("bilibili:gone")

    await poller.run_round()

    assert "bilibili:gone" not in poller.states


@pytest.mark.asyncio
async def test_stop_is_idempotent() -> None:
    poller = Poller(SubscriptionStore(), SETTINGS)

    await poller.stop()
    poller.start()
    await poller.stop()


def test_permanent_target_error_detects_kicked_group() -> None:
    from nonebot_plugin_ifollow.failures import is_permanent_target_error

    error = ActionFailed(retcode=100, msg="机器人已被移出该群")

    assert is_permanent_target_error(error)
    assert not is_permanent_target_error(RuntimeError("网络超时"))


def test_permanent_work_error_covers_parsehub_names() -> None:
    from nonebot_plugin_ifollow.failures import is_permanent_work_error

    class ContentUnavailableFallbackError(ContentUnavailableError):
        pass

    assert is_permanent_work_error(ContentUnavailableFallbackError("x"))
    assert is_permanent_work_error(PermanentError("作者已注销"))
    assert not is_permanent_work_error(RuntimeError("超时"))


def test_delivery_context_prune_clears_sent_buffers(tmp_path: Path) -> None:
    store = SubscriptionStore(tmp_path)
    poller = Poller(store, SETTINGS)
    delivery.sent_identities(poller.context, "bilibili:1", "gone").append("x")

    delivery.prune(poller.context, {})

    assert poller.context.sent == {}


@pytest.mark.asyncio
async def test_media_for_selects_pipeline_by_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """抖音与 B 站各走自己的媒体管线。"""

    calls: list[str] = []

    @asynccontextmanager
    async def douyin_pipeline(
        _work: PlatformWork, _settings: Settings, _author_name: str = ""
    ) -> AsyncIterator[list[Any]]:
        calls.append("douyin")
        yield []

    @asynccontextmanager
    async def bilibili_pipeline(
        _work: PlatformWork, _settings: Settings, _author_name: str = ""
    ) -> AsyncIterator[list[Any]]:
        calls.append("bilibili")
        yield []

    monkeypatch.setattr(delivery, "douyin_media_messages", douyin_pipeline)
    monkeypatch.setattr(delivery, "bilibili_media_messages", bilibili_pipeline)
    douyin_work = PlatformWork(
        platform="douyin",
        work_id="w",
        url="https://www.douyin.com/video/1",
        title="",
        description="",
        create_time=1,
        author=AuthorInfo("1", "作者", "1"),
    )

    async with delivery.media_for(douyin_work, SETTINGS):
        pass
    async with delivery.media_for(make_work("w", 1), SETTINGS):
        pass

    assert calls == ["douyin", "bilibili"]


@pytest.mark.asyncio
async def test_deliver_author_refreshes_author_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """投递时用最新作品刷新作者昵称与最近更新时间。"""

    store, author, (target_key,) = await make_author(tmp_path, "group")
    patch_media(monkeypatch, [])
    poller = Poller(store, SETTINGS)

    await delivery.deliver_author(
        poller.context, author, {"video": [make_work("new", 5)]}
    )

    snapshot = await store.snapshot()
    assert snapshot["bilibili:123"].last_update_time == 5
    assert snapshot["bilibili:123"].author_name == "作者"
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
        error=ContentUnavailableError("视频已删除"),
        fail_works={"bad"},
    )
    poller = Poller(store, SETTINGS)

    await delivery.deliver_feed(
        poller.context,
        author,
        "video",
        [make_work("new", 3), make_work("bad", 2), make_work("old", 1)],
    )

    assert sent == ["new"]
    assert cursor_of(await store.snapshot(), target_key) == Cursor("new", 3)
    assert poller.context.stats.abandoned == 1


@pytest.mark.asyncio
async def test_cursor_work_deleted_still_sends_newer_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """游标作品被作者删除后，靠发布时间仍能判断新旧。"""

    store, author, (target_key,) = await make_author(
        tmp_path, "group", cursors={"video": Cursor("gone", 2)}
    )
    sent: list[str] = []
    patch_media(monkeypatch, sent)
    poller = Poller(store, SETTINGS)

    await delivery.deliver_feed(poller.context, author, "video", [make_work("new", 5)])

    assert sent == ["new"]
    assert cursor_of(await store.snapshot(), target_key) == Cursor("new", 5)


@pytest.mark.asyncio
async def test_work_older_than_cursor_is_not_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, author, (target_key,) = await make_author(
        tmp_path, "group", cursors={"video": Cursor("gone", 2)}
    )
    sent: list[str] = []
    patch_media(monkeypatch, sent)
    poller = Poller(store, SETTINGS)

    await delivery.deliver_feed(poller.context, author, "video", [make_work("old", 1)])

    assert sent == []
    assert cursor_of(await store.snapshot(), target_key) == Cursor("gone", 2)


@pytest.mark.asyncio
async def test_sent_memory_is_scoped_per_author(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不同作者的作品标识可能相同，去重不能跨作者生效。

    `PlatformWork.identity` 只在平台内唯一：抖音 aweme_id 与 B 站动态 id 都是
    19 位数字，一旦相等，按目标会话共享的去重缓冲会把后一个作者的作品当成「已发」，
    推进游标却什么都不发。
    """

    store = SubscriptionStore(tmp_path)
    target = Target.group("group")
    target_key = make_target_key(target.dump())
    await store.add_target(
        "douyin",
        "d1",
        "抖音作者",
        target_key,
        target.dump(),
        {"video": Cursor("old", 1)},
    )
    await store.add_target(
        "bilibili",
        "b1",
        "B站作者",
        target_key,
        target.dump(),
        {"dynamic": Cursor("old", 1)},
    )
    douyin_author = AuthorSubscription(
        "douyin",
        "d1",
        "抖音作者",
        {target_key: TargetSubscription(target.dump(), {"video": Cursor("old", 1)})},
    )
    bili_author = AuthorSubscription(
        "bilibili",
        "b1",
        "B站作者",
        {target_key: TargetSubscription(target.dump(), {"dynamic": Cursor("old", 1)})},
    )
    douyin_work = PlatformWork(
        platform="douyin",
        work_id="7695069095121681125",
        url="https://www.douyin.com/video/7695069095121681125",
        title="抖音作品",
        description="",
        create_time=10,
        author=AuthorInfo("d1", "抖音作者", "d1"),
    )
    bili_work = make_work("7695069095121681125", 20, kind="dynamic")
    sent: list[str] = []
    patch_media(monkeypatch, sent)
    poller = Poller(store, SETTINGS)

    await delivery.deliver_feed(poller.context, douyin_author, "video", [douyin_work])
    await delivery.deliver_feed(poller.context, bili_author, "dynamic", [bili_work])

    assert sent == ["7695069095121681125", "7695069095121681125"]
    snapshot = await store.snapshot()
    cursors = snapshot["bilibili:b1"].targets[target_key].cursors
    assert cursors["dynamic"] == Cursor("7695069095121681125", 20)


@pytest.mark.asyncio
@pytest.mark.parametrize("mention", [True, False])
async def test_send_work_appends_subscriber_mentions(
    monkeypatch: pytest.MonkeyPatch, mention: bool
) -> None:
    """订阅人提醒拼在文案末尾；开关关闭时连分隔线也不追加。"""

    sent: list[tuple[str, str]] = []

    class RecordingMessage:
        def __init__(self, text: str = "") -> None:
            self.text = text

        def __add__(self, other: Any) -> RecordingMessage:
            extra = other.text if isinstance(other, RecordingMessage) else str(other)
            return RecordingMessage(self.text + extra)

        @staticmethod
        def at(user_id: str) -> RecordingMessage:
            return RecordingMessage(f"@{user_id}")

        async def send(self, *, target: Target) -> None:
            sent.append((self.text, target.id))

    monkeypatch.setattr(delivery, "UniMessage", RecordingMessage)
    target = Target.group("group")
    state = TargetSubscription(
        target.dump(), subscriber_ids=["1", "2"], mention=mention
    )

    await delivery.send_work([RecordingMessage("文案")], state)  # type: ignore[list-item]

    text, target_id = sent[0]
    assert target_id == "group"
    if mention:
        assert delivery.SEPARATOR in text
        assert "@1" in text
        assert "@2" in text
    else:
        assert delivery.SEPARATOR not in text
        assert "@" not in text
