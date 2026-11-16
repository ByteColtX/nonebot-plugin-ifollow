from typing import Any

import httpx
import pytest

from http_stub import Stub, install
from nonebot_plugin_ifollow import douyin_platform as douyin
from nonebot_plugin_ifollow.errors import PermanentError, TransientError
from nonebot_plugin_ifollow.platforms import Cursor
from nonebot_plugin_ifollow.settings import Settings

_REAL_ENSURE_TTWID = douyin._ensure_ttwid
"""fixture 会替换掉模块里的 _ensure_ttwid，这里保留原始实现供注册用例使用。"""

SETTINGS = Settings(
    interval=1800,
    max_items=5,
    media_max_bytes=100 * 1024 * 1024,
    use_base64=False,
)


def test_extract_sec_user_id() -> None:
    assert (
        douyin.extract_sec_user_id(
            "https://www.douyin.com/user/MS4wLjABAAAA-test?from_tab=main"
        )
        == "MS4wLjABAAAA-test"
    )
    assert douyin.extract_sec_user_id("MS4wLjABAAAA-test") == "MS4wLjABAAAA-test"
    assert douyin.extract_sec_user_id("https://www.douyin.com/video/123") is None


def test_extract_sec_user_id_from_mobile_share_link() -> None:
    """App 分享出来的是 m.douyin.com/share/user/<sec_uid>，要能认。"""

    url = "https://m.douyin.com/share/user/MS4wLjABAAAA-mobile"
    assert douyin.extract_sec_user_id(url) == "MS4wLjABAAAA-mobile"
    assert douyin.matches(url)


def test_matches_note_and_slides_links() -> None:
    """图文（note）与图集（slides）链接也要能被识别为抖音作品。"""

    urls = [
        "https://www.iesdouyin.com/share/note/7412345678901234567?schema_type=37",
        "https://www.iesdouyin.com/share/slides/7412345678901234567",
        "https://www.douyin.com/note/7412345678901234567",
        "https://www.douyin.com/share/note/7412345678901234567",
        "https://m.douyin.com/share/slides/7412345678901234567",
    ]
    for url in urls:
        assert douyin.matches(url), url
        match = douyin.WORK_RE.search(url)
        assert match is not None, url
        assert match["aweme_id"] == "7412345678901234567"


def response(
    status_code: int = 200, content: bytes = b"{}", data: Any = None
) -> httpx.Response:
    """构造一个真实响应：``data`` 走 JSON，否则原样返回字节。"""

    if data is not None:
        return httpx.Response(status_code, json=data)
    return httpx.Response(status_code, content=content)


def _works_payload(count: int = 1) -> dict[str, Any]:
    return {
        "status_code": 0,
        "aweme_list": [
            {
                "aweme_id": str(100 + index),
                "create_time": 20 + index,
                "desc": "作品",
                "author": {"nickname": "作者", "unique_id": "handle"},
            }
            for index in range(count)
        ],
    }


def empty_page() -> httpx.Response:
    return response(data={"status_code": 0, "aweme_list": []})


def responses_by_aid(
    video: list[httpx.Response],
    notes: list[httpx.Response] | None = None,
    extra: list[httpx.Response] | None = None,
) -> Stub:
    """按 ``aid`` 回放两条流的响应；非作品列表请求走 ``extra``。"""

    queues = {
        "1128": video,
        "6383": notes or [empty_page()],
        "extra": extra or [],
    }

    def route(request: httpx.Request) -> httpx.Response:
        key = (
            request.url.params["aid"]
            if request.url.path.endswith("/aweme/v1/web/aweme/post/")
            else "extra"
        )
        queue = queues[key]
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return Stub(route)


async def fetch_page(aid: str = "1128") -> dict[str, Any]:
    """直接请求一页，绕开双流合并，便于验证重试行为。"""

    async with douyin._client(SETTINGS) as http:
        return await douyin._fetch_post_page(
            SETTINGS, http, "MS4wLjABAAAA-test", "0", aid
        )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """安装 MockTransport 客户端，并让 ttwid 每次注册都返回新值。"""

    counter = iter(range(100))
    ttwids: list[str] = []

    def factory(responses: Any) -> Stub:
        return install(monkeypatch, Stub(responses))

    async def ensure_ttwid() -> str:
        # 和真实实现一样缓存 ttwid：失效后才重新注册。
        if douyin._ttwid:
            return douyin._ttwid
        ttwid = f"ttwid-{next(counter)}"
        ttwids.append(ttwid)
        douyin._ttwid = ttwid
        return ttwid

    monkeypatch.setattr(douyin, "_ensure_ttwid", ensure_ttwid)
    monkeypatch.setattr(douyin, "_ttwid", None)
    factory.ttwids = ttwids  # type: ignore[attr-defined]
    return factory


def test_signed_query_appends_encoded_a_bogus() -> None:
    query = douyin._signed_query({"aid": "6383", "sec_user_id": "MS4wLjABAAAA-test"})

    assert query.startswith("aid=6383&sec_user_id=MS4wLjABAAAA-test&a_bogus=")
    signature = query.split("&a_bogus=", 1)[1]
    assert signature
    assert "+" not in signature
    assert "/" not in signature


@pytest.mark.asyncio
async def test_fetch_latest_works_maps_to_common_model(client: Any) -> None:
    client(responses_by_aid([response(data=_works_payload())]))

    works = await douyin.fetch(SETTINGS, "MS4wLjABAAAA-test", "video")

    assert works[0].platform == "douyin"
    assert works[0].work_id == "100"
    assert works[0].author.name == "作者"
    assert works[0].author.handle == "handle"


@pytest.mark.asyncio
async def test_fetch_latest_works_sorts_newest_first(client: Any) -> None:
    client(responses_by_aid([response(data=_works_payload(count=3))]))

    works = await douyin.fetch(SETTINGS, "MS4wLjABAAAA-test", "video")

    assert [work.work_id for work in works] == ["102", "101", "100"]


@pytest.mark.asyncio
async def test_fetch_latest_works_reports_missing_author(client: Any) -> None:
    missing = response(data={"status_code": douyin.INVALID_AUTHOR_STATUS})
    client(responses_by_aid([missing], [missing]))

    with pytest.raises(PermanentError) as excinfo:
        await douyin.fetch(SETTINGS, "MS4wLjABAAAA-test", "video")

    assert "不存在" in str(excinfo.value)


@pytest.mark.asyncio
async def test_fetch_latest_works_retries_with_new_ttwid_on_empty_body(
    client: Any,
) -> None:
    client([response(content=b""), response(data=_works_payload())])

    data = await fetch_page()

    assert data["aweme_list"][0]["aweme_id"] == "100"
    assert client.ttwids == ["ttwid-0", "ttwid-1"]


@pytest.mark.asyncio
async def test_fetch_latest_works_retries_on_blocked_status(client: Any) -> None:
    client(
        [
            response(status_code=403, content=b"Blocked by ArgusSecurityPlugin"),
            response(data=_works_payload()),
        ]
    )

    data = await fetch_page()

    assert data["aweme_list"]
    assert len(client.ttwids) == 2


@pytest.mark.asyncio
async def test_fetch_latest_works_gives_up_after_second_block(client: Any) -> None:
    client([response(content=b""), response(content=b"")])

    with pytest.raises(TransientError) as excinfo:
        await fetch_page()

    assert isinstance(excinfo.value, TransientError)
    assert "风控" in str(excinfo.value)


@pytest.mark.asyncio
async def test_fetch_latest_works_rejects_non_json_body(client: Any) -> None:
    client([response(content=b"<html></html>")])

    with pytest.raises(TransientError) as excinfo:
        await fetch_page()

    assert isinstance(excinfo.value, TransientError)


@pytest.mark.asyncio
async def test_fetch_work_author_returns_none_when_detail_blocked() -> None:
    stub = Stub([response(content=b""), response(content=b"")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(stub)) as fake:
        author = await douyin._fetch_work_author(SETTINGS, fake, "100")

    assert author is None
    assert len(stub.requests) == 2


@pytest.mark.asyncio
async def test_fetch_author_profile_reads_nickname_and_handle(client: Any) -> None:
    client(
        [
            response(
                data={
                    "status_code": 0,
                    "user": {"nickname": "作者", "unique_id": "deagle"},
                }
            )
        ]
    )

    nickname, handle = await douyin.fetch_author_profile(SETTINGS, "MS4wLjABAAAA-test")

    assert (nickname, handle) == ("作者", "deagle")


@pytest.mark.asyncio
async def test_fetch_latest_works_pages_until_cursor(client: Any) -> None:
    """停机久了要按页补齐，直到翻到游标作品。"""

    created = client(
        responses_by_aid(
            [
                response(
                    data={
                        "status_code": 0,
                        "has_more": True,
                        "max_cursor": "50",
                        "aweme_list": [
                            {
                                "aweme_id": "103",
                                "create_time": 30,
                                "author": {"nickname": "作者"},
                            }
                        ],
                    }
                ),
                response(
                    data={
                        "status_code": 0,
                        "has_more": False,
                        "aweme_list": [
                            {
                                "aweme_id": "100",
                                "create_time": 10,
                                "author": {"nickname": "作者"},
                            }
                        ],
                    }
                ),
            ]
        )
    )

    works = await douyin.fetch(
        SETTINGS, "MS4wLjABAAAA-test", "video", Cursor("100", 10)
    )

    assert [work.work_id for work in works] == ["103", "100"]
    video_requests = [
        request
        for request in created.requests
        if request.url.params["aid"] == douyin.AID_MOBILE
    ]
    assert len(video_requests) == 2


@pytest.mark.asyncio
async def test_fetch_latest_works_without_cursor_requests_one_page(
    client: Any,
) -> None:
    created = client(
        responses_by_aid(
            [
                response(
                    data={
                        "status_code": 0,
                        "has_more": True,
                        "max_cursor": "50",
                        "aweme_list": [
                            {
                                "aweme_id": "103",
                                "create_time": 30,
                                "author": {"nickname": "作者"},
                            }
                        ],
                    }
                )
            ]
        )
    )

    works = await douyin.fetch(SETTINGS, "MS4wLjABAAAA-test", "video")

    assert [work.work_id for work in works] == ["103"]
    assert {request.url.params["aid"] for request in created.requests} == {
        douyin.AID_MOBILE,
        douyin.AID_WEB,
    }
    assert len(created.requests) == 2


@pytest.mark.asyncio
async def test_fetch_returns_image_posts(client: Any) -> None:
    """1128 没有图文，图文的 note 链接来自 6383。"""

    client(
        responses_by_aid(
            [empty_page()],
            [
                response(
                    data={
                        "status_code": 0,
                        "has_more": False,
                        "aweme_list": [
                            {
                                "aweme_id": "200",
                                "aweme_type": 68,
                                "create_time": 40,
                                "desc": "图文作品",
                                "author": {"nickname": "作者"},
                            }
                        ],
                    }
                )
            ],
        )
    )

    works = await douyin.fetch(SETTINGS, "MS4wLjABAAAA-test", "video")

    assert [work.work_id for work in works] == ["200"]
    assert works[0].url == "https://www.douyin.com/note/200"


@pytest.mark.asyncio
async def test_fetch_merges_mobile_videos_with_web_notes(client: Any) -> None:
    """1128 的视频与 6383 的图文合并；6383 里滞后的视频不再重复。"""

    video = {"aweme_id": "300", "create_time": 50, "author": {"nickname": "作者"}}
    stale_video = {"aweme_id": "100", "create_time": 10, "author": {"nickname": "作者"}}
    note = {
        "aweme_id": "200",
        "aweme_type": 68,
        "create_time": 40,
        "author": {"nickname": "作者"},
    }
    client(
        responses_by_aid(
            [response(data={"status_code": 0, "aweme_list": [video]})],
            [
                response(
                    data={
                        "status_code": 0,
                        "aweme_list": [note, stale_video],
                    }
                )
            ],
        )
    )

    works = await douyin.fetch(SETTINGS, "MS4wLjABAAAA-test", "video")

    assert [work.work_id for work in works] == ["300", "200"]
    assert works[1].url == "https://www.douyin.com/note/200"


@pytest.mark.asyncio
async def test_fetch_does_not_starve_notes_behind_videos(client: Any) -> None:
    """6383 同一页里前面的视频很多时，图文仍不能被补发上限挤掉。"""

    videos = [
        {"aweme_id": str(300 + index), "create_time": 50 + index}
        for index in range(SETTINGS.max_items)
    ]
    note = {"aweme_id": "200", "aweme_type": 68, "create_time": 40}
    client(
        responses_by_aid(
            [empty_page()],
            [response(data={"status_code": 0, "aweme_list": [*videos, note]})],
        )
    )

    works = await douyin.fetch(SETTINGS, "MS4wLjABAAAA-test", "video")

    assert [work.work_id for work in works] == ["200"]


@pytest.mark.asyncio
async def test_fetch_latest_works_requests_full_page_and_stops_at_limit(
    client: Any,
) -> None:
    """每页按固定条数拉取，攒够补发上限后不再翻页。"""

    created = client(
        responses_by_aid(
            [
                response(
                    data={
                        "status_code": 0,
                        "has_more": True,
                        "max_cursor": "50",
                        **_works_payload(count=5),
                    }
                )
            ]
        )
    )

    works = await douyin.fetch(SETTINGS, "MS4wLjABAAAA-test", "video", Cursor("old", 1))

    assert len(works) == 5
    assert len(created.requests) == 2
    assert all(
        request.url.params["count"] == str(douyin.POST_PAGE_SIZE)
        for request in created.requests
    )


@pytest.mark.asyncio
async def test_resolve_sec_user_id_follows_short_link(client: Any) -> None:
    client(
        [
            httpx.Response(
                302,
                headers={"location": "https://www.douyin.com/user/MS4wLjABAAAA-x"},
            ),
            response(content=b""),
        ]
    )

    assert (
        await douyin.resolve_sec_user_id(SETTINGS, "https://v.douyin.com/abc/")
        == "MS4wLjABAAAA-x"
    )


@pytest.mark.asyncio
async def test_resolve_sec_user_id_reads_author_from_detail(client: Any) -> None:
    client(
        [
            response(content=b""),
            response(data={"aweme_detail": {"author": {"sec_uid": "MS4wLjABAAAA-x"}}}),
        ]
    )

    assert (
        await douyin.resolve_sec_user_id(SETTINGS, "https://www.douyin.com/video/123")
        == "MS4wLjABAAAA-x"
    )


@pytest.mark.asyncio
async def test_resolve_sec_user_id_follows_note_short_link(client: Any) -> None:
    """App 分享的图文短链会展开成 iesdouyin.com/share/note/<id>，不能再报网络错误。"""

    note_url = "https://www.iesdouyin.com/share/note/7412345678901234567?schema_type=37"

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == "v.douyin.com":
            return httpx.Response(302, headers={"location": note_url})
        if request.url.path == "/aweme/v1/web/aweme/detail/":
            return response(
                data={"aweme_detail": {"author": {"sec_uid": "MS4wLjABAAAA-note"}}}
            )
        return response(content=b"")

    created = client(Stub(route))

    assert (
        await douyin.resolve_sec_user_id(SETTINGS, "https://v.douyin.com/abc/")
        == "MS4wLjABAAAA-note"
    )
    assert created.requests[-1].url.params["aweme_id"] == "7412345678901234567"


@pytest.mark.asyncio
async def test_resolve_sec_user_id_accepts_direct_note_link(client: Any) -> None:
    """直接粘贴 douyin.com/note/<id> 也要能解析出作者。"""

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/aweme/v1/web/aweme/detail/":
            return response(
                data={"aweme_detail": {"author": {"sec_uid": "MS4wLjABAAAA-note"}}}
            )
        return response(content=b"")

    client(Stub(route))

    assert (
        await douyin.resolve_sec_user_id(
            SETTINGS, "https://www.douyin.com/note/7412345678901234567"
        )
        == "MS4wLjABAAAA-note"
    )


@pytest.mark.asyncio
async def test_resolve_sec_user_id_rejects_unknown_input() -> None:
    with pytest.raises(ValueError, match="sec_user_id"):
        await douyin.resolve_sec_user_id(SETTINGS, "随便一段文字")


@pytest.mark.asyncio
async def test_resolve_author_uses_latest_work(client: Any) -> None:
    client(responses_by_aid([response(data=_works_payload(count=2))]))

    resolved = await douyin.resolve_author(SETTINGS, "MS4wLjABAAAA-test")

    assert resolved.author.name == "作者"
    assert [work.work_id for work in resolved.feeds["video"]] == ["101", "100"]


@pytest.mark.asyncio
async def test_resolve_author_backfills_handle_from_profile(client: Any) -> None:
    """作品列表不带抖音号时，用主页资料补上，订阅列表才不会缺 ID。"""

    payload = _works_payload(count=1)
    payload["aweme_list"][0]["author"] = {"nickname": "作者"}
    created = client(
        responses_by_aid(
            [response(data=payload)],
            extra=[
                response(
                    data={
                        "status_code": 0,
                        "user": {
                            "nickname": "作者",
                            "unique_id": "30086054030",
                        },
                    }
                )
            ],
        )
    )

    resolved = await douyin.resolve_author(SETTINGS, "MS4wLjABAAAA-test")

    assert resolved.author.handle == "30086054030"
    assert len(created.requests) == 3
    assert created.requests[-1].url.path == "/aweme/v1/web/user/profile/other/"


@pytest.mark.asyncio
async def test_resolve_author_falls_back_to_profile(client: Any) -> None:
    client(
        responses_by_aid(
            [empty_page()],
            extra=[
                response(
                    data={
                        "status_code": 0,
                        "user": {"nickname": "作者", "unique_id": "deagle"},
                    }
                )
            ],
        )
    )

    resolved = await douyin.resolve_author(SETTINGS, "MS4wLjABAAAA-test")

    assert resolved.author.name == "作者"
    assert resolved.feeds["video"] == []


@pytest.mark.asyncio
async def test_ensure_ttwid_registers_cookie(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ttwid 注册成功后缓存起来，后续请求复用。"""

    client([httpx.Response(200, headers={"set-cookie": "ttwid=abc; Path=/"})])
    monkeypatch.setattr(douyin, "_ttwid", None)

    assert await _REAL_ENSURE_TTWID() == "abc"
    assert await _REAL_ENSURE_TTWID() == "abc"


def test_parse_cookie_splits_pairs() -> None:
    assert douyin._parse_cookie("a=1; b=2; broken") == {"a": "1", "b": "2"}
    assert douyin._parse_cookie(None) == {}


def test_extract_sec_user_id_from_page_unescapes() -> None:
    page = "https:\\/\\/www.douyin.com\\/user\\/MS4wLjABAAAA-page"

    assert douyin._extract_sec_user_id_from_page(page) == "MS4wLjABAAAA-page"


@pytest.mark.asyncio
async def test_resolve_sec_user_id_adds_missing_scheme(client: Any) -> None:
    """用户粘贴没有协议头的作品链接时补上 https://，而不是抛 UnsupportedProtocol。"""

    stub = client(
        [
            response(content=b""),
            response(data={"aweme_detail": {"author": {"sec_uid": "MS4wLjABAAAA-x"}}}),
        ]
    )

    assert (
        await douyin.resolve_sec_user_id(SETTINGS, "www.douyin.com/video/123")
        == "MS4wLjABAAAA-x"
    )
    assert str(stub.requests[0].url).startswith("https://www.douyin.com/video/123")
