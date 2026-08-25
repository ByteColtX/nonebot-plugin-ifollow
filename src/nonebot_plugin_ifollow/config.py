"""爱播插件配置。"""

from nonebot import get_plugin_config
from pydantic import BaseModel, Field


class Config(BaseModel):
    """爱播订阅配置。"""

    ifollow_subscribe_interval: int = Field(default=300, ge=30)
    """所有平台共享的轮询间隔，单位为秒。"""

    ifollow_subscribe_count: int = Field(default=20, ge=1, le=50)
    """每个平台每次拉取的最新内容数量。"""

    ifollow_douyin_cookie: str | None = None
    """可选的抖音网页 Cookie。"""

    ifollow_bilibili_cookie: str | None = None
    """可选的 B 站 Cookie。"""


config = get_plugin_config(Config)
