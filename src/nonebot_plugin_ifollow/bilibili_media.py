"""用 parsehub 解析 B 站作品并构造待发送的消息。

parsehub 的 B 站视频接口固定按 720p（``qn=64``）取流，Cookie 不会提升画质；
动态解析要求 ``t.bilibili.com/{id}`` 或 ``/opus/{id}`` 形式的链接。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from nonebot_plugin_alconna import UniMessage

from .media import MediaPolicy, WorkMeta, media_messages_for
from .platforms import PlatformWork
from .settings import Settings


@asynccontextmanager
async def bilibili_media_messages(
    work: PlatformWork, settings: Settings
) -> AsyncIterator[list[UniMessage]]:
    """解析并下载一个 B 站视频或动态，产出待发送的消息列表。

    媒体落在临时目录里，退出上下文时删除，因此调用方必须在上下文内把消息发完。

    Args:
        work: 待发送的 B 站作品。
        settings: 运行期配置。

    Yields:
        消息列表，第一条是文案，其后是媒体。
    """

    meta = WorkMeta(
        author_name=work.author.name or work.author.author_id,
        create_time=work.create_time,
        duration_unit="s",
    )
    async with media_messages_for(
        meta,
        work.url,
        policy=MediaPolicy.from_settings(settings),
        cookie=settings.bilibili_cookie,
        label="B站",
        temp_prefix="ifollow-bilibili-",
    ) as messages:
        yield messages
