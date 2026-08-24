"""抖音作者主页和作品接口。"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx
from nonebot import logger

from .config import config

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
TTWID_REGISTER_URL = "https://ttwid.bytedance.com/ttwid/union/register/"

_ttwid: str | None = None
_ttwid_lock = asyncio.Lock()


@dataclass(frozen=True)
class DouyinWork:
    """抖音作品的最小信息。"""

    aweme_id: str
    share_url: str
    description: str
    create_time: int
    author_name: str
    author_unique_id: str = ""


class DouyinRequestError(RuntimeError):
    """抖音接口不可用或返回了无法识别的数据。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        """初始化抖音请求异常。

        Args:
            message: 面向日志或用户的错误信息。
            retryable: 是否可能由临时风控或网络问题导致。
        """

        super().__init__(message)
        self.retryable = retryable


def extract_sec_user_id(value: str) -> str | None:
    """从作者主页 URL 或 sec_user_id 中提取作者 ID。"""

    value = value.strip().strip("<>[]()")
    if matched := PROFILE_RE.search(value):
        return matched.group("sec_user_id")
    if SEC_USER_ID_RE.fullmatch(value):
        return value
    return None


async def resolve_sec_user_id(value: str) -> str:
    """解析作者主页或作品链接，支持抖音短链。"""

    if sec_user_id := extract_sec_user_id(value):
        return sec_user_id

    matched = SHORT_LINK_RE.search(value) or WORK_RE.search(value)
    if not matched:
        raise ValueError("请输入抖音作者主页链接、作品分享链接，或直接输入 sec_user_id")

    async with httpx.AsyncClient(
        headers=_headers(),
        follow_redirects=True,
        timeout=20,
        verify=False,
    ) as client:
        response = await client.get(
            matched.group(0), cookies=_parse_cookie(config.douyin_cookie)
        )
        response.raise_for_status()
        final_url = str(response.url)
        if sec_user_id := extract_sec_user_id(final_url):
            return sec_user_id

        if work_match := WORK_RE.search(final_url):
            if sec_user_id := await _fetch_work_author(
                client, work_match.group("aweme_id")
            ):
                return sec_user_id
            page = await client.get(final_url, cookies=await _request_cookies())
            page.raise_for_status()
            if sec_user_id := _extract_sec_user_id_from_page(page.text):
                return sec_user_id
            raise DouyinRequestError("作品页未找到可识别的作者主页")
    raise DouyinRequestError("链接未跳转到可识别的抖音作者主页或作品页")


async def _fetch_work_author(client: httpx.AsyncClient, aweme_id: str) -> str | None:
    """从作品详情接口提取作者的 sec_user_id。"""

    response = await client.get(
        WORK_DETAIL_URL,
        params={"aweme_id": aweme_id, "aid": "6383"},
        cookies=await _request_cookies(),
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
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


async def fetch_latest_works(sec_user_id: str) -> list[DouyinWork]:
    """读取作者最新作品，返回从新到旧的列表。"""

    params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "sec_user_id": sec_user_id,
        "max_cursor": "0",
        "locate_query": "false",
        "count": str(config.douyin_subscribe_count),
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
    async with httpx.AsyncClient(
        headers=_headers(),
        follow_redirects=True,
        timeout=30,
        verify=False,
    ) as client:
        response = await client.get(
            "https://www.douyin.com/aweme/v1/web/aweme/post/",
            params=params,
            cookies=await _request_cookies(),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = response.status_code
            raise DouyinRequestError(
                "抖音作品接口请求失败："
                f"HTTP {status_code}，响应内容：{_response_excerpt(response.text)}",
                retryable=status_code in {403, 429, 444, 500, 502, 503, 504},
            ) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise DouyinRequestError(
                "抖音作品接口返回的不是 JSON："
                f"HTTP {response.status_code}，响应内容："
                f"{_response_excerpt(response.text)}",
                retryable=True,
            ) from exc

    if not isinstance(data, dict):
        raise DouyinRequestError("抖音接口返回格式异常")
    status_code = data.get("status_code", 0)
    if status_code not in (0, "0", None):
        raise DouyinRequestError(f"抖音接口返回错误码: {status_code}")

    raw_works = data.get("aweme_list") or data.get("aweme_list_v2") or []
    if not isinstance(raw_works, list):
        raise DouyinRequestError("抖音接口缺少 aweme_list")

    works: list[DouyinWork] = []
    for raw in raw_works:
        if not isinstance(raw, dict):
            continue
        aweme_id = str(raw.get("aweme_id") or "")
        if not aweme_id:
            continue
        author = raw.get("author") or {}
        if not isinstance(author, dict):
            author = {}
        author_name = str(author.get("nickname") or sec_user_id)
        author_unique_id = _extract_unique_id(author)
        works.append(
            DouyinWork(
                aweme_id=aweme_id,
                share_url=f"https://www.douyin.com/video/{aweme_id}",
                description=str(raw.get("desc") or ""),
                create_time=int(raw.get("create_time") or 0),
                author_name=author_name,
                author_unique_id=author_unique_id,
            )
        )
    works.sort(key=lambda work: (work.create_time, work.aweme_id), reverse=True)
    logger.debug(f"抖音作者 {sec_user_id} 拉取到 {len(works)} 个作品")
    return works


def _response_excerpt(text: str, limit: int = 160) -> str:
    """压缩并截取接口响应，避免错误日志过长。"""

    excerpt = " ".join(text.split())
    return repr(excerpt[:limit] if excerpt else "<empty>")


async def fetch_author_profile(sec_user_id: str) -> tuple[str, str]:
    """读取作者昵称和抖音号。"""

    async with httpx.AsyncClient(
        headers=_headers(),
        follow_redirects=True,
        timeout=20,
        verify=False,
    ) as client:
        response = await client.get(
            AUTHOR_PROFILE_URL,
            params={"sec_user_id": sec_user_id, "aid": "6383"},
            cookies=await _request_cookies(),
        )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise DouyinRequestError("抖音作者资料接口返回的不是 JSON") from exc

    if not isinstance(data, dict):
        raise DouyinRequestError("抖音作者资料接口返回格式异常")
    status_code = data.get("status_code", 0)
    if status_code not in (0, "0", None):
        raise DouyinRequestError(f"抖音作者资料接口返回错误码: {status_code}")
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


async def _ensure_ttwid() -> str:
    """参考 parser_lite 注册并缓存抖音的 ttwid Cookie。"""

    global _ttwid

    if _ttwid:
        return _ttwid
    async with _ttwid_lock:
        if _ttwid:
            return _ttwid
        async with httpx.AsyncClient(
            headers=_headers(),
            follow_redirects=True,
            timeout=20,
            verify=False,
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
                raise DouyinRequestError(
                    "抖音 ttwid 注册失败："
                    f"HTTP {response.status_code}，响应内容："
                    f"{_response_excerpt(response.text)}",
                    retryable=True,
                ) from exc
            ttwid = response.cookies.get("ttwid")
            if not ttwid:
                raise DouyinRequestError(
                    "抖音 ttwid 注册成功但未返回 Cookie",
                    retryable=True,
                )
        _ttwid = ttwid
        logger.debug("抖音 ttwid 注册成功")
        return ttwid


async def _request_cookies() -> dict[str, str]:
    """组合配置 Cookie 与 parser_lite 风格的 ttwid Cookie。"""

    cookies = _parse_cookie(config.douyin_cookie)
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
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.douyin.com/",
        "Origin": "https://open.douyin.com",
        "Accept": "application/json, text/plain, */*",
    }
    return headers
