"""用 parsehub 解析抖音作品并构造待发送的消息。

媒体管线和体积限制由 :mod:`media` 统一实现，本模块只负责传入抖音 Cookie。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from nonebot_plugin_alconna import UniMessage

from .config import config
from .media import media_messages_for


@asynccontextmanager
async def douyin_media_messages(url: str) -> AsyncIterator[list[UniMessage]]:
    """解析并下载一个抖音作品，产出待发送的消息列表。

    媒体落在临时目录里，退出上下文时删除，因此调用方必须在上下文内把消息发完。

    Args:
        url: 抖音作品链接。

    Yields:
        消息列表，第一条是文案，其后是媒体。
    """

    async with media_messages_for(
        url,
        cookie=config.ifollow_douyin_cookie,
        label="抖音",
        temp_prefix="ifollow-douyin-",
        max_bytes=config.ifollow_media_max_mb * 1024 * 1024,
    ) as messages:
        yield messages
