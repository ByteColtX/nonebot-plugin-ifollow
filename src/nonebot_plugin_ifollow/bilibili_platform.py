"""B 站作者资料、投稿与动态接口适配。"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import replace
from typing import Any

import httpx
from bilibili_api import Credential
from bilibili_api.user import User
from bilibili_api.utils.network import get_buvid
from bilibili_api.video import Video
from nonebot import logger

from .errors import PermanentError, TransientError
from .pagination import Page, paginate
from .platforms import (
    AuthorInfo,
    Cursor,
    FeedKind,
    PlatformWork,
    ResolvedAuthor,
)
from .settings import Settings

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
PERMANENT_APP_CODES = frozenset({-404, 62002, 62012})
"""APP 接口表示作者/内容不存在或不可见的返回码。"""

platform = "bilibili"


def matches(value: str) -> bool:
    """判断输入是否属于 B 站。"""

    return value.strip().isdigit() or is_bilibili_value(value)


def feeds(settings: Settings) -> tuple[FeedKind, ...]:
    """返回本平台要轮询的 feed；动态只在配置 Cookie 后启用。"""

    if settings.dynamics_enabled:
        return ("video", "dynamic")
    return ("video",)


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


async def warm_up() -> None:
    """预热 B 站匿名 buvid3/buvid4，失败不影响后续请求。"""

    try:
        await get_buvid()
    except Exception:
        logger.opt(exception=True).warning("B 站 buvid3 预热失败，后续请求将按需重试")


async def resolve_author(settings: Settings, value: str) -> ResolvedAuthor:
    """解析作者主页或视频链接，并返回首页内容用于初始化游标。"""

    mid = extract_bilibili_mid(value)
    if mid is None and value.strip().isdigit():
        mid = value.strip()
    if mid is None:
        mid = await _author_id_from_video(settings, value)
    author = await fetch_author_profile(settings, mid)
    feeds: dict[FeedKind, list[PlatformWork]] = {}
    video = await fetch(settings, mid, "video", None)
    feeds["video"] = video
    if settings.dynamics_enabled:
        try:
            feeds["dynamic"] = await fetch(settings, mid, "dynamic", None)
        except TransientError as exc:
            logger.warning(f"B 站动态首页拉取失败，订阅时跳过动态：{exc}")
    # 订阅名只能取作者资料：最新作品的作者字段在联合创作时是合作方。
    return ResolvedAuthor(author, feeds)


# PlatformAdapter 协议里的名字是 resolve；commands.py 调的就是 adapter.resolve()。
# 函数本身保留 resolve_author（测试和内部调用都在用这个名字）。
resolve = resolve_author


async def _author_id_from_video(settings: Settings, value: str) -> str:
    """从 BV/AV 链接或短链解析出作者 UID。"""

    video = extract_bilibili_video(value)
    if video is None and SHORT_LINK_RE.search(value):
        value = await _resolve_short_link(value)
        mid = extract_bilibili_mid(value)
        if mid:
            return mid
        video = extract_bilibili_video(value)
    if video is None:
        raise ValueError("请输入 B站作者主页、BV/AV 视频链接或 b23.tv 短链")
    kind, identifier = video
    try:
        info = await Video(
            bvid=identifier if kind == "bvid" else None,
            aid=int(identifier) if kind == "avid" else None,
            credential=_credential(settings),
        ).get_info()
    except Exception as exc:
        raise _wrap_error("B站作品详情请求失败", exc) from exc
    owner = info.get("owner")
    if not isinstance(owner, dict) or not owner.get("mid"):
        raise TransientError("B站作品详情缺少作者信息")
    return str(owner["mid"])


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
            headers=BILIBILI_BROWSER_HEADERS, follow_redirects=True, timeout=20
        ) as client:
            response = await client.get(short_url)
            response.raise_for_status()
            return str(response.url)
    except Exception as exc:
        raise _wrap_error("B站短链解析失败", exc) from exc


async def fetch_author_profile(settings: Settings, mid: str) -> AuthorInfo:
    """获取 B 站作者资料，优先使用匿名可用的 APP 接口。"""

    try:
        return await _fetch_author_profile_app(mid)
    except TransientError as exc:
        logger.warning(f"B 站 APP 作者资料接口失败，回退网页接口：{exc}")
    return await _fetch_author_profile_web(settings, mid)


async def _fetch_author_profile_app(mid: str) -> AuthorInfo:
    """通过 APP 接口获取 B 站作者资料。"""

    async with _app_client() as client:
        data = await _app_get(client, "/x/v2/space", {"vmid": mid})
    card = data.get("card")
    if not isinstance(card, dict) or not card.get("mid"):
        raise TransientError("B站 APP 作者资料返回格式异常")
    return AuthorInfo(
        author_id=str(card["mid"]),
        name=str(card.get("name") or card["mid"]),
        handle=str(card.get("mid") or ""),
        avatar=str(card.get("face") or ""),
        description=str(card.get("sign") or ""),
    )


async def _fetch_author_profile_web(settings: Settings, mid: str) -> AuthorInfo:
    """通过网页接口获取 B 站作者资料。"""

    try:
        info = await User(int(mid), credential=_credential(settings)).get_user_info()
    except Exception as exc:
        raise _wrap_error("B站作者资料请求失败", exc) from exc
    if not isinstance(info, dict) or not info.get("mid"):
        raise TransientError("B站作者资料返回格式异常")
    return AuthorInfo(
        author_id=str(info["mid"]),
        name=str(info.get("name") or info["mid"]),
        handle=str(info.get("mid") or ""),
        avatar=str(info.get("face") or ""),
        description=str(info.get("sign") or ""),
    )


def _is_joint_creation_item(item: Any) -> bool:
    """判断 APP 投稿条目是否联合创作；此时 ``author`` 是合作方而不是空间主人。"""

    if not isinstance(item, dict):
        return False
    if item.get("is_cooperation"):
        return True
    return str(item.get("author") or "").endswith(JOINT_CREATION_SUFFIX)


def _is_collaborator_item(item: Any, mid: str) -> bool:
    """判断网页投稿条目的 ``upper`` 是否只是合作方。

    网页接口没有 ``is_cooperation`` 标记，只能看 ``upper.mid`` 是否等于空间主人；
    联合创作时 ``upper`` 是合作方，署它的名字会把订阅名改错。
    """

    upper = item.get("upper") if isinstance(item, dict) else None
    if not isinstance(upper, dict):
        return False
    upper_mid = upper.get("mid")
    return bool(upper_mid) and str(upper_mid) != mid


async def _owner_identity(settings: Settings, mid: str) -> AuthorInfo | None:
    """取空间主人资料，给联合创作稿件署名；失败时返回 ``None`` 交给订阅名兜底。"""

    try:
        return await fetch_author_profile(settings, mid)
    except TransientError as exc:
        logger.debug(f"B 站作者资料获取失败，联合创作暂不署名：{exc}")
        return None


async def fetch(
    settings: Settings,
    author_id: str,
    feed: FeedKind,
    cursor: Cursor | None = None,
) -> list[PlatformWork]:
    """读取作者视频投稿或动态，返回从新到旧的列表。

    Args:
        settings: 运行期配置。
        author_id: 作者 UID。
        feed: ``video`` 或 ``dynamic``。
        cursor: 目标当前的游标，``None`` 表示首次订阅。

    Returns:
        从新到旧排列的作品列表。
    """

    if feed == "video":
        try:
            return await _fetch_video_app(settings, author_id, cursor)
        except TransientError as exc:
            logger.warning(f"B 站 APP 投稿接口失败，回退网页接口：{exc}")
        return await _fetch_video_web(settings, author_id, cursor)
    if feed == "dynamic":
        return await _fetch_dynamics(settings, author_id, cursor)
    return []


async def _fetch_video_app(
    settings: Settings, mid: str, cursor: Cursor | None
) -> list[PlatformWork]:
    """通过 APP 接口读取视频投稿。"""

    owner: AuthorInfo | None = None

    async with _app_client() as client:

        async def fetch_page(token: str | None) -> Page:
            nonlocal owner
            params = {"vmid": mid, "order": "pubdate"}
            if token:
                params["aid"] = token
            data = await _app_get(client, "/x/v2/space/archive/cursor", params)
            items = data.get("item") or []
            if not isinstance(items, list):
                raise TransientError("B站 APP 投稿列表缺少 item")
            works: list[PlatformWork] = []
            joint_ids: set[int] = set()
            for item in items:
                work = _parse_app_video_item(item, mid)
                if work is None:
                    continue
                if _is_joint_creation_item(item):
                    joint_ids.add(id(work))
                works.append(work)
            if joint_ids:
                if owner is None:
                    owner = await _owner_identity(settings, mid)
                if owner is not None:
                    works = [
                        replace(work, author=owner) if id(work) in joint_ids else work
                        for work in works
                    ]
            last_item = items[-1] if items and isinstance(items[-1], dict) else {}
            last_aid = last_item.get("param")
            return Page(
                works=works,
                next_token=str(last_aid) if last_aid else None,
                has_more=bool(data.get("has_next")),
            )

        return await paginate(fetch_page, cursor=cursor, limit=settings.max_items)


async def _fetch_video_web(
    settings: Settings, mid: str, cursor: Cursor | None
) -> list[PlatformWork]:
    """通过网页接口读取视频投稿。"""

    user = User(int(mid), credential=_credential(settings))
    author: AuthorInfo | None = None

    async def fetch_page(token: str | None) -> Page:
        nonlocal author
        try:
            result = await user.get_media_list(
                oid=int(token) if token else None,
                direction=False,
                desc=True,
                with_current=False,
            )
        except Exception as exc:
            raise _wrap_error("B站投稿列表请求失败", exc) from exc
        if not isinstance(result, dict):
            raise TransientError("B站投稿列表返回格式异常")
        items = result.get("media_list") or []
        if not isinstance(items, list):
            raise TransientError("B站投稿列表缺少 media_list")
        works: list[PlatformWork] = []
        for item in items:
            if author is None and _is_collaborator_item(item, mid):
                author = await _owner_identity(settings, mid)
            work = _parse_video_item(item, mid, author)
            if work is None:
                continue
            author = work.author
            works.append(work)
        last_item = items[-1] if items and isinstance(items[-1], dict) else {}
        last_aid = last_item.get("id")
        return Page(
            works=works,
            next_token=str(last_aid) if last_aid else None,
            has_more=bool(result.get("has_more")),
        )

    return await paginate(fetch_page, cursor=cursor, limit=settings.max_items)


async def _fetch_dynamics(
    settings: Settings, mid: str, cursor: Cursor | None
) -> list[PlatformWork]:
    """通过网页接口读取作者动态。"""

    user = User(int(mid), credential=_credential(settings))

    async def fetch_page(token: str | None) -> Page:
        try:
            result = await user.get_dynamics_new(offset=token or "")
        except Exception as exc:
            raise _wrap_error("B站动态列表请求失败", exc) from exc
        if not isinstance(result, dict):
            raise TransientError("B站动态列表返回格式异常")
        items = result.get("items") or []
        if not isinstance(items, list):
            raise TransientError("B站动态列表缺少 items")
        works = [work for item in items if (work := _parse_dynamic_item(item, mid))]
        next_offset = str(result.get("offset") or "")
        return Page(
            works=works,
            next_token=next_offset or None,
            has_more=bool(result.get("has_more")),
        )

    return await paginate(fetch_page, cursor=cursor, limit=settings.max_items)


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
            last_error = TransientError("B站 APP 接口返回格式异常")
            continue
        code = payload.get("code")
        if code != 0:
            if code in PERMANENT_APP_CODES:
                raise PermanentError(
                    f"B站 APP 接口返回 {code}：{payload.get('message') or ''}"
                )
            last_error = TransientError(
                f"B站 APP 接口返回 {code}：{payload.get('message') or ''}"
            )
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            last_error = TransientError("B站 APP 接口缺少 data")
            continue
        return data
    if isinstance(last_error, TransientError):
        raise last_error
    raise _wrap_error("B站 APP 接口请求失败", last_error or RuntimeError("未知错误"))


def _parse_app_video_item(item: Any, mid: str) -> PlatformWork | None:
    """把 APP 投稿条目转换为统一作品模型。"""

    if not isinstance(item, dict):
        return None
    bvid = str(item.get("bvid") or "")
    if not bvid:
        return None
    name = str(item.get("author") or "")
    if item.get("is_cooperation") or name.endswith(JOINT_CREATION_SUFFIX):
        # 联合创作时 author 是合作方（如「哔哩哔哩纪录片 等联合创作」），不是空间主人；
        # 留空由 _fetch_video_app 用作者资料补上，绝不能拿合作方名字当订阅名。
        name = ""
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


def _parse_video_item(
    item: Any, mid: str, author: AuthorInfo | None
) -> PlatformWork | None:
    """把网页投稿条目转换为统一作品模型。"""

    if not isinstance(item, dict):
        return None
    bvid = str(item.get("bv_id") or "")
    if not bvid:
        return None
    item_author = item.get("upper")
    if isinstance(item_author, dict) and not _is_collaborator_item(item, mid):
        author = AuthorInfo(
            author_id=mid,
            name=str(item_author.get("name") or mid),
            handle=mid,
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


def _parse_dynamic_item(item: Any, mid: str) -> PlatformWork | None:
    """把动态条目转换为统一作品模型。"""

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


def _credential(settings: Settings) -> Credential | None:
    """按配置构造 bilibili-api 凭证。"""

    raw_cookie = settings.bilibili_cookie
    if not raw_cookie:
        return None
    return Credential.from_cookies(_parse_cookie(raw_cookie))


def _parse_cookie(raw_cookie: str) -> dict[str, str]:
    """把 Cookie 请求头解析成键值字典。"""

    cookies: dict[str, str] = {}
    for item in raw_cookie.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name:
            cookies[name] = value
    return cookies


def _wrap_error(prefix: str, exc: Exception) -> TransientError:
    """把 bilibili-api 的异常包装成统一的可重试错误。"""

    return TransientError(f"{prefix}：{exc}")
