"""抖音作者主页与作品接口适配。"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from nonebot import logger

from .douyin_abogus import generate_abogus
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
    r"(?:https?://)?(?:(?:www\.|m\.)?douyin\.com/user/"
    r"|(?:www\.)?iesdouyin\.com/share/user/)"
    r"(?P<sec_user_id>[^/?#\s\"'<>]+)"
)
SEC_USER_ID_RE = re.compile(r"^MS4w[^\s/?#]+$")
SHORT_LINK_RE = re.compile(r"https?://(?:v|jx)\.douyin\.com/[\w-]+")
WORK_RE = re.compile(r"(?:https?://)?(?:www\.|m\.)?douyin\.com/video/(?P<aweme_id>\d+)")
WORK_DETAIL_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
AUTHOR_PROFILE_URL = "https://www.douyin.com/aweme/v1/web/user/profile/other/"
AUTHOR_POST_URL = "https://www.douyin.com/aweme/v1/web/aweme/post/"
TTWID_REGISTER_URL = "https://ttwid.bytedance.com/ttwid/union/register/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
"""请求与 a_bogus 签名共用的 User-Agent，两者必须一致。"""

BLOCKED_STATUS_CODES = frozenset({403, 429, 444, 500, 502, 503, 504})
"""抖音风控或网关异常时返回的状态码，通常可重试。"""

INVALID_AUTHOR_STATUS = 5
"""作品列表接口表示作者不存在或已注销的 status_code。"""

platform = "douyin"

_ttwid: str | None = None
_ttwid_lock = asyncio.Lock()


def matches(value: str) -> bool:
    """判断输入是否属于抖音。"""

    return bool(
        extract_sec_user_id(value)
        or SHORT_LINK_RE.search(value)
        or WORK_RE.search(value)
    )


def feeds(settings: Settings) -> tuple[FeedKind, ...]:
    """抖音只有作品 feed。"""

    del settings
    return ("video",)


def extract_sec_user_id(value: str) -> str | None:
    """从作者主页 URL 或 sec_user_id 中提取作者 ID。"""

    value = value.strip().strip("<>[]()")
    if matched := PROFILE_RE.search(value):
        return matched.group("sec_user_id")
    if SEC_USER_ID_RE.fullmatch(value):
        return value
    return None


async def resolve_author(settings: Settings, value: str) -> ResolvedAuthor:
    """解析作者链接并返回首页作品，用于初始化游标。"""

    sec_user_id = await resolve_sec_user_id(settings, value)
    works = await fetch(settings, sec_user_id, "video", None)
    if works:
        return ResolvedAuthor(works[0].author, {"video": works})
    name, handle = await fetch_author_profile(settings, sec_user_id)
    author = AuthorInfo(sec_user_id, name or sec_user_id, handle)
    return ResolvedAuthor(author, {"video": works})


async def resolve_sec_user_id(settings: Settings, value: str) -> str:
    """解析作者主页或作品链接，支持抖音短链。"""

    if sec_user_id := extract_sec_user_id(value):
        return sec_user_id

    matched = SHORT_LINK_RE.search(value) or WORK_RE.search(value)
    if not matched:
        raise ValueError("请输入抖音作者主页链接、作品分享链接，或直接输入 sec_user_id")

    async with _client(settings) as client:
        response = await client.get(matched.group(0))
        response.raise_for_status()
        final_url = str(response.url)
        if sec_user_id := extract_sec_user_id(final_url):
            return sec_user_id

        if work_match := WORK_RE.search(final_url):
            if sec_user_id := await _fetch_work_author(
                settings, client, work_match["aweme_id"]
            ):
                return sec_user_id
            page = await client.get(final_url)
            page.raise_for_status()
            if sec_user_id := _extract_sec_user_id_from_page(page.text):
                return sec_user_id
            raise TransientError("作品页未找到可识别的作者主页")
    raise TransientError("链接未跳转到可识别的抖音作者主页或作品页")


async def _fetch_work_author(
    settings: Settings, client: httpx.AsyncClient, aweme_id: str
) -> str | None:
    """从作品详情接口提取作者的 sec_user_id。"""

    try:
        data = await _request_json(
            settings,
            client,
            WORK_DETAIL_URL,
            {"device_platform": "webapp", "aid": "6383", "aweme_id": aweme_id},
        )
    except TransientError as exc:
        logger.debug(f"抖音作品详情接口不可用，改用作品页解析：{exc}")
        return None
    detail = data.get("aweme_detail")
    if not isinstance(detail, dict):
        return None
    author = detail.get("author")
    if not isinstance(author, dict):
        return None
    sec_user_id = author.get("sec_uid") or author.get("sec_user_id")
    return str(sec_user_id) if sec_user_id else None


def _extract_sec_user_id_from_page(page_text: str) -> str | None:
    """从抖音作品页 HTML 中提取作者主页的 sec_user_id。"""

    normalized = page_text.replace(r"\/", "/")
    matched = PROFILE_RE.search(normalized)
    return matched.group("sec_user_id") if matched else None


async def fetch(
    settings: Settings,
    author_id: str,
    feed: FeedKind,
    cursor: Cursor | None = None,
) -> list[PlatformWork]:
    """读取作者作品，返回从新到旧的列表。

    Args:
        settings: 运行期配置。
        author_id: 作者 sec_user_id。
        feed: 抖音只有 ``video``。
        cursor: 目标当前的游标，``None`` 表示首次订阅。

    Returns:
        从新到旧排列的作品列表。

    Raises:
        TransientError: 接口被风控、网络异常或数据格式不符。
        PermanentError: 作者不存在或已注销。
    """

    if feed != "video":
        return []
    async with _client(settings, timeout=30) as client:

        async def fetch_page(token: str | None) -> Page:
            data = await _fetch_post_page(settings, client, author_id, token or "0")
            raw_works = data.get("aweme_list") or data.get("aweme_list_v2") or []
            if not isinstance(raw_works, list):
                raise TransientError("抖音接口缺少 aweme_list")
            works = [
                work
                for raw in raw_works
                if (work := _parse_work_item(raw, author_id)) is not None
            ]
            next_cursor = str(data.get("max_cursor") or "")
            has_more = bool(
                data.get("has_more") and next_cursor and next_cursor != (token or "0")
            )
            return Page(
                works=works,
                next_token=next_cursor or None,
                has_more=has_more,
            )

        works = await paginate(fetch_page, cursor=cursor, limit=settings.max_items)
    logger.debug(f"抖音作者 {author_id} 拉取到 {len(works)} 个作品")
    return works


async def _fetch_post_page(
    settings: Settings,
    client: httpx.AsyncClient,
    sec_user_id: str,
    max_cursor: str,
) -> dict[str, Any]:
    """请求一页作者作品，并校验返回码。"""

    params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "sec_user_id": sec_user_id,
        "max_cursor": max_cursor,
        "locate_query": "false",
        "publish_video_strategy_type": "2",
        "pc_client_type": "1",
        "version_code": "170400",
        "version_name": "17.4.0",
        "cookie_enabled": "true",
        "screen_width": "1920",
        "screen_height": "1080",
        "browser_language": "zh-CN",
        "browser_platform": "MacIntel",
        "browser_name": "Chrome",
        "browser_version": "131.0.0.0",
    }
    data = await _request_json(settings, client, AUTHOR_POST_URL, params)
    status_code = data.get("status_code", 0)
    if status_code not in (0, "0", None):
        if str(status_code) == str(INVALID_AUTHOR_STATUS):
            raise PermanentError(f"抖音作者不存在或已注销：{sec_user_id}")
        raise TransientError(f"抖音接口返回错误码: {status_code}")
    return data


def _parse_work_item(raw: Any, sec_user_id: str) -> PlatformWork | None:
    """把一条作品数据转换成统一作品模型。"""

    if not isinstance(raw, dict):
        return None
    aweme_id = str(raw.get("aweme_id") or "")
    if not aweme_id:
        return None
    author = raw.get("author") or {}
    if not isinstance(author, dict):
        author = {}
    return PlatformWork(
        platform="douyin",
        work_id=aweme_id,
        url=f"https://www.douyin.com/video/{aweme_id}",
        title=str(raw.get("desc") or ""),
        description=str(raw.get("desc") or ""),
        create_time=int(raw.get("create_time") or 0),
        author=AuthorInfo(
            author_id=sec_user_id,
            name=str(author.get("nickname") or sec_user_id),
            handle=_extract_unique_id(author),
        ),
    )


def _response_excerpt(text: str, limit: int = 160) -> str:
    """压缩并截取接口响应，避免错误日志过长。"""

    excerpt = " ".join(text.split())
    return repr(excerpt[:limit] if excerpt else "<empty>")


async def fetch_author_profile(settings: Settings, sec_user_id: str) -> tuple[str, str]:
    """读取作者昵称和抖音号。"""

    async with _client(settings) as client:
        data = await _request_json(
            settings,
            client,
            AUTHOR_PROFILE_URL,
            {"device_platform": "webapp", "aid": "6383", "sec_user_id": sec_user_id},
        )

    status_code = data.get("status_code", 0)
    if status_code not in (0, "0", None):
        raise TransientError(f"抖音作者资料接口返回错误码: {status_code}")
    user = data.get("user")
    if not isinstance(user, dict):
        return "", ""
    return str(user.get("nickname") or ""), _extract_unique_id(user)


def _extract_unique_id(author: dict[str, object]) -> str:
    """从作者对象提取抖音号，忽略接口返回的占位 short_id。"""

    unique_id = str(author.get("unique_id") or "")
    if unique_id:
        return unique_id
    short_id = str(author.get("short_id") or "")
    return short_id if short_id and short_id != "0" else ""


async def _request_json(
    settings: Settings,
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str],
) -> dict[str, Any]:
    """带 a_bogus 签名请求抖音 web 接口，并返回解析后的 JSON。

    抖音网关会以 403 或空响应拦截签名、ttwid 失效的请求。遇到这类响应时丢弃缓存
    的 ttwid，重新注册后重试一次；仍失败则抛出可重试异常。

    Args:
        settings: 运行期配置。
        client: 已配置好请求头的 HTTP 客户端。
        url: 接口地址，不含查询串。
        params: 接口查询参数。

    Returns:
        解析后的 JSON 对象。

    Raises:
        TransientError: 接口被拦截、请求失败或返回内容不是 JSON 对象。
        PermanentError: 接口返回了不可重试的状态码。
    """

    last_error: TransientError | None = None
    for _ in range(2):
        client.cookies.update(await _request_cookies(settings))
        response = await client.get(f"{url}?{_signed_query(params)}")
        blocked = (
            response.status_code in BLOCKED_STATUS_CODES or not response.content.strip()
        )
        if blocked:
            _invalidate_ttwid()
            last_error = TransientError(
                "抖音接口被风控拦截："
                f"HTTP {response.status_code}，响应内容："
                f"{_response_excerpt(response.text)}"
            )
            logger.debug(f"抖音接口响应异常，已重置 ttwid：{last_error}")
            continue
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = (
                "抖音接口请求失败："
                f"HTTP {response.status_code}，响应内容："
                f"{_response_excerpt(response.text)}"
            )
            if response.status_code in BLOCKED_STATUS_CODES:
                raise TransientError(message) from exc
            raise PermanentError(message) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise TransientError(
                "抖音接口返回的不是 JSON："
                f"HTTP {response.status_code}，响应内容："
                f"{_response_excerpt(response.text)}"
            ) from exc
        if not isinstance(data, dict):
            raise TransientError("抖音接口返回格式异常")
        return data

    if last_error is None:  # pragma: no cover - 循环至少执行一次
        raise TransientError("抖音接口请求失败")
    raise last_error


def _signed_query(params: dict[str, str]) -> str:
    """把查询参数拼成带 a_bogus 签名的查询串。"""

    query = urlencode(params)
    signature = generate_abogus(query, user_agent=USER_AGENT)
    return f"{query}&a_bogus={quote(signature, safe='')}"


async def _ensure_ttwid() -> str:
    """注册并缓存抖音的 ttwid Cookie。"""

    global _ttwid

    if _ttwid:
        return _ttwid
    async with _ttwid_lock:
        if _ttwid:
            return _ttwid
        async with httpx.AsyncClient(
            headers=_headers(), follow_redirects=True, timeout=20
        ) as client:
            response = await client.post(
                TTWID_REGISTER_URL,
                json={
                    "region": "cn",
                    "aid": 1768,
                    "needFid": False,
                    "service": "www.douyin.com",
                    "migrate_info": {"ticket": "", "source": "node"},
                    "cbUrlProtocol": "https",
                    "union": True,
                },
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise TransientError(
                    "抖音 ttwid 注册失败："
                    f"HTTP {response.status_code}，响应内容："
                    f"{_response_excerpt(response.text)}"
                ) from exc
            ttwid = response.cookies.get("ttwid")
            if not ttwid:
                raise TransientError("抖音 ttwid 注册成功但未返回 Cookie")
        _ttwid = ttwid
        logger.debug("抖音 ttwid 注册成功")
        return ttwid


def _invalidate_ttwid() -> None:
    """丢弃缓存的 ttwid，让下一次请求重新注册。"""

    global _ttwid

    _ttwid = None


async def _request_cookies(settings: Settings) -> dict[str, str]:
    """组合配置 Cookie 与自动注册的 ttwid Cookie。

    配置 Cookie 中的 ttwid 会被自动注册的 ttwid 覆盖，以保证签名失效时能自愈。
    """

    cookies = _parse_cookie(settings.douyin_cookie)
    cookies["ttwid"] = await _ensure_ttwid()
    return cookies


def _parse_cookie(raw_cookie: str | None) -> dict[str, str]:
    """把浏览器复制的 Cookie 请求头转换为键值字典。"""

    if not raw_cookie:
        return {}
    cookies: dict[str, str] = {}
    for item in raw_cookie.split(";"):
        name, separator, value = item.strip().partition("=")
        if separator and name:
            cookies[name] = value
    return cookies


def _headers() -> dict[str, str]:
    """返回抖音接口需要的请求头。"""

    return {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.douyin.com/",
        "Origin": "https://open.douyin.com",
        "Accept": "application/json, text/plain, */*",
        "x-tt-argus": "1",
    }


def _client(settings: Settings, timeout: float = 20) -> httpx.AsyncClient:
    """创建抖音请求客户端；配置 Cookie 在客户端级维护。"""

    return httpx.AsyncClient(
        headers=_headers(),
        follow_redirects=True,
        timeout=timeout,
        cookies=_parse_cookie(settings.douyin_cookie),
    )
