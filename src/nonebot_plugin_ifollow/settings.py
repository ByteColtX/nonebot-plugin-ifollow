"""插件配置：nonebot 配置项与运行期不可变快照。"""

from __future__ import annotations

from dataclasses import dataclass

from nonebot import get_plugin_config
from pydantic import BaseModel, Field


class Config(BaseModel):
    """nonebot 配置项。"""

    ifollow_sub_interval: int = Field(default=1800, ge=30)
    ifollow_sub_max_items: int = Field(default=5, ge=1, le=50)
    ifollow_douyin_cookie: str | None = None
    ifollow_media_max_mb: int = Field(default=100, ge=1, le=2048)
    ifollow_use_base64: bool = False
    ifollow_bilibili_cookie: str | None = None


@dataclass(frozen=True, slots=True)
class Settings:
    """运行期使用的不可变配置快照。

    插件只在 :meth:`load` 里读一次 nonebot 配置，其余模块通过参数接收本对象，
    这样每个模块都能脱离全局状态单测。
    """

    interval: int
    max_items: int
    media_max_bytes: int
    use_base64: bool
    douyin_cookie: str | None = None
    bilibili_cookie: str | None = None

    @classmethod
    def load(cls) -> Settings:
        """从 nonebot 配置构造快照。"""

        raw = get_plugin_config(Config)
        return cls(
            interval=raw.ifollow_sub_interval,
            max_items=raw.ifollow_sub_max_items,
            media_max_bytes=raw.ifollow_media_max_mb * 1024 * 1024,
            use_base64=raw.ifollow_use_base64,
            douyin_cookie=raw.ifollow_douyin_cookie or None,
            bilibili_cookie=raw.ifollow_bilibili_cookie or None,
        )

    @property
    def dynamics_enabled(self) -> bool:
        """B 站动态只在配置了 Cookie 时轮询。"""

        return bool(self.bilibili_cookie)
