"""B 站作者投稿和动态接口适配。"""

from __future__ import annotations

import re
from typing import Any

from bilibili_api import Credential
from bilibili_api.user import User
from bilibili_api.video import Video

from .config import config
from .platforms import AuthorInfo, PlatformWork

PROFILE_RE = re.compile(r"(?:https?://)?(?:www\.)?space\.bilibili\.com/(?P<mid>\d+)")
BV_RE = re.compile(r"(?P<bvid>BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"(?:^|[/\s])av(?P<avid>\d+)(?:$|[/\s?&#])", re.IGNORECASE)
SHORT_LINK_RE = re.compile(r"https?://b23\.tv/[\w-]+")
MAX_CATCHUP_PAGES = 5


class BilibiliRequestError(RuntimeError):
    """B 站接口不可用或返回了无法识别的数据。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def extract_bilibili_mid(value: str) -> str | None:
    """从 B 站作者主页中提取 UID。"""

    value = value.strip().strip("<>[]()")
    matched = PROFILE_RE.search(value)
    return matched.group("mid") if matched else None


def extract_bilibili_video(value: str) -> tuple[str, str] | None:
    """从 BV/AV 链接中提取视频标识。"""

    if matched := BV_RE.search(value):
        return "bvid", matched.group("bvid")
    if matched := AV_RE.search(value):
        return "avid", matched.group("avid")
    return None


def is_bilibili_value(value: str) -> bool:
    """判断输入是否明显属于 B 站。"""

    return bool(
        extract_bilibili_mid(value)
        or extract_bilibili_video(value)
        or SHORT_LINK_RE.search(value)
        or "bilibili.com" in value
        or "b23.tv" in value
    )


async def resolve_bilibili_author(value: str) -> AuthorInfo:
    """解析 B 站作者主页或视频链接，返回作者资料。"""

    mid = extract_bilibili_mid(value)
    if mid:
        return await fetch_author_profile(mid)

    video = extract_bilibili_video(value)
    if video is None and SHORT_LINK_RE.search(value):
        value = await _resolve_short_link(value)
        video = extract_bilibili_video(value)
    if video is None:
        raise ValueError("请输入 B 站作者主页、BV/AV 视频链接或 b23.tv 短链")

    try:
        kind, identifier = video
        bili_video = Video(
            bvid=identifier if kind == "bvid" else None,
            aid=int(identifier) if kind == "avid" else None,
            credential=_credential(),
        )
        info = await bili_video.get_info()
    except Exception as exc:
        raise _wrap_error("B 站作品详情请求失败", exc) from exc
    owner = info.get("owner")
    if not isinstance(owner, dict) or not owner.get("mid"):
        raise BilibiliRequestError("B 站作品详情缺少作者信息")
    return AuthorInfo(
        author_id=str(owner["mid"]),
        name=str(owner.get("name") or owner["mid"]),
        handle=str(owner["mid"]),
        avatar=str(owner.get("face") or ""),
    )


async def _resolve_short_link(value: str) -> str:
    """解析 b23.tv 短链。"""

    import httpx

    matched = SHORT_LINK_RE.search(value)
    if matched is None:
        return value
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=20, verify=False
        ) as client:
            response = await client.get(matched.group(0))
            response.raise_for_status()
            return str(response.url)
    except Exception as exc:
        raise _wrap_error("B 站短链解析失败", exc) from exc


async def fetch_author_profile(mid: str) -> AuthorInfo:
    """获取 B 站作者资料。"""

    try:
        info = await User(int(mid), credential=_credential()).get_user_info()
    except Exception as exc:
        raise _wrap_error("B 站作者资料请求失败", exc) from exc
    if not isinstance(info, dict) or not info.get("mid"):
        raise BilibiliRequestError("B 站作者资料返回格式异常")
    return AuthorInfo(
        author_id=str(info["mid"]),
        name=str(info.get("name") or info["mid"]),
        handle=str(info.get("mid") or ""),
        avatar=str(info.get("face") or ""),
        description=str(info.get("sign") or ""),
    )


async def fetch_video_works(mid: str, cursor: str | None = None) -> list[PlatformWork]:
    """读取 B 站作者视频投稿，返回从新到旧的列表。"""

    user = User(int(mid), credential=_credential())
    works: list[PlatformWork] = []
    oid: int | None = None
    author: AuthorInfo | None = None
    for _ in range(MAX_CATCHUP_PAGES if cursor else 1):
        try:
            result = await user.get_media_list(
                oid=oid,
                ps=config.ifollow_subscribe_count,
                direction=False,
                desc=True,
                with_current=False,
            )
        except Exception as exc:
            raise _wrap_error("B 站投稿列表请求失败", exc) from exc
        if not isinstance(result, dict):
            raise BilibiliRequestError("B 站投稿列表返回格式异常")
        items = result.get("media_list") or []
        if not isinstance(items, list):
            raise BilibiliRequestError("B 站投稿列表缺少 media_list")
        for item in items:
            work = _parse_video_item(item, mid, author)
            if work is None:
                continue
            author = work.author
            works.append(work)
        if not items or not result.get("has_more"):
            break
        if cursor and any(str(item.get("bv_id") or "") == cursor for item in items):
            break
        last_aid = items[-1].get("id")
        if not last_aid:
            break
        oid = int(last_aid)
    works.sort(key=lambda work: (work.create_time, work.work_id), reverse=True)
    return works


def _parse_video_item(
    item: Any, mid: str, author: AuthorInfo | None
) -> PlatformWork | None:
    if not isinstance(item, dict):
        return None
    bvid = str(item.get("bv_id") or "")
    if not bvid:
        return None
    item_author = item.get("upper")
    if isinstance(item_author, dict):
        author = AuthorInfo(
            author_id=str(item_author.get("mid") or mid),
            name=str(item_author.get("name") or mid),
            handle=str(item_author.get("mid") or mid),
            avatar=str(item_author.get("face") or ""),
        )
    if author is None:
        author = AuthorInfo(author_id=mid, name=mid, handle=mid)
    return PlatformWork(
        platform="bilibili",
        work_id=bvid,
        bvid=bvid,
        url=f"https://www.bilibili.com/video/{bvid}",
        title=str(item.get("title") or ""),
        description=str(item.get("intro") or ""),
        create_time=int(item.get("pubtime") or 0),
        author=author,
    )


async def fetch_dynamic_works(
    mid: str, cursor: str | None = None
) -> list[PlatformWork]:
    """读取 B 站作者动态，返回从新到旧的列表。"""

    user = User(int(mid), credential=_credential())
    works: list[PlatformWork] = []
    offset = ""
    for _ in range(MAX_CATCHUP_PAGES if cursor else 1):
        try:
            result = await user.get_dynamics_new(offset=offset)
        except Exception as exc:
            raise _wrap_error("B 站动态列表请求失败", exc) from exc
        if not isinstance(result, dict):
            raise BilibiliRequestError("B 站动态列表返回格式异常")
        items = result.get("items") or []
        if not isinstance(items, list):
            raise BilibiliRequestError("B 站动态列表缺少 items")
        for item in items:
            work = _parse_dynamic_item(item, mid)
            if work is not None:
                works.append(work)
        if not items or not result.get("has_more"):
            break
        if cursor and any(str(item.get("id_str") or "") == cursor for item in items):
            break
        next_offset = str(result.get("offset") or "")
        if not next_offset or next_offset == offset:
            break
        offset = next_offset
    works.sort(key=lambda work: (work.create_time, work.work_id), reverse=True)
    return works


def _parse_dynamic_item(item: Any, mid: str) -> PlatformWork | None:
    if not isinstance(item, dict):
        return None
    dynamic_id = str(item.get("id_str") or "")
    if not dynamic_id:
        return None
    modules = item.get("modules") or {}
    if not isinstance(modules, dict):
        modules = {}
    author_data = modules.get("module_author") or {}
    if not isinstance(author_data, dict):
        author_data = {}
    author = AuthorInfo(
        author_id=str(author_data.get("mid") or mid),
        name=str(author_data.get("name") or mid),
        handle=str(author_data.get("mid") or mid),
        avatar=str(author_data.get("face") or ""),
    )
    published_at = int(author_data.get("pub_ts") or 0)
    dynamic = modules.get("module_dynamic") or {}
    if not isinstance(dynamic, dict):
        dynamic = {}
    major = dynamic.get("major") or {}
    if not isinstance(major, dict):
        major = {}
    archive = major.get("archive")
    if isinstance(archive, dict) and archive.get("bvid"):
        bvid = str(archive["bvid"])
        return PlatformWork(
            platform="bilibili",
            work_id=dynamic_id,
            bvid=bvid,
            url=f"https://www.bilibili.com/video/{bvid}",
            title=str(archive.get("title") or ""),
            description=str(archive.get("desc") or ""),
            create_time=published_at,
            author=author,
            kind="video",
        )
    opus = major.get("opus")
    url = f"https://www.bilibili.com/dynamic/{dynamic_id}"
    title = ""
    description = ""
    if isinstance(opus, dict):
        jump_url = opus.get("jump_url")
        if isinstance(jump_url, str) and jump_url:
            url = f"https:{jump_url}" if jump_url.startswith("//") else jump_url
        title = str(opus.get("title") or "")
        summary = opus.get("summary")
        if isinstance(summary, dict):
            description = str(summary.get("text") or "")
    dynamic_desc = dynamic.get("desc")
    if not description and isinstance(dynamic_desc, dict):
        description = str(dynamic_desc.get("text") or "")
    return PlatformWork(
        platform="bilibili",
        work_id=dynamic_id,
        url=url,
        title=title,
        description=description,
        create_time=published_at,
        author=author,
        kind="dynamic",
    )


def _credential() -> Credential | None:
    raw_cookie = config.ifollow_bilibili_cookie
    if not raw_cookie:
        return None
    return Credential.from_cookies(_parse_cookie(raw_cookie))


def _parse_cookie(raw_cookie: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for item in raw_cookie.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name:
            cookies[name] = value
    return cookies


def _wrap_error(prefix: str, exc: Exception) -> BilibiliRequestError:
    message = f"{prefix}：{exc}"
    text = str(exc)
    retryable = any(
        code in text for code in ("HTTP 412", "403", "429", "500", "502", "503", "504")
    )
    return BilibiliRequestError(message, retryable=retryable)
