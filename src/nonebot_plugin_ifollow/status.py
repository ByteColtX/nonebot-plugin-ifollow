"""轮询运行时状态与 ``爱播状态`` 文案。"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .failures import WORK_RETRY_LIMIT
from .media import SEPARATOR
from .models import AuthorSubscription
from .settings import Settings


@dataclass
class AuthorPollState:
    """单个作者的运行时轮询状态，重启后清零。"""

    next_poll_at: float = 0.0
    failure_count: int = 0
    last_success_at: float = 0.0
    last_error: str = ""
    disabled: bool = False


@dataclass
class RoundStats:
    """最近一轮轮询的统计。"""

    authors: int = 0
    due: int = 0
    duration: float = 0.0
    new_works: int = 0
    delivered: int = 0
    abandoned: int = 0


class StateLike(Protocol):
    """状态展示需要的作者状态字段。"""

    failure_count: int
    last_error: str
    next_poll_at: float
    disabled: bool


def platform_name(platform: str) -> str:
    """返回平台中文名。"""

    return "抖音" if platform == "douyin" else "B站"


def format_age(seconds: float) -> str:
    """把秒数格式化成「N 秒/分钟/小时前」。"""

    if seconds < 60:
        return f"{max(0, int(seconds))} 秒前"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    return f"{int(seconds // 3600)} 小时前"


def format_wait(seconds: float) -> str:
    """把秒数格式化成「N 秒/分钟后」。"""

    if not math.isfinite(seconds):
        return "稍后"
    if seconds <= 0:
        return "立即"
    if seconds < 60:
        return f"{int(seconds)} 秒后"
    return f"{int(seconds // 60)} 分钟后"


def render(
    *,
    settings: Settings,
    tick_seconds: float,
    fetch_concurrency: int,
    stats: RoundStats,
    states: Mapping[str, StateLike],
    authors: dict[str, AuthorSubscription],
    in_flight: set[str],
    last_round_at: float,
    recent_abandoned: list[tuple[str, str]],
) -> str:
    """生成 ``爱播状态`` 的正文。"""

    now = time.monotonic()
    lines = [
        "爱播状态",
        SEPARATOR,
        f"· 调度：每 {int(tick_seconds)} 秒扫描，作者周期 {settings.interval} 秒，"
        f"抓取并发 {fetch_concurrency}",
    ]
    if last_round_at:
        lines.append(
            f"· 上次轮询：{format_age(now - last_round_at)}"
            f"（耗时 {stats.duration:.1f} 秒，"
            f"{stats.due}/{stats.authors} 个作者到期）"
        )
    else:
        lines.append("· 上次轮询：尚未运行")
    if in_flight:
        lines.append(f"· 正在抓取：{len(in_flight)} 个作者")
    lines.append(
        f"· 本轮：{stats.new_works} 个新作品，发送 {stats.delivered} 次，"
        f"放弃 {stats.abandoned} 个"
    )
    if recent_abandoned:
        title, reason = recent_abandoned[-1]
        lines.append(f"· 最近放弃：{title}（{reason}）")
    lines.append(SEPARATOR)
    unhealthy = [
        (author_key, state)
        for author_key, state in states.items()
        if (state.failure_count or state.disabled) and author_key in authors
    ]
    if not unhealthy:
        lines.append("所有作者状态正常。")
        return "\n".join(lines)
    lines.append(f"异常作者（{len(unhealthy)} 个）")
    for author_key, state in sorted(unhealthy)[:10]:
        author = authors[author_key]
        if state.disabled:
            lines.append(
                f"· {platform_name(author.platform)} {author.author_name}："
                f"已停用（{state.last_error}）"
            )
            continue
        lines.append(
            f"· {platform_name(author.platform)} {author.author_name}："
            f"连续失败 {state.failure_count} 次（{state.last_error}），"
            f"{format_wait(state.next_poll_at - now)}重试"
        )
    return "\n".join(lines)


__all__ = [
    "WORK_RETRY_LIMIT",
    "AuthorPollState",
    "RoundStats",
    "format_age",
    "format_wait",
    "platform_name",
    "render",
]
