import hashlib
from typing import Any, ClassVar

import httpx
import pytest

from http_stub import Stub, install
from nonebot_plugin_ifollow import bilibili_platform as bili
from nonebot_plugin_ifollow.errors import TransientError
from nonebot_plugin_ifollow.platforms import Cursor
from nonebot_plugin_ifollow.settings import Settings

SETTINGS = Settings(
    interval=1800,
    max_items=5,
    media_max_bytes=100 * 1024 * 1024,
    use_base64=False,
)


def test_extract_bilibili_links() -> None:
    assert bili.extract_bilibili_mid("https://space.bilibili.com/12345") == "12345"
    assert bili.extract_bilibili_mid("https://m.bilibili.com/space/12345") == "12345"
    assert bili.extract_bilibili_video(
        "https://www.bilibili.com/video/BV1AbcdefG12"
    ) == (
        "bvid",
        "BV1AbcdefG12",
    )
    assert bili.extract_bilibili_video("https://www.bilibili.com/video/av123456") == (
        "avid",
        "123456",
    )
    assert bili.is_bilibili_value("https://b23.tv/abc")
    assert bili.is_bilibili_value("b23.tv/abc")


@pytest.mark.asyncio
async def test_resolve_author_from_short_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve_short_link(_value: str) -> str:
        return "https://m.bilibili.com/space/123"

    async def fetch_profile(_settings: Settings, mid: str) -> bili.AuthorInfo:
        return bili.AuthorInfo(mid, "作者", mid)

    monkeypatch.setattr(bili, "_resolve_short_link", resolve_short_link)
    monkeypatch.setattr(bili, "fetch_author_profile", fetch_profile)

    resolved = await bili.resolve_author(SETTINGS, "b23.tv/profile")

    assert resolved.author.author_id == "123"


@pytest.mark.asyncio
async def test_resolve_author_from_short_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolve_short_link(_value: str) -> str:
        return "https://www.bilibili.com/video/BV1AbcdefG12"

    class FakeVideo:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def get_info(self) -> dict[str, Any]:
            return {"owner": {"mid": 123, "name": "作者", "face": "face"}}

    monkeypatch.setattr(bili, "_resolve_short_link", resolve_short_link)
    monkeypatch.setattr(bili, "Video", FakeVideo)

    resolved = await bili.resolve_author(SETTINGS, "b23.tv/video")

    assert resolved.author.author_id == "123"


@pytest.mark.asyncio
async def test_resolve_short_link_uses_browser_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = install(
        monkeypatch,
        Stub(
            [
                httpx.Response(
                    302,
                    headers={"location": "https://www.bilibili.com/video/BV1AbcdefG12"},
                ),
                httpx.Response(200, content=b"ok"),
            ]
        ),
    )

    resolved = await bili._resolve_short_link("b23.tv/video")

    assert resolved.endswith("BV1AbcdefG12")
    assert (
        stub.requests[0].headers["user-agent"]
        == bili.BILIBILI_BROWSER_HEADERS["User-Agent"]
    )
    assert str(stub.requests[0].url) == "https://b23.tv/video"


def app_response(payload: dict[str, Any]) -> httpx.Response:
    """构造 APP 接口响应；带上 request 以便 raise_for_status 可用。"""

    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", "https://app.bilibili.com/x/test"),
    )


class FakeUser:
    pages: ClassVar[list[dict[str, Any]]] = []
    dynamic_result: ClassVar[dict[str, Any]] = {}

    def __init__(self, _mid: int, credential: object = None) -> None:
        self.calls: list[dict[str, Any]] = []

    async def get_user_info(self) -> dict[str, Any]:
        return {"mid": 123, "name": "作者", "face": "face", "sign": "签名"}

    async def get_media_list(self, **params: Any) -> dict[str, Any]:
        self.calls.append(params)
        return self.pages[min(len(self.calls) - 1, len(self.pages) - 1)]

    async def get_dynamics_new(self, **_params: Any) -> dict[str, Any]:
        return self.dynamic_result


def use_app_responses(
    monkeypatch: pytest.MonkeyPatch, *payloads: dict[str, Any]
) -> Stub:
    """让 APP 接口按顺序返回这些 payload，并返回请求记录器。"""

    return install(monkeypatch, Stub([app_response(payload) for payload in payloads]))


def test_app_sign_matches_md5_of_sorted_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bili.time, "time", lambda: 1_700_000_000)

    signed = bili._sign_app_params(
        {"vmid": "123", "ps": "20", "order": "pubdate"}, "appkey", "appsec"
    )

    query = (
        "appkey=appkey&build=8130300&mobi_app=android&order=pubdate"
        "&platform=android&ps=20&ts=1700000000&vmid=123"
    )
    assert signed["sign"] == hashlib.md5(f"{query}appsec".encode()).hexdigest()


@pytest.mark.asyncio
async def test_app_get_rotates_appkey_after_failure() -> None:
    stub = Stub(
        [
            app_response({"code": -400, "message": "请求错误"}),
            app_response({"code": 0, "data": {"ok": True}}),
        ]
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(stub)) as client:
        data = await bili._app_get(client, "/x/test", {})

    assert data == {"ok": True}
    assert len(stub.requests) == 2


@pytest.mark.asyncio
async def test_app_get_rejects_missing_data() -> None:
    stub = Stub([app_response({"code": 0})])

    async with httpx.AsyncClient(transport=httpx.MockTransport(stub)) as client:
        with pytest.raises(TransientError):
            await bili._app_get(client, "/x/test", {})


@pytest.mark.asyncio
async def test_app_get_marks_http_errors_retryable() -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        raise RuntimeError("HTTP 503")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
        with pytest.raises(TransientError) as exc_info:
            await bili._app_get(client, "/x/test", {})

    assert isinstance(exc_info.value, TransientError)


@pytest.mark.asyncio
async def test_fetch_profile_prefers_app(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = use_app_responses(
        monkeypatch,
        {
            "code": 0,
            "data": {
                "card": {
                    "mid": "123",
                    "name": "作者",
                    "face": "face",
                    "sign": "签名",
                }
            },
        },
    )

    profile = await bili.fetch_author_profile(SETTINGS, "123")

    assert profile.name == "作者"
    assert profile.description == "签名"
    assert stub.requests[0].url.path == "/x/v2/space"


@pytest.mark.asyncio
async def test_fetch_profile_falls_back_to_web(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_app_responses(monkeypatch, {"code": -400, "message": "请求错误"})
    monkeypatch.setattr(bili, "User", FakeUser)

    profile = await bili.fetch_author_profile(SETTINGS, "123")

    assert profile.name == "作者"


@pytest.mark.asyncio
async def test_fetch_video_works_prefers_app(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = use_app_responses(
        monkeypatch,
        {
            "code": 0,
            "data": {
                "item": [
                    {
                        "bvid": "BV1AbcdefG12",
                        "title": "标题",
                        "ctime": 200,
                        "param": "100",
                        "author": "作者",
                    }
                ],
                "has_next": False,
            },
        },
    )

    works = await bili.fetch(SETTINGS, "123", "video", None)

    assert [work.work_id for work in works] == ["BV1AbcdefG12"]
    assert works[0].author.author_id == "123"
    assert works[0].create_time == 200
    assert stub.requests[0].url.params["vmid"] == "123"


@pytest.mark.asyncio
async def test_app_video_pagination_uses_last_aid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = use_app_responses(
        monkeypatch,
        {
            "code": 0,
            "data": {
                "item": [
                    {
                        "bvid": "BV100",
                        "title": "新",
                        "ctime": 200,
                        "param": "100",
                    }
                ],
                "has_next": True,
            },
        },
        {
            "code": 0,
            "data": {
                "item": [
                    {
                        "bvid": "BV99",
                        "title": "旧",
                        "ctime": 100,
                        "param": "99",
                    }
                ],
                "has_next": False,
            },
        },
    )

    works = await bili.fetch(SETTINGS, "123", "video", Cursor("BV1"))

    assert [work.work_id for work in works] == ["BV100", "BV99"]
    assert stub.requests[1].url.params["aid"] == "100"


@pytest.mark.asyncio
async def test_app_video_pagination_stops_at_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = use_app_responses(
        monkeypatch,
        {
            "code": 0,
            "data": {
                "item": [
                    {"bvid": "BV100", "ctime": 200, "param": "100"},
                    {"bvid": "BV99", "ctime": 100, "param": "99"},
                ],
                "has_next": True,
            },
        },
    )

    works = await bili.fetch(SETTINGS, "123", "video", Cursor("BV99"))

    assert [work.work_id for work in works] == ["BV100", "BV99"]
    assert len(stub.requests) == 1


@pytest.mark.asyncio
async def test_empty_app_video_list_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_app_responses(monkeypatch, {"code": 0, "data": {"item": [], "has_next": False}})

    def fail_user(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("不应回退网页接口")

    monkeypatch.setattr(bili, "User", fail_user)

    assert await bili.fetch(SETTINGS, "123", "video", None) == []


@pytest.mark.asyncio
async def test_fetch_video_works_falls_back_to_web(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_app_responses(monkeypatch, {"code": -400, "message": "请求错误"})
    FakeUser.pages = [
        {
            "media_list": [
                {
                    "id": 100,
                    "bv_id": "BV1AbcdefG12",
                    "title": "标题",
                    "intro": "简介",
                    "pubtime": 200,
                    "upper": {"mid": 123, "name": "作者", "face": "face"},
                }
            ],
            "has_more": False,
        }
    ]
    monkeypatch.setattr(bili, "User", FakeUser)

    works = await bili.fetch(SETTINGS, "123", "video", None)

    assert works[0].work_id == "BV1AbcdefG12"
    assert works[0].author.author_id == "123"


def test_parse_app_video_item_strips_joint_creation_suffix() -> None:
    work = bili._parse_app_video_item(
        {
            "bvid": "BV1AbcdefG12",
            "title": "联合投稿",
            "ctime": 1,
            "author": "合作作者 等联合创作",
        },
        "123",
    )

    assert work is not None
    assert work.author.name == "合作作者"


def test_parse_app_video_item_ignores_invalid_items() -> None:
    assert bili._parse_app_video_item(None, "123") is None
    assert bili._parse_app_video_item({"title": "缺少 bvid"}, "123") is None


@pytest.mark.asyncio
async def test_video_pagination_uses_last_aid_on_web_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_app_responses(monkeypatch, {"code": -400, "message": "请求错误"})
    FakeUser.pages = [
        {
            "media_list": [
                {"id": 100, "bv_id": "BV100", "title": "新", "pubtime": 200}
            ],
            "has_more": True,
        },
        {
            "media_list": [
                {"id": 99, "bv_id": "BV99", "title": "游标", "pubtime": 100}
            ],
            "has_more": True,
        },
    ]
    instances: list[FakeUser] = []

    def make_user(mid: int, credential: object = None) -> FakeUser:
        user = FakeUser(mid, credential)
        instances.append(user)
        return user

    monkeypatch.setattr(bili, "User", make_user)

    works = await bili.fetch(SETTINGS, "123", "video", Cursor("BV99"))

    assert [work.work_id for work in works] == ["BV100", "BV99"]
    assert instances[0].calls[1]["oid"] == 100


@pytest.mark.asyncio
async def test_dynamic_mapping_and_bvid(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeUser.dynamic_result = {
        "items": [
            {
                "id_str": "dynamic-1",
                "type": "DYNAMIC_TYPE_AV",
                "modules": {
                    "module_author": {"mid": 123, "name": "作者", "pub_ts": 200},
                    "module_dynamic": {
                        "major": {
                            "type": "MAJOR_TYPE_ARCHIVE",
                            "archive": {
                                "bvid": "BV1AbcdefG12",
                                "title": "视频动态",
                                "desc": "文案",
                            },
                        }
                    },
                },
            },
            {
                "id_str": "dynamic-2",
                "type": "DYNAMIC_TYPE_FORWARD",
                "modules": {
                    "module_author": {"mid": 123, "name": "作者", "pub_ts": 100},
                    "module_dynamic": {
                        "desc": {"text": "转发内容"},
                    },
                },
            },
        ],
        "has_more": False,
        "offset": "dynamic-2",
    }
    monkeypatch.setattr(bili, "User", FakeUser)

    works = await bili.fetch(SETTINGS, "123", "dynamic", None)

    assert works[0].bvid == "BV1AbcdefG12"
    assert works[0].kind == "video"
    assert works[1].kind == "dynamic"
    assert works[1].url == "https://t.bilibili.com/dynamic-2"


@pytest.mark.asyncio
async def test_bilibili_errors_mark_412_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_app_responses(monkeypatch, {"code": -400, "message": "请求错误"})

    class FailingUser(FakeUser):
        async def get_media_list(self, **_params: Any) -> dict[str, Any]:
            raise RuntimeError("HTTP 412")

    monkeypatch.setattr(bili, "User", FailingUser)

    with pytest.raises(TransientError) as exc_info:
        await bili.fetch(SETTINGS, "123", "video", None)

    assert isinstance(exc_info.value, TransientError)


def test_dynamics_enabled_only_with_cookie() -> None:
    assert not SETTINGS.dynamics_enabled
    assert Settings(
        interval=1800,
        max_items=5,
        media_max_bytes=0,
        use_base64=False,
        bilibili_cookie="SESSDATA=x",
    ).dynamics_enabled


@pytest.mark.asyncio
async def test_warm_up_swallows_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(bili, "get_buvid", fail)

    await bili.warm_up()


@pytest.mark.asyncio
async def test_warm_up_calls_buvid(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def ok() -> None:
        calls.append(True)

    monkeypatch.setattr(bili, "get_buvid", ok)

    await bili.warm_up()

    assert calls == [True]


@pytest.mark.asyncio
async def test_app_video_works_omits_ps_and_stops_at_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不再传分页条数；攒够补发上限后不再翻页。"""

    stub = use_app_responses(
        monkeypatch,
        {
            "code": 0,
            "data": {
                "item": [
                    {"bvid": f"BV1{index}", "ctime": 200 - index, "param": str(index)}
                    for index in range(5)
                ],
                "has_next": True,
            },
        },
    )

    works = await bili.fetch(SETTINGS, "123", "video", Cursor("BV1old"))

    assert len(works) == 5
    assert len(stub.requests) == 1
    assert "ps" not in stub.requests[0].url.params


@pytest.mark.asyncio
async def test_web_fallback_ignores_non_dict_last_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """网页兜底遇到非对象条目时按「没有翻页游标」处理，不抛 AttributeError。"""

    use_app_responses(monkeypatch, {"code": -400, "message": "请求错误"})
    FakeUser.pages = [
        {
            "media_list": [
                {
                    "id": 100,
                    "bv_id": "BV1AbcdefG12",
                    "title": "标题",
                    "pubtime": 200,
                    "upper": {"mid": 123, "name": "作者"},
                },
                "broken",
            ],
            "has_more": True,
        }
    ]
    monkeypatch.setattr(bili, "User", FakeUser)

    works = await bili.fetch(SETTINGS, "123", "video", None)

    assert [work.work_id for work in works] == ["BV1AbcdefG12"]


@pytest.mark.asyncio
async def test_web_video_fallback_pages_through_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """APP 失败后网页接口按 aid 翻页，直到平台说没有更多。"""

    use_app_responses(monkeypatch, {"code": -400, "message": "请求错误"})
    FakeUser.pages = [
        {
            "media_list": [
                {"id": 200, "bv_id": "BV200", "title": "新", "pubtime": 200}
            ],
            "has_more": True,
        },
        {
            "media_list": [
                {"id": 100, "bv_id": "BV100", "title": "旧", "pubtime": 100}
            ],
            "has_more": False,
        },
    ]
    monkeypatch.setattr(bili, "User", FakeUser)

    works = await bili.fetch(SETTINGS, "123", "video", Cursor("old", 1))

    assert [work.work_id for work in works] == ["BV200", "BV100"]


@pytest.mark.asyncio
async def test_dynamics_are_mapped_from_web_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeUser.dynamic_result = {
        "items": [
            {
                "id_str": "777",
                "modules": {
                    "module_author": {"mid": 123, "name": "作者", "pub_ts": 500},
                    "module_dynamic": {
                        "major": {
                            "opus": {
                                "jump_url": "//www.bilibili.com/opus/777",
                                "title": "动态标题",
                                "summary": {"text": "动态正文"},
                            }
                        }
                    },
                },
            }
        ],
        "has_more": False,
        "offset": "",
    }
    monkeypatch.setattr(bili, "User", FakeUser)

    works = await bili.fetch(SETTINGS, "123", "dynamic", None)

    assert works[0].kind == "dynamic"
    assert works[0].url == "https://www.bilibili.com/opus/777"
    assert works[0].description == "动态正文"


@pytest.mark.asyncio
async def test_resolve_author_from_bv_link(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeVideo:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def get_info(self) -> dict[str, Any]:
            return {"owner": {"mid": 123}}

    async def profile(_settings: Settings, mid: str) -> bili.AuthorInfo:
        return bili.AuthorInfo(mid, "作者", mid)

    async def fetch(
        _settings: Settings, _mid: str, _feed: str, _cursor: Cursor | None = None
    ) -> list[Any]:
        return []

    monkeypatch.setattr(bili, "Video", FakeVideo)
    monkeypatch.setattr(bili, "fetch_author_profile", profile)
    monkeypatch.setattr(bili, "fetch", fetch)

    resolved = await bili.resolve_author(
        SETTINGS, "https://www.bilibili.com/video/BV1AbcdefG12"
    )

    assert resolved.author.author_id == "123"


@pytest.mark.asyncio
async def test_resolve_author_rejects_unknown_link() -> None:
    with pytest.raises(ValueError, match="BV/AV"):
        await bili.resolve_author(SETTINGS, "https://www.bilibili.com/read/1")


def test_parse_cookie_splits_pairs() -> None:
    assert bili._parse_cookie("SESSDATA=a; bili_jct=b") == {
        "SESSDATA": "a",
        "bili_jct": "b",
    }
