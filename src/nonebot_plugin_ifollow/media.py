"""用 parsehub 解析作品并构造待发送的消息。

`parsehub <https://github.com/z-mio/ParseHub>`_ 负责解析作品详情并下载媒体，
本模块只做「解析 → 下载 → 拼成 ``UniMessage``」三件事，渲染与发送交给 alconna。

默认把媒体的本地路径交给协议端（``file://``），不读字节也不编码，事件循环没有负担。
只有协议端与插件不在同一台机器（它读不到本地路径）时，才开启 ``base64`` 传输，
把媒体编码成 base64 消息段发送。

parsehub 是同步库（内部用 requests），首次导入还要几秒（numpy/opencv），因此解析
调用统一放进线程执行，``ParseHub`` 实例全局复用。
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from base64 import b64encode
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from nonebot import logger
from nonebot_plugin_alconna import UniMessage

IMAGE_BATCH_SIZE = 9
"""单条消息最多拼接的图片数量，超出拆成多条发送。"""

MAX_MEDIA_COUNT = 20
"""单个作品最多发送的媒体数量，避免图集把群刷屏。"""

VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".flv", ".mkv"})
"""按后缀判断媒体是否为视频。"""

SEPARATOR = "─" * 12
"""文案里使用的分隔线，调用方拼订阅人提醒时也用它。"""

_hub: Any | None = None
_hub_lock = asyncio.Lock()


@dataclass(frozen=True)
class WorkMeta:
    """一条作品在文案里需要的元信息。

    Attributes:
        author_name: 作者昵称。
        create_time: 发布时间，Unix 秒。
        duration_unit: parsehub 返回的时长单位，抖音是毫秒、B 站是秒。
    """

    author_name: str
    create_time: int
    duration_unit: Literal["ms", "s"] = "s"


@dataclass
class MediaBundle:
    """下载并拼好的媒体，以及文案里要用的统计。"""

    messages: list[UniMessage] = field(default_factory=list)
    count: int = 0
    total_bytes: int = 0
    width: int = 0
    height: int = 0
    video_seconds: int = 0


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


def _work_text(result: Any) -> str:
    """取作品文案，标题与正文重复时只保留一次。"""

    parts: list[str] = []
    for text in (getattr(result, "title", ""), getattr(result, "content", "")):
        cleaned = str(text or "").strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    return "\n".join(parts)


def _duration_seconds(value: Any, unit: Literal["ms", "s"]) -> int:
    """把 parsehub 的时长换算成秒。"""

    try:
        duration = int(value or 0)
    except (TypeError, ValueError):
        return 0
    if duration <= 0:
        return 0
    return duration // 1000 if unit == "ms" else duration


def _format_duration(seconds: int) -> str:
    """把秒数格式化成 ``mm:ss`` 或 ``h:mm:ss``。"""

    minutes, secs = divmod(max(0, seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_size(num_bytes: int) -> str:
    """把字节数格式化成 MB / KB。"""

    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / 1024 / 1024:.1f} MB"
    return f"{max(1, round(num_bytes / 1024))} KB"


def _detail_parts(bundle: MediaBundle) -> list[str]:
    """拼出参数行：数量 / 时长 / 分辨率 / 体积。"""

    parts: list[str] = []
    if bundle.count > 1:
        parts.append(
            f"{bundle.count} 张" if not bundle.video_seconds else f"{bundle.count} 个"
        )
    if bundle.video_seconds:
        parts.append(_format_duration(bundle.video_seconds))
    if bundle.width and bundle.height:
        parts.append(f"{bundle.width}×{bundle.height}")
    if bundle.total_bytes:
        parts.append(_format_size(bundle.total_bytes))
    return parts


def caption(meta: WorkMeta, result: Any, bundle: MediaBundle) -> str:
    """拼出作品文案。

    Args:
        meta: 作者与发布时间。
        result: parsehub 的解析结果。
        bundle: 下载后的媒体统计。

    Returns:
        形如 ``作者 发布了新作品`` + 分隔线 + 文案 + 参数行 + 发布时间的多行文案。
    """

    lines = [f"{meta.author_name} 发布了新作品", SEPARATOR]
    text = _work_text(result)
    if text:
        lines.extend([text, ""])
    details = _detail_parts(bundle)
    if details:
        lines.append(" · ".join(details))
    if meta.create_time:
        stamp = datetime.fromtimestamp(meta.create_time).strftime("%Y-%m-%d %H:%M")
        lines.append(stamp)
    return "\n".join(lines)


def _file_sizes(paths: list[Path]) -> dict[Path, int]:
    """一次返回存在文件的体积，缺失的路径不出现在结果里。"""

    sizes: dict[Path, int] = {}
    for path in paths:
        try:
            sizes[path] = path.stat().st_size
        except OSError:
            continue
    return sizes


async def _segments_for(path: Path, *, base64_media: bool) -> UniMessage:
    """把本地媒体包装成图片或视频消息段。

    Args:
        path: 本地媒体文件。
        base64_media: 是否编码成 base64；默认直接把路径交给协议端。

    Returns:
        图片或视频消息段。
    """

    is_video = path.suffix.lower() in VIDEO_SUFFIXES
    if base64_media:
        url = await asyncio.to_thread(_base64_url, path)
        return UniMessage.video(url=url) if is_video else UniMessage.image(url=url)
    return UniMessage.video(path=path) if is_video else UniMessage.image(path=path)


def _base64_url(path: Path) -> str:
    """在线程里读文件并编码，避免大文件阻塞事件循环。"""

    return f"base64://{b64encode(path.read_bytes()).decode()}"


async def media_messages(
    files: list[Any],
    max_bytes: int,
    label: str,
    duration_unit: Literal["ms", "s"] = "s",
    *,
    base64_media: bool = False,
) -> MediaBundle:
    """把下载好的媒体拼成若干条消息：图片按批合并，视频单独发送。

    Args:
        files: parsehub 下载得到的媒体文件。
        max_bytes: 单个媒体的体积上限，``0`` 表示不限制。
        label: 日志里使用的平台名。
        duration_unit: parsehub 返回的时长单位。
        base64_media: 是否把媒体编码成 base64 发送。

    Returns:
        媒体消息与文案统计。
    """

    images: list[Path] = []
    bundle = MediaBundle()
    selected = files[:MAX_MEDIA_COUNT]
    paths = [Path(str(getattr(file, "path", ""))) for file in selected]
    live_paths = [
        Path(str(file.video_path))
        for file in selected
        if getattr(file, "video_path", None)
    ]
    sizes = await asyncio.to_thread(_file_sizes, paths + live_paths)

    async def flush_images() -> None:
        while images:
            batch = images[:IMAGE_BATCH_SIZE]
            del images[:IMAGE_BATCH_SIZE]
            message = UniMessage()
            for path in batch:
                message += await _segments_for(path, base64_media=base64_media)
            bundle.messages.append(message)

    for file, path in zip(selected, paths):
        size = sizes.get(path)
        if size is None:
            logger.debug(f"{label}媒体文件不存在，已跳过：{path}")
            continue
        if max_bytes and size > max_bytes:
            logger.warning(
                f"{label}媒体 {path.name} 超过 {max_bytes // 1024 // 1024}MB，已跳过"
            )
            continue

        bundle.count += 1
        bundle.total_bytes += size
        if not bundle.width and getattr(file, "width", 0):
            bundle.width = int(file.width)
            bundle.height = int(getattr(file, "height", 0) or 0)

        video_path = getattr(file, "video_path", None)
        if video_path:
            await flush_images()
            live_path = Path(str(video_path))
            bundle.messages.append(await _segments_for(path, base64_media=base64_media))
            bundle.video_seconds += _duration_seconds(
                getattr(file, "duration", 0), duration_unit
            )
            live_size = sizes.get(live_path)
            if live_size is not None and (not max_bytes or live_size <= max_bytes):
                bundle.messages.append(
                    await _segments_for(live_path, base64_media=base64_media)
                )
                bundle.total_bytes += live_size
            continue

        if path.suffix.lower() in VIDEO_SUFFIXES:
            await flush_images()
            bundle.messages.append(await _segments_for(path, base64_media=base64_media))
            bundle.video_seconds += _duration_seconds(
                getattr(file, "duration", 0), duration_unit
            )
        else:
            images.append(path)

    await flush_images()
    return bundle


async def build_messages(
    result: Any,
    download: Any,
    meta: WorkMeta,
    *,
    label: str,
    max_bytes: int,
    base64_media: bool = False,
) -> list[UniMessage]:
    """把解析结果与下载结果拼成待发送的消息列表。

    Args:
        result: parsehub 的解析结果。
        download: parsehub 的下载结果。
        meta: 作者与发布时间。
        label: 日志里使用的平台名。
        max_bytes: 单个媒体的体积上限。
        base64_media: 是否把媒体编码成 base64 发送。

    Returns:
        第一条是文案，其后是媒体消息。
    """

    bundle = await media_messages(
        downloaded_files(download),
        max_bytes,
        label,
        meta.duration_unit,
        base64_media=base64_media,
    )
    return [UniMessage(caption(meta, result, bundle)), *bundle.messages]


@asynccontextmanager
async def media_messages_for(
    meta: WorkMeta,
    url: str,
    *,
    cookie: str | None,
    label: str,
    temp_prefix: str,
    max_bytes: int,
    base64_media: bool = False,
) -> AsyncIterator[list[UniMessage]]:
    """解析并下载一个作品，产出待发送的消息列表。

    媒体落在临时目录里，退出上下文时删除，因此调用方必须在上下文内把消息发完。

    Args:
        meta: 作者与发布时间。
        url: 作品链接。
        cookie: 对应平台的 Cookie。
        label: 日志里使用的平台名。
        temp_prefix: 临时目录前缀。
        max_bytes: 单个媒体的体积上限，``0`` 表示不限制。
        base64_media: 是否把媒体编码成 base64 发送。

    Yields:
        消息列表，第一条是文案，其后是媒体。
    """

    workdir = Path(tempfile.mkdtemp(prefix=temp_prefix))
    try:
        result = await parse_work(url, cookie)
        download = await result.download(workdir)
        yield await build_messages(
            result,
            download,
            meta,
            label=label,
            max_bytes=max_bytes,
            base64_media=base64_media,
        )
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, ignore_errors=True)
