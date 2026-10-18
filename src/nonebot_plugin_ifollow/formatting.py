"""命令输出的文案与订阅匹配。"""

from __future__ import annotations

import time
from datetime import datetime

from .media import SEPARATOR
from .models import AuthorSubscription
from .platforms import Platform
from .status import platform_name

HELP_MESSAGE = (
    "爱播帮助：\n"
    "爱播订阅 <抖音或 B 站作者主页/作品链接>\n"
    "爱播订阅列表（别名：爱播列表）\n"
    "爱播取消订阅 <序号、作者 ID、昵称或作者主页/作品链接>\n"
    "爱播状态（超级用户，查看轮询健康度）\n"
    "也可引用包含链接的消息后发送 爱播订阅。\n"
    "支持抖音和 B 站；首次订阅不会补发历史内容。"
)

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
    "format_handle",
    "format_relative_time",
    "subscription_list",
    "target_subscriptions",
]
