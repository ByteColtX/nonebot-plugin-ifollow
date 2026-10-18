import time

from nonebot_plugin_ifollow.models import AuthorSubscription, SubscriptionStore
from nonebot_plugin_ifollow.platforms import AuthorInfo, Cursor, PlatformWork
from nonebot_plugin_ifollow.poller import Poller
from nonebot_plugin_ifollow.settings import Settings
from nonebot_plugin_ifollow.status import (
    AuthorPollState,
    RoundStats,
    format_age,
    format_wait,
    platform_name,
    render,
)

SETTINGS = Settings(interval=1800, max_items=5, media_max_bytes=0, use_base64=False)


def test_format_age_buckets() -> None:
    assert format_age(5) == "5 秒前"
    assert format_age(125) == "2 分钟前"
    assert format_age(7300) == "2 小时前"


def test_format_wait_handles_infinity_and_zero() -> None:
    assert format_wait(float("inf")) == "稍后"
    assert format_wait(-1) == "立即"
    assert format_wait(30) == "30 秒后"
    assert format_wait(120) == "2 分钟后"


def test_platform_name() -> None:
    assert platform_name("douyin") == "抖音"
    assert platform_name("bilibili") == "B站"


def make_work() -> PlatformWork:
    return PlatformWork(
        platform="bilibili",
        work_id="w",
        url="https://example.com/w",
        title="标题",
        description="",
        create_time=1,
        author=AuthorInfo("1", "作者", "1"),
    )


def render_status(
    *,
    states: dict[str, AuthorPollState],
    authors: dict[str, AuthorSubscription],
    stats: RoundStats | None = None,
    last_round_at: float = 0.0,
    in_flight: set[str] | None = None,
    abandoned: list[tuple[str, str]] | None = None,
) -> str:
    return render(
        settings=SETTINGS,
        tick_seconds=60,
        fetch_concurrency=4,
        stats=stats or RoundStats(),
        states=states,
        authors=authors,
        in_flight=in_flight or set(),
        last_round_at=last_round_at,
        recent_abandoned=abandoned or [],
    )


def test_render_reports_never_run_and_healthy() -> None:
    author = AuthorSubscription("bilibili", "1", "作者")
    text = render_status(states={}, authors={author.key: author})

    assert "上次轮询：尚未运行" in text
    assert "所有作者状态正常。" in text


def test_render_reports_round_stats_and_inflight() -> None:
    author = AuthorSubscription("bilibili", "1", "作者")
    text = render_status(
        states={},
        authors={author.key: author},
        stats=RoundStats(authors=3, due=1, duration=1.5, new_works=2, delivered=2),
        last_round_at=time.monotonic() - 90,
        in_flight={author.key},
        abandoned=[("标题", "作品已删除")],
    )

    assert "上次轮询：1 分钟前" in text
    assert "正在抓取：1 个作者" in text
    assert "本轮：2 个新作品" in text
    assert "最近放弃：标题（作品已删除）" in text


def test_render_lists_failing_author() -> None:
    author = AuthorSubscription("bilibili", "1", "作者")
    state = AuthorPollState(
        failure_count=2, last_error="风控", next_poll_at=time.monotonic() + 30
    )
    text = render_status(states={author.key: state}, authors={author.key: author})

    assert "连续失败 2 次（风控）" in text
    assert "秒后重试" in text


def test_render_lists_disabled_author() -> None:
    author = AuthorSubscription("douyin", "1", "作者")
    state = AuthorPollState(disabled=True, last_error="作者已注销")
    text = render_status(states={author.key: state}, authors={author.key: author})

    assert "已停用（作者已注销）" in text


def test_poller_status_text_uses_round_stats() -> None:
    store = SubscriptionStore()
    poller = Poller(store, SETTINGS)
    poller.stats = RoundStats(authors=1, due=1)

    text = poller.status_text({})

    assert "作者周期 1800 秒" in text


def test_cursor_order_is_total() -> None:
    assert Cursor("b", 20).order > Cursor("a", 10).order
    assert Cursor("a", 10).order < Cursor("b", 10).order
    assert make_work().order == (1, "w")
