"""爱播插件配置。"""

from nonebot import get_plugin_config
from pydantic import BaseModel, Field


class Config(BaseModel):
    """爱播订阅配置。"""

    ifollow_sub_interval: int = Field(default=1800, ge=30)
    """每个作者的轮询周期，单位为秒；月更作者无需高频轮询。"""

    ifollow_sub_max_items: int = Field(default=5, ge=1, le=50)
    """每个作者每个 feed 一次轮询最多补发的作品数；超出上限的较早作品不再补发。"""

    ifollow_douyin_cookie: str | None = None
    """可选的抖音网页 Cookie。"""

    ifollow_media_max_mb: int = Field(default=100, ge=1, le=2048)
    """单个媒体文件的体积上限，超出则跳过该媒体。"""

    ifollow_use_base64: bool = False
    """是否把媒体编码成 base64 发送；仅当协议端与插件不在同一台机器时需要开启。"""

    ifollow_bilibili_cookie: str | None = None
    """可选的 B 站 Cookie（SESSDATA）；配置后才会轮询动态。"""


config = get_plugin_config(Config)
