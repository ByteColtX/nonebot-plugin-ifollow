"""面向用户的展示常量与助手。"""

from __future__ import annotations

SEPARATOR = "─" * 12
"""分段用的分隔线，推送文案、列表、帮助与状态都复用它。"""


def platform_name(platform: str) -> str:
    """返回平台的中文名。"""

    return "抖音" if platform == "douyin" else "B站"


__all__ = ["SEPARATOR", "platform_name"]
