"""命令输出的文案与订阅匹配。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from .errors import PermanentError, TransientError
from .media import SEPARATOR
from .models import AuthorSubscription
from .platforms import Platform
from .status import platform_name

HELP_MESSAGE = (
    "爱播帮助\n"
    f"{SEPARATOR}\n"
    "· 爱播订阅 <作者主页或作品链接>\n"
    "· 爱播订阅列表\n"
    "· 爱播取消订阅 <序号、作者 ID 或链接>\n"
    "· 爱播状态（仅超级用户）\n"
    f"{SEPARATOR}\n"
    "引用含链接的消息后发送 爱播订阅 也能订阅。\n"
    "支持抖音与 B 站，首次订阅不补发历史内容。"
)

_PLATFORM_CODES = {"douyin": "DY", "bilibili": "BILI"}

_RISK_HINTS = ("风控", "拦截", "blocked", "403", "412", "429", "-352", "-799", "-509")
_GONE_HINTS = ("不存在", "已注销", "已删除", "404", "410", "62002")


@dataclass(frozen=True, slots=True)
class FailureReport:
    """面向用户的失败说明，附带可供运维检索的错误码。"""

    code: str
    reason: str
    hint: str


def author_label(platform: str, name: str) -> str:
    """统一的作者称呼：``平台 · 昵称``。"""

    return f"{platform_name(platform)} · {name}"


def describe_failure(
    exc: BaseException, *, platform: str | None = None
) -> FailureReport:
    """把异常翻译成「人话 + 建议 + 错误码」，细节只留在日志里。"""

    code = _PLATFORM_CODES.get(platform or "", "")
    text = str(exc).lower()
    if isinstance(exc, ValueError):
        return FailureReport(
            _code(code, "INPUT"),
            "没能识别这个链接",
            "请发送作者主页链接、作品链接，或直接发作者 ID。",
        )
    if isinstance(exc, PermanentError) or any(k in text for k in _GONE_HINTS):
        return FailureReport(
            _code(code, "GONE"),
            "作者不存在或已注销",
            "请确认链接是否指向仍在更新的作者。",
        )
    if any(k in text for k in _RISK_HINTS):
        return FailureReport(
            _code(code, "RISK"),
            "平台暂时拦截了请求",
            "请稍后重试；多次失败可以在 .env 里配置对应平台的 Cookie。",
        )
    if isinstance(exc, (TransientError, httpx.HTTPError)):
        return FailureReport(
            _code(code, "NET"),
            "暂时连不上平台",
            "请稍后重试。",
        )
    return FailureReport(
        _code(code, "UNKNOWN"),
        "遇到未预期的问题",
        "请稍后重试；如果一直失败，请把下面的错误码发出来。",
    )


def _code(platform_code: str, category: str) -> str:
    parts = [part for part in (platform_code, category) if part]
    return "E-" + "-".join(parts)


def render_failure(action: str, report: FailureReport) -> str:
    """生成 ``订阅失败：…`` 这类消息；只有人话、建议与错误码，没有堆栈。"""

    return f"{action}失败：{report.reason}\n{report.hint}\n错误码 {report.code}"


PLATFORM_ORDER: tuple[Platform, ...] = ("douyin", "bilibili")
"""订阅列表里平台的展示顺序，同时决定序号顺序。"""

RELATIVE_DAYS_LIMIT = 90
"""超过这个天数就改用绝对日期，避免出现「730 天前」。"""


def target_subscriptions(
    authors: dict[str, AuthorSubscription], target_key: str
) -> list[AuthorSubscription]:
    """返回当前会话的订阅，按平台分组、组内保持订阅先后。

    列表展示与「按序号取消」共用这一个顺序，改排序时两者不会错位。
    """

    items = [author for author in authors.values() if target_key in author.targets]
    return sorted(items, key=lambda author: _platform_rank(author.platform))


def _platform_rank(platform: str) -> int:
    if platform in PLATFORM_ORDER:
        return PLATFORM_ORDER.index(platform)  # type: ignore[arg-type]
    return len(PLATFORM_ORDER)


def format_handle(author: AuthorSubscription) -> str:
    """返回作者标识的展示形式；没有标识时返回空串。"""

    if not author.author_handle:
        return ""
    if author.platform == "douyin":
        return f"@{author.author_handle}"
    return f"UID:{author.author_handle}"


def format_relative_time(timestamp: int, *, now: float | None = None) -> str:
    """把发布时间格式化成「刚刚 / N 分钟前 / N 天前 / 绝对日期」。"""

    if timestamp <= 0:
        return "未知"
    current = time.time() if now is None else now
    delta = max(0, int(current - timestamp))
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{delta // 60} 分钟前"
    if delta < 86400:
        return f"{delta // 3600} 小时前"
    days = delta // 86400
    if days <= RELATIVE_DAYS_LIMIT:
        return f"{days} 天前"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def subscription_list(
    authors: dict[str, AuthorSubscription],
    target_key: str,
    *,
    now: float | None = None,
) -> str:
    """生成 ``爱播订阅列表`` 的正文：按平台分组，末尾给出取消订阅提示。"""

    items = target_subscriptions(authors, target_key)
    if not items:
        return "当前会话没有爱播订阅。\n发送 爱播订阅 <作者主页链接> 开始订阅。"
    lines = [f"爱播订阅（{len(items)} 个）", SEPARATOR]
    current_platform: str | None = None
    for index, author in enumerate(items, 1):
        if author.platform != current_platform:
            if current_platform is not None:
                lines.append("")
            current_platform = author.platform
            lines.append(platform_name(author.platform))
        lines.append(f"{index}. {_describe(author, now=now)}")
    lines.extend(["", "取消订阅：爱播取消订阅 <序号>"])
    return "\n".join(lines)


def _describe(author: AuthorSubscription, *, now: float | None) -> str:
    """拼出「昵称 标识 · 最近更新」。"""

    parts = [author.author_name]
    if handle := format_handle(author):
        parts.append(handle)
    updated = format_relative_time(author.last_update_time, now=now)
    return f"{' '.join(parts)} · {updated}"


__all__ = [
    "HELP_MESSAGE",
    "PLATFORM_ORDER",
    "FailureReport",
    "author_label",
    "describe_failure",
    "format_handle",
    "format_relative_time",
    "render_failure",
    "subscription_list",
    "target_subscriptions",
]
