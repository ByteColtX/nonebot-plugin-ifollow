"""用 parsehub 解析作品并构造待发送的消息。

`parsehub <https://github.com/z-mio/ParseHub>`_ 负责解析作品详情并下载媒体，
本模块只做「解析 → 下载 → 拼成 ``UniMessage``」三件事，渲染与发送交给 alconna。

parsehub 是同步库（内部用 requests），首次导入还要几秒（numpy/opencv），因此解析
调用统一放进线程执行，``ParseHub`` 实例全局复用。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from nonebot import logger
from nonebot_plugin_alconna import UniMessage

IMAGE_BATCH_SIZE = 9
"""单条消息最多拼接的图片数量，超出拆成多条发送。"""

MAX_MEDIA_COUNT = 20
"""单个作品最多发送的媒体数量，避免图集把群刷屏。"""

VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".flv", ".mkv"})
"""按后缀判断媒体是否为视频。"""

_hub: Any | None = None
_hub_lock = asyncio.Lock()


def _import_parsehub() -> Any:
    """导入 parsehub 并返回实例，供线程调用。"""

    from parsehub import ParseHub

    return ParseHub()


async def parsehub() -> Any:
    """返回全局复用的 ``ParseHub`` 实例，必要时先导入。"""

    global _hub
    if _hub is None:
        async with _hub_lock:
            if _hub is None:
                _hub = await asyncio.to_thread(_import_parsehub)
    return _hub


async def parse_work(url: str, cookie: str | None) -> Any:
    """解析作品链接，返回 parsehub 的解析结果。

    Args:
        url: 作品链接。
        cookie: 对应平台的 Cookie，未配置时传 ``None``。

    Returns:
        parsehub 的解析结果。
    """

    hub = await parsehub()
    return await asyncio.to_thread(hub.parse_sync, url, cookie=cookie or None)


def downloaded_files(download: Any) -> list[Any]:
    """把 ``DownloadResult.media`` 统一成列表。"""

    media = getattr(download, "media", None)
    if media is None:
        return []
    if isinstance(media, Sequence):
        return list(media)
    return [media]


def caption(result: Any, url: str) -> str:
    """拼出作品文案，标题与正文重复时只保留一次。"""

    parts: list[str] = []
    for text in (getattr(result, "title", ""), getattr(result, "content", "")):
        cleaned = str(text or "").strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    if url and url not in parts:
        parts.append(url)
    return "\n".join(parts)


def _segments_for(path: Path) -> UniMessage:
    """按后缀把本地媒体包装成图片或视频消息段。"""

    if path.suffix.lower() in VIDEO_SUFFIXES:
        return UniMessage.video(path=path)
    return UniMessage.image(path=path)


def media_messages(files: list[Any], max_bytes: int, label: str) -> list[UniMessage]:
    """把下载好的媒体拼成若干条消息：图片按批合并，视频单独发送。

    Args:
        files: parsehub 下载得到的媒体文件。
        max_bytes: 单个媒体的体积上限，``0`` 表示不限制。
        label: 日志里使用的平台名。

    Returns:
        待发送的媒体消息列表。
    """

    images: list[Path] = []
    messages: list[UniMessage] = []

    def flush_images() -> None:
        while images:
            batch = images[:IMAGE_BATCH_SIZE]
            del images[:IMAGE_BATCH_SIZE]
            message = UniMessage()
            for path in batch:
                message += _segments_for(path)
            messages.append(message)

    for file in files[:MAX_MEDIA_COUNT]:
        path = Path(str(getattr(file, "path", "")))
        if not path.is_file():
            logger.debug(f"{label}媒体文件不存在，已跳过：{path}")
            continue
        if max_bytes and path.stat().st_size > max_bytes:
            logger.warning(
                f"{label}媒体 {path.name} 超过 {max_bytes // 1024 // 1024}MB，已跳过"
            )
            continue

        video_path = getattr(file, "video_path", None)
        if video_path:
            flush_images()
            live_path = Path(str(video_path))
            messages.append(UniMessage.image(path=path))
            live_size = live_path.stat().st_size if live_path.is_file() else None
            if live_size is not None and (not max_bytes or live_size <= max_bytes):
                messages.append(UniMessage.video(path=live_path))
            continue

        if path.suffix.lower() in VIDEO_SUFFIXES:
            flush_images()
            messages.append(_segments_for(path))
        else:
            images.append(path)

    flush_images()
    return messages


def build_messages(
    result: Any,
    download: Any,
    url: str,
    *,
    label: str,
    max_bytes: int,
) -> list[UniMessage]:
    """把解析结果与下载结果拼成待发送的消息列表。

    Args:
        result: parsehub 的解析结果。
        download: parsehub 的下载结果。
        url: 作品链接。
        label: 日志里使用的平台名。
        max_bytes: 单个媒体的体积上限。

    Returns:
        第一条是文案，其后是媒体消息。
    """

    messages = [UniMessage(caption(result, url))]
    messages.extend(media_messages(downloaded_files(download), max_bytes, label))
    return messages


@asynccontextmanager
async def media_messages_for(
    url: str,
    *,
    cookie: str | None,
    label: str,
    temp_prefix: str,
    max_bytes: int,
) -> AsyncIterator[list[UniMessage]]:
    """解析并下载一个作品，产出待发送的消息列表。

    媒体落在临时目录里，退出上下文时删除，因此调用方必须在上下文内把消息发完。

    Args:
        url: 作品链接。
        cookie: 对应平台的 Cookie。
        label: 日志里使用的平台名。
        temp_prefix: 临时目录前缀。
        max_bytes: 单个媒体的体积上限，``0`` 表示不限制。

    Yields:
        消息列表，第一条是文案，其后是媒体。
    """

    workdir = Path(tempfile.mkdtemp(prefix=temp_prefix))
    try:
        result = await parse_work(url, cookie)
        download = await result.download(workdir)
        yield build_messages(result, download, url, label=label, max_bytes=max_bytes)
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, ignore_errors=True)
