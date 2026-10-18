"""B 站作者投稿和动态接口适配。"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

import httpx
from bilibili_api import Credential
from bilibili_api.user import User
from bilibili_api.utils.network import get_buvid
from bilibili_api.video import Video
from nonebot import logger

from .config import config
from .platforms import (
    AuthorInfo,
    Cursor,
    PlatformWork,
    page_reaches_cursor,
)

PROFILE_RE = re.compile(
    r"(?:https?://)?(?:(?:www\.)?space\.bilibili\.com/|m\.bilibili\.com/space/)"
    r"(?P<mid>\d+)"
)
BV_RE = re.compile(r"(?P<bvid>BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"(?:^|[/\s])av(?P<avid>\d+)(?:$|[/\s?&#])", re.IGNORECASE)
SHORT_LINK_RE = re.compile(r"(?:https?://)?b23\.tv/[A-Za-z0-9_-]+")
BILIBILI_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
}
BILIBILI_APP_API = "https://app.bilibili.com"
BILIBILI_APP_CREDENTIALS = (
    ("1d8b6e7d45233436", "560c52ccd288fed045859ed18bffd973"),
    ("783bbb7264451d82", "2653583c8873dea268ab9386918b1d65"),
)
BILIBILI_APP_BUILD = "8130300"
BILIBILI_APP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 BiliDroid/8.13.0 (bbcallen@gmail.com) os/android "
        "model/Pixel Build/UP1A"
    ),
}
JOINT_CREATION_SUFFIX = " 等联合创作"


class BilibiliRequestError(RuntimeError):
    """B 站接口不可用或返回了无法识别的数据。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def bilibili_dynamics_enabled() -> bool:
    """动态接口匿名不稳定，仅在配置 B 站 Cookie 时轮询。"""

    return bool(config.ifollow_bilibili_cookie)


async def warm_up_bilibili() -> None:
    """预热 B 站匿名 buvid3/buvid4，失败不影响后续请求。"""

    try:
        await get_buvid()
    except Exception:
        logger.opt(exception=True).warning("B 站 buvid3 预热失败，后续请求将按需重试")


def _sign_app_params(
    params: dict[str, str], appkey: str, appsec: str
) -> dict[str, str]:
    """为 B 站 APP 接口参数追加 appkey、ts 和 sign。"""

    signed = {
        "appkey": appkey,
        "build": BILIBILI_APP_BUILD,
        "mobi_app": "android",
        "platform": "android",
        "ts": str(int(time.time())),
        **params,
    }
    query = "&".join(f"{key}={signed[key]}" for key in sorted(signed))
    signed["sign"] = hashlib.md5(f"{query}{appsec}".encode()).hexdigest()
    return signed


def _app_client() -> httpx.AsyncClient:
    """创建 B 站 APP 接口使用的 HTTP 客户端。"""

    return httpx.AsyncClient(headers=BILIBILI_APP_HEADERS, timeout=20)


async def _app_get(
    client: httpx.AsyncClient, path: str, params: dict[str, str]
) -> dict[str, Any]:
    """调用 B 站 APP 接口，失败时轮换 appkey，返回 data 字段。"""

    last_error: Exception | None = None
    for appkey, appsec in BILIBILI_APP_CREDENTIALS:
        try:
            response = await client.get(
                f"{BILIBILI_APP_API}{path}",
                params=_sign_app_params(params, appkey, appsec),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            last_error = exc
            continue
        if not isinstance(payload, dict):
            last_error = BilibiliRequestError("B 站 APP 接口返回格式异常")
            continue
        code = payload.get("code")
        if code != 0:
            last_error = BilibiliRequestError(
                f"B 站 APP 接口返回 {code}：{payload.get('message') or ''}",
                retryable=code in (-500, -503, -509, -799),
            )
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            last_error = BilibiliRequestError("B 站 APP 接口缺少 data")
            continue
        return data
    if isinstance(last_error, BilibiliRequestError):
        raise last_error
    raise _wrap_error("B 站 APP 接口请求失败", last_error or RuntimeError("未知错误"))


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
        mid = extract_bilibili_mid(value)
        if mid:
            return await fetch_author_profile(mid)
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

    matched = SHORT_LINK_RE.search(value)
    if matched is None:
        return value
    short_url = matched.group(0)
    if not short_url.startswith(("http://", "https://")):
        short_url = f"https://{short_url}"
    try:
        async with httpx.AsyncClient(
            headers=BILIBILI_BROWSER_HEADERS,
            follow_redirects=True,
            timeout=20,
        ) as client:
            response = await client.get(short_url)
            response.raise_for_status()
            return str(response.url)
    except Exception as exc:
        raise _wrap_error("B 站短链解析失败", exc) from exc


async def fetch_author_profile(mid: str) -> AuthorInfo:
    """获取 B 站作者资料，优先使用匿名可用的 APP 接口。"""

    try:
        return await _fetch_author_profile_app(mid)
    except BilibiliRequestError as exc:
        logger.warning(f"B 站 APP 作者资料接口失败，回退网页接口：{exc}")
    return await _fetch_author_profile_web(mid)


async def _fetch_author_profile_app(mid: str) -> AuthorInfo:
    """通过 APP 接口获取 B 站作者资料。"""

    async with _app_client() as client:
        data = await _app_get(client, "/x/v2/space", {"vmid": mid})
    card = data.get("card")
    if not isinstance(card, dict) or not card.get("mid"):
        raise BilibiliRequestError("B 站 APP 作者资料返回格式异常")
    return AuthorInfo(
        author_id=str(card["mid"]),
        name=str(card.get("name") or card["mid"]),
        handle=str(card.get("mid") or ""),
        avatar=str(card.get("face") or ""),
        description=str(card.get("sign") or ""),
    )


async def _fetch_author_profile_web(mid: str) -> AuthorInfo:
    """通过网页接口获取 B 站作者资料。"""

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


async def fetch_video_works(
    mid: str, cursor: Cursor | None = None
) -> list[PlatformWork]:
    """读取 B 站作者视频投稿，返回从新到旧的列表。"""

    try:
        return await _fetch_video_works_app(mid, cursor)
    except BilibiliRequestError as exc:
        logger.warning(f"B 站 APP 投稿接口失败，回退网页接口：{exc}")
    return await _fetch_video_works_web(mid, cursor)


async def _fetch_video_works_app(
    mid: str, cursor: Cursor | None = None
) -> list[PlatformWork]:
    """通过 APP 接口读取 B 站作者视频投稿。"""

    works: list[PlatformWork] = []
    seen: set[str] = set()
    aid: int | None = None
    limit = config.ifollow_sub_max_items
    async with _app_client() as client:
        while True:
            params = {"vmid": mid, "order": "pubdate"}
            if aid is not None:
                params["aid"] = str(aid)
            data = await _app_get(client, "/x/v2/space/archive/cursor", params)
            items = data.get("item") or []
            if not isinstance(items, list):
                raise BilibiliRequestError("B 站 APP 投稿列表缺少 item")
            page: list[PlatformWork] = []
            for item in items:
                work = _parse_app_video_item(item, mid)
                if work is None or work.work_id in seen:
                    continue
                seen.add(work.work_id)
                page.append(work)
            works.extend(page)
            if not page or not data.get("has_next"):
                break
            if cursor is None or len(works) >= limit:
                break
            if page_reaches_cursor(page, cursor):
                break
            last_item = items[-1] if isinstance(items[-1], dict) else {}
            last_aid = last_item.get("param")
            if not last_aid:
                break
            aid = int(last_aid)
    works.sort(key=lambda work: (work.create_time, work.work_id), reverse=True)
    return works


def _parse_app_video_item(item: Any, mid: str) -> PlatformWork | None:
    """把 APP 投稿条目转换为统一作品模型。"""

    if not isinstance(item, dict):
        return None
    bvid = str(item.get("bvid") or "")
    if not bvid:
        return None
    name = str(item.get("author") or "")
    if name.endswith(JOINT_CREATION_SUFFIX):
        name = name[: -len(JOINT_CREATION_SUFFIX)]
    return PlatformWork(
        platform="bilibili",
        work_id=bvid,
        bvid=bvid,
        url=f"https://www.bilibili.com/video/{bvid}",
        title=str(item.get("title") or ""),
        description=str(item.get("subtitle") or ""),
        create_time=int(item.get("ctime") or 0),
        author=AuthorInfo(author_id=mid, name=name, handle=mid),
    )


async def _fetch_video_works_web(
    mid: str, cursor: Cursor | None = None
) -> list[PlatformWork]:
    """通过网页接口读取 B 站作者视频投稿。"""

    user = User(int(mid), credential=_credential())
    works: list[PlatformWork] = []
    oid: int | None = None
    author: AuthorInfo | None = None
    limit = config.ifollow_sub_max_items
    while True:
        try:
            result = await user.get_media_list(
                oid=oid,
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
        page: list[PlatformWork] = []
        for item in items:
            work = _parse_video_item(item, mid, author)
            if work is None:
                continue
            author = work.author
            page.append(work)
        works.extend(page)
        if not page or not result.get("has_more"):
            break
        if cursor is None or len(works) >= limit:
            break
        if page_reaches_cursor(page, cursor):
            break
        last_item = items[-1] if isinstance(items[-1], dict) else {}
        last_aid = last_item.get("id")
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
    mid: str, cursor: Cursor | None = None
) -> list[PlatformWork]:
    """读取 B 站作者动态，返回从新到旧的列表。"""

    user = User(int(mid), credential=_credential())
    works: list[PlatformWork] = []
    offset = ""
    limit = config.ifollow_sub_max_items
    while True:
        try:
            result = await user.get_dynamics_new(offset=offset)
        except Exception as exc:
            raise _wrap_error("B 站动态列表请求失败", exc) from exc
        if not isinstance(result, dict):
            raise BilibiliRequestError("B 站动态列表返回格式异常")
        items = result.get("items") or []
        if not isinstance(items, list):
            raise BilibiliRequestError("B 站动态列表缺少 items")
        page = [work for item in items if (work := _parse_dynamic_item(item, mid))]
        works.extend(page)
        if not page or not result.get("has_more"):
            break
        if cursor is None or len(works) >= limit:
            break
        if page_reaches_cursor(page, cursor):
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
    url = f"https://t.bilibili.com/{dynamic_id}"
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
