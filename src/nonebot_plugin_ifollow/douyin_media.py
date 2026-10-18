"""用 parsehub 解析抖音作品并构造待发送的消息。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from nonebot_plugin_alconna import UniMessage

from .media import MediaPolicy, WorkMeta, media_messages_for
from .platforms import PlatformWork
from .settings import Settings
from .style import platform_name


@asynccontextmanager
async def douyin_media_messages(
    work: PlatformWork, settings: Settings
) -> AsyncIterator[list[UniMessage]]:
    """解析并下载一个抖音作品，产出待发送的消息列表。

    媒体落在临时目录里，退出上下文时删除，因此调用方必须在上下文内把消息发完。

    Args:
        work: 待发送的抖音作品。
        settings: 运行期配置。

    Yields:
        消息列表，第一条是文案，其后是媒体。
    """

    meta = WorkMeta(
        author_name=work.author.name or work.author.author_id,
        create_time=work.create_time,
        duration_unit="ms",
        platform_name=platform_name("douyin"),
    )
    async with media_messages_for(
        meta,
        work.url,
        policy=MediaPolicy.from_settings(settings),
        cookie=settings.douyin_cookie,
        label="抖音",
        temp_prefix="ifollow-douyin-",
    ) as messages:
        yield messages
