"""平台适配器注册表：按链接识别平台、按平台取适配器。"""

from __future__ import annotations

from typing import cast

from . import bilibili_platform, douyin_platform
from .platforms import Platform, PlatformAdapter

ADAPTERS: tuple[PlatformAdapter, ...] = (
    cast(PlatformAdapter, bilibili_platform),
    cast(PlatformAdapter, douyin_platform),
)


def adapter_for(platform: Platform) -> PlatformAdapter:
    """按平台名取适配器。

    Args:
        platform: 平台标识。

    Returns:
        对应的平台适配器。

    Raises:
        ValueError: 平台未注册。
    """

    for candidate in ADAPTERS:
        if candidate.platform == platform:
            return candidate
    raise ValueError(f"不支持的平台：{platform}")


def detect(value: str) -> PlatformAdapter:
    """按用户输入识别平台。

    Args:
        value: 作者主页、作品链接或作者 ID。

    Returns:
        匹配的平台适配器。

    Raises:
        ValueError: 输入无法识别为任何平台。
    """

    for candidate in ADAPTERS:
        if candidate.matches(value):
            return candidate
    raise ValueError("请输入抖音或 B 站的作者主页链接、作品链接，或作者 ID")
