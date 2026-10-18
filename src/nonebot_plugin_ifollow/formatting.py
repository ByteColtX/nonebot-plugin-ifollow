"""命令输出的文案与订阅匹配。"""

from __future__ import annotations

from datetime import datetime

from .models import AuthorSubscription
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


def target_subscriptions(
    authors: dict[str, AuthorSubscription], target_key: str
) -> list[AuthorSubscription]:
    """返回当前会话的订阅，顺序与订阅列表命令一致。"""

    return [author for author in authors.values() if target_key in author.targets]


def format_handle(author: AuthorSubscription) -> str:
    """返回作者标识的展示形式。"""

    if not author.author_handle:
        return "未知"
    if author.platform == "douyin":
        return f"@{author.author_handle}"
    return f"UID:{author.author_handle}"


def format_update_time(timestamp: int) -> str:
    """返回「最近更新」的展示形式。"""

    if timestamp <= 0:
        return "未知"
    return datetime.fromtimestamp(timestamp).strftime("%m月%d日 %H:%M")


def subscription_list(authors: dict[str, AuthorSubscription], target_key: str) -> str:
    """生成 ``爱播订阅列表`` 的正文。"""

    items = target_subscriptions(authors, target_key)
    if not items:
        return "当前会话没有爱播订阅。"
    lines = [
        f"{index}. {platform_name(author.platform)} {author.author_name}"
        f"（{format_handle(author)}）{format_update_time(author.last_update_time)}"
        for index, author in enumerate(items, 1)
    ]
    header = "当前会话的爱播订阅：\n序号 平台 昵称（标识） 最近更新\n"
    return header + "\n".join(lines)


__all__ = [
    "HELP_MESSAGE",
    "format_handle",
    "format_update_time",
    "subscription_list",
    "target_subscriptions",
]
