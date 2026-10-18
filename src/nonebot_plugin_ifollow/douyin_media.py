"""用 parsehub 解析抖音作品并构造待发送的消息。

媒体管线和体积限制由 :mod:`media` 统一实现，本模块只负责传入抖音 Cookie
和时长单位（抖音接口给的是毫秒）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from nonebot_plugin_alconna import UniMessage

from .config import config
from .media import WorkMeta, media_messages_for
from .platforms import PlatformWork


@asynccontextmanager
async def douyin_media_messages(work: PlatformWork) -> AsyncIterator[list[UniMessage]]:
    """解析并下载一个抖音作品，产出待发送的消息列表。

    媒体落在临时目录里，退出上下文时删除，因此调用方必须在上下文内把消息发完。

    Args:
        work: 待发送的抖音作品。

    Yields:
        消息列表，第一条是文案，其后是媒体。
    """

    meta = WorkMeta(
        author_name=work.author.name or work.author.author_id,
        create_time=work.create_time,
        duration_unit="ms",
    )
    async with media_messages_for(
        meta,
        work.url,
        cookie=config.ifollow_douyin_cookie,
        label="抖音",
        temp_prefix="ifollow-douyin-",
        max_bytes=config.ifollow_media_max_mb * 1024 * 1024,
        base64_media=config.ifollow_use_base64,
    ) as messages:
        yield messages
