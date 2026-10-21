"""用 parsehub 解析作品并构造待发送的消息。

`parsehub <https://github.com/z-mio/ParseHub>`_ 负责解析作品详情并下载媒体，
本模块只做「解析 → 下载 → 拼成 ``UniMessage``」三件事，渲染与发送交给 alconna。

默认把媒体的本地路径交给协议端（``file://``），不读字节也不编码，事件循环没有负担。
只有协议端与插件不在同一台机器（它读不到本地路径）时，才开启 ``base64`` 传输，
把媒体编码成 base64 消息段发送。

视频类媒体在下载前会先探测远端体积，超过上限的直接不下载；探测失败或拿不到长度时，
退回下载后的体积检查。

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

import httpx
from nonebot import logger
from nonebot_plugin_alconna import UniMessage

from .settings import Settings
from .style import SEPARATOR

IMAGE_BATCH_SIZE = 9
"""单条消息最多拼接的图片数量，超出拆成多条发送。"""

MAX_MEDIA_COUNT = 20
"""单个作品最多发送的媒体数量，避免图集把群刷屏。"""

VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".flv", ".mkv"})
"""按后缀判断媒体是否为视频。"""

PROBE_SUFFIXES = VIDEO_SUFFIXES | {".gif"}
"""下载前探测体积的后缀：视频、动图，以及带 video_url 的实况照片。"""

PROBE_TIMEOUT_SECONDS = 10.0
"""单个体积探测请求的超时时间。"""

_hub: Any | None = None
_hub_lock = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class MediaPolicy:
    """一条作品的媒体处理策略，由配置换算一次后传下去。"""

    max_bytes: int
    max_count: int = MAX_MEDIA_COUNT
    image_batch: int = IMAGE_BATCH_SIZE
    base64_media: bool = False

    @classmethod
    def from_settings(cls, settings: Settings) -> MediaPolicy:
        """从运行期配置构造策略。"""

        return cls(
            max_bytes=settings.media_max_bytes,
            base64_media=settings.use_base64,
        )


@dataclass(frozen=True)
class WorkMeta:
    """一条作品在文案里需要的元信息。

    Attributes:
        author_name: 作者昵称。
        create_time: 发布时间，Unix 秒。
        duration_unit: parsehub 返回的时长单位，抖音是毫秒、B 站是秒。
        platform_name: 平台中文名，为空时文案不带头部前缀。
    """

    author_name: str
    create_time: int
    duration_unit: Literal["ms", "s"] = "s"
    platform_name: str = ""


@dataclass
class MediaBundle:
    """下载并拼好的媒体，以及文案里要用的统计。"""

    messages: list[UniMessage] = field(default_factory=list)
    count: int = 0
    total_bytes: int = 0
    width: int = 0
    height: int = 0
    video_seconds: int = 0
    skipped: int = 0
    """因超过体积上限而未发送的媒体数量，不含文件缺失。"""
    max_bytes: int = 0
    """体积上限，用于在文案里说明跳过原因。"""


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
        形如 ``平台 · 作者 发布了新作品`` + 分隔线 + 文案 + 参数行 + 发布时间的多行
        文案。
    """

    author = (
        f"{meta.platform_name} · {meta.author_name}"
        if meta.platform_name
        else meta.author_name
    )
    lines = [f"{author} 发布了新作品", SEPARATOR]
    text = _work_text(result)
    if text:
        lines.extend([text, ""])
    details = _detail_parts(bundle)
    if details:
        lines.append(" · ".join(details))
    if bundle.skipped:
        lines.append(
            f"另有 {bundle.skipped} 个媒体超过 "
            f"{bundle.max_bytes // 1024 // 1024}MB 未发送"
        )
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


def _parse_int(value: str | None) -> int | None:
    """把响应头里的数字解析成整数，解析不了返回 ``None``。"""

    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_content_range_total(value: str | None) -> int | None:
    """从 ``bytes 0-0/12345`` 中取出媒体总长度。"""

    if not value or "/" not in value:
        return None
    return _parse_int(value.rsplit("/", 1)[1])


async def _probe_remote_size(client: httpx.AsyncClient, url: str) -> int | None:
    """探测远端媒体体积：先 HEAD，拿不到长度时用 ``Range`` 回退。

    Args:
        client: 复用的 HTTP 客户端。
        url: 媒体直链。

    Returns:
        媒体字节数；探测失败或服务端不返回长度时返回 ``None``。
    """

    try:
        response = await client.head(
            url,
            headers={"Accept-Encoding": "identity"},
            follow_redirects=True,
        )
        size = _parse_int(response.headers.get("content-length"))
        if size:
            return size
        response = await client.get(
            url,
            headers={"Accept-Encoding": "identity", "Range": "bytes=0-0"},
            follow_redirects=True,
        )
    except Exception as exc:
        logger.debug(f"媒体体积探测失败，改由下载后检查：{exc}")
        return None
    if response.status_code == 206:
        total = _parse_content_range_total(response.headers.get("content-range"))
        if total:
            return total
    return _parse_int(response.headers.get("content-length"))


def _needs_size_probe(ref: Any) -> bool:
    """判断一个媒体引用是否值得在下载前探测体积。"""

    ext = str(getattr(ref, "ext", "") or "").lstrip(".").lower()
    if ext and f".{ext}" in PROBE_SUFFIXES:
        return True
    return bool(getattr(ref, "video_url", None))


async def _keep_by_size(
    client: httpx.AsyncClient, ref: Any, max_bytes: int, label: str
) -> bool:
    """探测一个媒体引用的体积，返回是否保留它。"""

    urls = [getattr(ref, "url", None), getattr(ref, "video_url", None)]
    for url in urls:
        if not url:
            continue
        size = await _probe_remote_size(client, str(url))
        if size is None:
            continue
        if max_bytes and size > max_bytes:
            logger.warning(
                f"{label}媒体（{getattr(ref, 'ext', '') or '未知'}）体积 "
                f"{_format_size(size)} 超过上限 {_format_size(max_bytes)}，跳过下载"
            )
            return False
    return True


async def _drop_oversized_media(result: Any, policy: MediaPolicy, label: str) -> int:
    """下载前剔除超限的视频类媒体，避免白下载。

    只探测视频、动图与实况照片；探测失败或拿不到长度时保留该媒体，由下载后的体积
    检查兜底。

    Args:
        result: parsehub 的解析结果，会就地修改它的 ``media``。
        policy: 媒体处理策略。
        label: 日志里使用的平台名。

    Returns:
        被剔除的媒体数量。
    """

    raw_media = getattr(result, "media", None)
    if raw_media is None:
        return 0
    is_sequence = isinstance(raw_media, Sequence)
    refs = list(raw_media) if is_sequence else [raw_media]
    targets = [ref for ref in refs if _needs_size_probe(ref)]
    if not targets:
        return 0
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        keep = await asyncio.gather(
            *(_keep_by_size(client, ref, policy.max_bytes, label) for ref in targets)
        )
    dropped = {id(ref) for ref, kept in zip(targets, keep) if not kept}
    if not dropped:
        return 0
    kept_refs = [ref for ref in refs if id(ref) not in dropped]
    if is_sequence:
        result.media = kept_refs or None
    else:
        result.media = kept_refs[0] if kept_refs else None
    return len(dropped)


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
    policy: MediaPolicy,
    label: str,
    duration_unit: Literal["ms", "s"] = "s",
    skipped: int = 0,
) -> MediaBundle:
    """把下载好的媒体拼成若干条消息：图片按批合并，视频单独发送。

    Args:
        files: parsehub 下载得到的媒体文件。
        policy: 媒体处理策略（体积上限、数量上限、图片批次、传输方式）。
        label: 日志里使用的平台名。
        duration_unit: parsehub 返回的时长单位。
        skipped: 下载前已因体积超限剔除的媒体数量。

    Returns:
        媒体消息与文案统计。
    """

    images: list[Path] = []
    bundle = MediaBundle(skipped=skipped, max_bytes=policy.max_bytes)
    selected = files[: policy.max_count]
    paths = [Path(str(getattr(file, "path", ""))) for file in selected]
    live_paths = [
        Path(str(file.video_path))
        for file in selected
        if getattr(file, "video_path", None)
    ]
    sizes = await asyncio.to_thread(_file_sizes, paths + live_paths)

    async def flush_images() -> None:
        while images:
            batch = images[: policy.image_batch]
            del images[: policy.image_batch]
            message = UniMessage()
            for path in batch:
                message += await _segments_for(path, base64_media=policy.base64_media)
            bundle.messages.append(message)

    for file, path in zip(selected, paths):
        size = sizes.get(path)
        if size is None:
            logger.debug(f"{label}媒体文件不存在，已跳过：{path}")
            continue
        if policy.max_bytes and size > policy.max_bytes:
            logger.warning(
                f"{label}媒体 {path.name} 超过 "
                f"{policy.max_bytes // 1024 // 1024}MB，已跳过"
            )
            bundle.skipped += 1
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
            bundle.messages.append(
                await _segments_for(path, base64_media=policy.base64_media)
            )
            bundle.video_seconds += _duration_seconds(
                getattr(file, "duration", 0), duration_unit
            )
            live_size = sizes.get(live_path)
            if live_size is not None:
                if not policy.max_bytes or live_size <= policy.max_bytes:
                    bundle.messages.append(
                        await _segments_for(live_path, base64_media=policy.base64_media)
                    )
                    bundle.total_bytes += live_size
                else:
                    bundle.skipped += 1
            continue

        if path.suffix.lower() in VIDEO_SUFFIXES:
            await flush_images()
            bundle.messages.append(
                await _segments_for(path, base64_media=policy.base64_media)
            )
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
    policy: MediaPolicy,
    skipped: int = 0,
) -> list[UniMessage]:
    """把解析结果与下载结果拼成待发送的消息列表。

    Args:
        result: parsehub 的解析结果。
        download: parsehub 的下载结果。
        meta: 作者与发布时间。
        label: 日志里使用的平台名。
        policy: 媒体处理策略。
        skipped: 下载前已因体积超限剔除的媒体数量。

    Returns:
        第一条是文案，其后是媒体消息。
    """

    bundle = await media_messages(
        downloaded_files(download), policy, label, meta.duration_unit, skipped
    )
    return [UniMessage(caption(meta, result, bundle)), *bundle.messages]


@asynccontextmanager
async def media_messages_for(
    meta: WorkMeta,
    url: str,
    *,
    policy: MediaPolicy,
    cookie: str | None,
    label: str,
    temp_prefix: str,
) -> AsyncIterator[list[UniMessage]]:
    """解析并下载一个作品，产出待发送的消息列表。

    媒体落在临时目录里，退出上下文时删除，因此调用方必须在上下文内把消息发完。

    Args:
        meta: 作者与发布时间。
        url: 作品链接。
        policy: 媒体处理策略。
        cookie: 对应平台的 Cookie。
        label: 日志里使用的平台名。
        temp_prefix: 临时目录前缀。

    Yields:
        消息列表，第一条是文案，其后是媒体。
    """

    workdir = Path(tempfile.mkdtemp(prefix=temp_prefix))
    try:
        result = await parse_work(url, cookie)
        skipped = 0
        if policy.max_bytes:
            skipped = await _drop_oversized_media(result, policy, label)
        download = await result.download(workdir)
        yield await build_messages(
            result, download, meta, label=label, policy=policy, skipped=skipped
        )
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, ignore_errors=True)
