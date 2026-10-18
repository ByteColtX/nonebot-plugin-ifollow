"""用 parsehub 解析 B 站作品并构造待发送的消息。

媒体管线和体积限制由 :mod:`media` 统一实现，本模块只负责传入 B 站 Cookie。

parsehub 的 B 站视频接口固定按 720p（``qn=64``）取流，Cookie 不会提升画质；
动态解析要求 ``t.bilibili.com/{id}`` 或 ``/opus/{id}`` 形式的链接。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from nonebot_plugin_alconna import UniMessage

from .config import config
from .media import media_messages_for


@asynccontextmanager
async def bilibili_media_messages(url: str) -> AsyncIterator[list[UniMessage]]:
    """解析并下载一个 B 站视频或动态，产出待发送的消息列表。

    媒体落在临时目录里，退出上下文时删除，因此调用方必须在上下文内把消息发完。

    Args:
        url: B 站视频或动态链接。

    Yields:
        消息列表，第一条是文案，其后是媒体。
    """

    async with media_messages_for(
        url,
        cookie=config.ifollow_bilibili_cookie,
        label="B站",
        temp_prefix="ifollow-bilibili-",
        max_bytes=config.ifollow_media_max_mb * 1024 * 1024,
    ) as messages:
        yield messages
