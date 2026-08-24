"""插件配置。"""

from nonebot import get_plugin_config
from pydantic import BaseModel, Field


class Config(BaseModel):
    """抖音订阅插件配置。"""

    douyin_subscribe_interval: int = Field(default=300, ge=30)
    """轮询间隔，单位为秒，最小 30 秒。"""

    douyin_subscribe_count: int = Field(default=20, ge=1, le=50)
    """每次请求拉取的最新作品数。"""

    douyin_cookie: str | None = None
    """可选的抖音 Cookie；网页接口被限制时填写。"""


config = get_plugin_config(Config)
