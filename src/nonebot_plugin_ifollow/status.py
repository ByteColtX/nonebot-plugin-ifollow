"""轮询运行时状态与 ``爱播状态`` 文案。"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .failures import WORK_RETRY_LIMIT
from .models import AuthorSubscription
from .settings import Settings
from .style import SEPARATOR, STATUS_COMMAND, platform_name


@dataclass
class AuthorPollState:
    """单个作者的运行时轮询状态，重启后清零。"""

    next_poll_at: float = 0.0
    failure_count: int = 0
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
    unhealthy = [
        (author_key, state)
        for author_key, state in states.items()
        if (state.failure_count or state.disabled) and author_key in authors
    ]
    headline = (
        f"{STATUS_COMMAND} · {len(unhealthy)} 个作者异常"
        if unhealthy
        else f"{STATUS_COMMAND} · 正常"
    )
    lines = [
        headline,
        SEPARATOR,
        f"每 {int(tick_seconds)} 秒扫描一次，作者每 {settings.interval} 秒轮询一次，"
        f"同时抓取 {fetch_concurrency} 个作者",
    ]
    if last_round_at:
        lines.append(
            f"上次轮询 {format_age(now - last_round_at)}，"
            f"耗时 {stats.duration:.1f} 秒，{stats.due}/{stats.authors} 个作者到期"
        )
    else:
        lines.append("还没有轮询过")
    if in_flight:
        lines.append(f"正在抓取 {len(in_flight)} 个作者")
    lines.append(
        f"本轮新增 {stats.new_works} 条，发送 {stats.delivered} 次，"
        f"放弃 {stats.abandoned} 条"
    )
    if recent_abandoned:
        work_title, reason = recent_abandoned[-1]
        lines.append(f"最近放弃 {work_title}：{reason}")
    lines.append(SEPARATOR)
    if not unhealthy:
        lines.append("所有作者正常")
        return "\n".join(lines)
    for author_key, state in sorted(unhealthy)[:10]:
        author = authors[author_key]
        label = f"{platform_name(author.platform)} {author.author_name}"
        if state.disabled:
            lines.extend([f"{label} · 已停用", f"原因：{state.last_error}"])
            continue
        lines.extend(
            [
                f"{label} · 连续失败 {state.failure_count} 次",
                f"{state.last_error}，{format_wait(state.next_poll_at - now)}重试",
            ]
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
