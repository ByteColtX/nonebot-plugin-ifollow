"""命令输出的文案与订阅匹配。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from .errors import PermanentError
from .models import AuthorSubscription
from .platforms import Platform
from .status import platform_name
from .style import (
    HELP_COMMAND,
    LIST_COMMAND,
    NOTIFY_COMMAND,
    SEPARATOR,
    STATUS_COMMAND,
    SUBSCRIBE_COMMAND,
    UNSUBSCRIBE_COMMAND,
    command_text,
)


def help_message() -> str:
    """生成帮助正文；紧凑一行一条，不带命令前缀。"""

    return (
        f"{HELP_COMMAND}\n"
        f"{SEPARATOR}\n"
        f"· {SUBSCRIBE_COMMAND} <主页、作品链接或作者 ID；"
        "也可引用带链接的消息>\n"
        f"· {LIST_COMMAND}\n"
        f"· {UNSUBSCRIBE_COMMAND} <序号、作者 ID、昵称或链接>\n"
        f"· {NOTIFY_COMMAND} 开|关\n"
        f"· {STATUS_COMMAND}\n"
        f"{SEPARATOR}\n"
        "退订只能退自己的，群主、群管理和机器人超级用户可以退任意订阅\n"
        "支持平台：抖音、B站"
    )


def usage() -> str:
    """生成插件元数据里的简短用法。"""

    return "\n".join(
        (
            command_text(HELP_COMMAND),
            command_text(SUBSCRIBE_COMMAND, "<作者主页、作品链接或作者 ID>"),
            command_text(LIST_COMMAND),
            command_text(UNSUBSCRIBE_COMMAND, "<序号、作者 ID、昵称或链接>"),
            command_text(NOTIFY_COMMAND, "开|关"),
            f"{command_text(STATUS_COMMAND)}（超级用户）",
        )
    )


def empty_subscription_message() -> str:
    """当前会话没有订阅时的统一提示。"""

    return (
        "本会话还没有订阅\n"
        f"发送 {command_text(SUBSCRIBE_COMMAND, '<作者主页链接>')} 开始订阅"
    )


_GONE_HINTS = ("不存在", "已注销", "已删除", "404", "410", "62002")
RAW_ERROR_LIMIT = 200
"""原始错误在消息里的最大长度，超长接口响应会截断。"""


@dataclass(frozen=True, slots=True)
class FailureReport:
    """面向用户的失败说明：原始错误 + 一句他能做的事。"""

    detail: str
    hint: str


def author_label(platform: str, name: str) -> str:
    """统一的作者称呼：``平台 · 昵称``。"""

    return f"{platform_name(platform)} · {name}"


def _raw_error(exc: BaseException) -> str:
    """把异常压成一行可读文本；空消息退回异常类名。"""

    text = " ".join(str(exc).split()) or type(exc).__name__
    if len(text) > RAW_ERROR_LIMIT:
        return f"{text[:RAW_ERROR_LIMIT]}…"
    return text


def describe_failure(
    exc: BaseException, *, value: str = "", usage: str = ""
) -> FailureReport:
    """把异常整理成「原始错误 + 用户能做的一步」。

    Args:
        exc: 捕获到的异常。
        value: 用户这次输入的原文，链接认不出时回显给他看。
        usage: 输入类失败时提示的完整命令文本。

    Returns:
        失败说明；只含用户能执行的动作，不含配置与日志细节。
    """

    text = str(exc).lower()
    if isinstance(exc, ValueError):
        detail = f"没能识别 {value}" if value else "没能识别这个链接"
        return FailureReport(detail, f"发送 {usage}" if usage else "请重试")
    if isinstance(exc, PermanentError) or any(k in text for k in _GONE_HINTS):
        return FailureReport(_raw_error(exc), "确认一下链接还能不能打开")
    return FailureReport(_raw_error(exc), "稍后再试")


def render_failure(action: str, report: FailureReport) -> str:
    """生成 ``无法订阅`` 这类提示：结论、原始错误、下一步。"""

    return f"无法{action}\n{report.detail}\n{report.hint}"


PLATFORM_ORDER: tuple[Platform, ...] = ("douyin", "bilibili")
"""订阅列表里平台的展示顺序，同时决定序号顺序。"""

RELATIVE_DAYS_LIMIT = 90
"""超过这个天数就改用绝对日期，避免出现「730 天前」。"""


def target_subscriptions(
    authors: dict[str, AuthorSubscription], target_key: str
) -> list[AuthorSubscription]:
    """返回当前会话的订阅，按平台分组、组内保持订阅先后。

    列表展示与「按序号退订」共用这一个顺序，改排序时两者不会错位。
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
    return f"UID {author.author_handle}"


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
    """生成 ``爱播列表`` 的正文：按平台分组，末尾给出退订提示。"""

    items = target_subscriptions(authors, target_key)
    if not items:
        return empty_subscription_message()
    lines = [f"{LIST_COMMAND}（{len(items)} 个）", SEPARATOR]
    current_platform: str | None = None
    for index, author in enumerate(items, 1):
        if author.platform != current_platform:
            if current_platform is not None:
                lines.append("")
            current_platform = author.platform
            lines.append(platform_name(author.platform))
        lines.append(f"{index}. {_describe(author, now=now)}")
    lines.extend(["", f"退订：{command_text(UNSUBSCRIBE_COMMAND, '<序号>')}"])
    return "\n".join(lines)


def _describe(author: AuthorSubscription, *, now: float | None) -> str:
    """拼出「昵称 标识 · 最近更新」。"""

    parts = [author.author_name]
    if handle := format_handle(author):
        parts.append(handle)
    updated = format_relative_time(author.last_update_time, now=now)
    if updated == "未知":
        return " ".join(parts)
    return f"{' '.join(parts)} · {updated}更新"


__all__ = [
    "PLATFORM_ORDER",
    "FailureReport",
    "author_label",
    "describe_failure",
    "empty_subscription_message",
    "format_handle",
    "format_relative_time",
    "help_message",
    "render_failure",
    "subscription_list",
    "target_subscriptions",
    "usage",
]
