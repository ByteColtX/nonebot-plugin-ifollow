"""面向用户的展示常量与助手。"""

from __future__ import annotations

from nonebot import get_driver

SEPARATOR = "─" * 12
"""分段用的分隔线，推送文案、列表、帮助与状态都复用它。"""

HELP_COMMAND = "爱播帮助"
SUBSCRIBE_COMMAND = "爱播订阅"
UNSUBSCRIBE_COMMAND = "爱播退订"
LIST_COMMAND = "爱播列表"
NOTIFY_COMMAND = "爱播提醒"
STATUS_COMMAND = "爱播状态"
"""六个命令的唯一名字：一个功能只对应一个 ``爱播X``。"""


def platform_name(platform: str) -> str:
    """返回平台的中文名。"""

    return "抖音" if platform == "douyin" else "B站"


def command_prefix() -> str:
    """返回命令前缀：优先 ``/``，否则取配置里排序后的第一个非空前缀。"""

    starts = get_driver().config.command_start
    if "/" in starts:
        return "/"
    return next((start for start in sorted(starts) if start), "")


def command_text(name: str, args: str = "") -> str:
    """拼出用户可直接发送的命令文本。"""

    text = f"{command_prefix()}{name}"
    return f"{text} {args}" if args else text


__all__ = [
    "HELP_COMMAND",
    "LIST_COMMAND",
    "NOTIFY_COMMAND",
    "SEPARATOR",
    "STATUS_COMMAND",
    "SUBSCRIBE_COMMAND",
    "UNSUBSCRIBE_COMMAND",
    "command_prefix",
    "command_text",
    "platform_name",
]
