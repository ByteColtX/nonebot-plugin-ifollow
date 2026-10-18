import hashlib
from typing import Any, ClassVar, cast

import httpx
import pytest

from nonebot_plugin_ifollow import bilibili_platform as bili
from nonebot_plugin_ifollow.platforms import Cursor


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

    async def fetch_profile(mid: str) -> bili.AuthorInfo:
        return bili.AuthorInfo(mid, "作者", mid)

    monkeypatch.setattr(bili, "_resolve_short_link", resolve_short_link)
    monkeypatch.setattr(bili, "fetch_author_profile", fetch_profile)

    author = await bili.resolve_bilibili_author("b23.tv/profile")

    assert author.author_id == "123"


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

    author = await bili.resolve_bilibili_author("b23.tv/video")

    assert author.author_id == "123"


@pytest.mark.asyncio
async def test_resolve_short_link_uses_browser_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        url = "https://www.bilibili.com/video/BV1AbcdefG12"

        def raise_for_status(self) -> None:
            return

    class FakeClient:
        kwargs: dict[str, Any]
        requested_url: str

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def __aenter__(self) -> "FakeClient":
            FakeClient.kwargs = self.kwargs
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return

        async def get(self, url: str) -> FakeResponse:
            FakeClient.requested_url = url
            return FakeResponse()

    monkeypatch.setattr(bili.httpx, "AsyncClient", FakeClient)

    resolved = await bili._resolve_short_link("b23.tv/video")

    assert resolved.endswith("BV1AbcdefG12")
    assert FakeClient.kwargs["headers"] == bili.BILIBILI_BROWSER_HEADERS
    assert FakeClient.requested_url == "https://b23.tv/video"


class FakeAppResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeAppClient:
    responses: ClassVar[list[dict[str, Any]]] = []
    requests: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeAppClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return

    async def get(
        self, url: str, params: dict[str, str] | None = None
    ) -> FakeAppResponse:
        FakeAppClient.requests.append({"url": url, "params": params or {}})
        index = min(
            len(FakeAppClient.requests) - 1,
            len(FakeAppClient.responses) - 1,
        )
        return FakeAppResponse(FakeAppClient.responses[index])


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
    monkeypatch: pytest.MonkeyPatch, *responses: dict[str, Any]
) -> None:
    FakeAppClient.responses = list(responses)
    FakeAppClient.requests = []
    monkeypatch.setattr(bili, "_app_client", lambda: FakeAppClient())


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
    class RotatingClient:
        calls = 0

        async def get(
            self, _url: str, params: dict[str, str] | None = None
        ) -> FakeAppResponse:
            RotatingClient.calls += 1
            if RotatingClient.calls == 1:
                return FakeAppResponse({"code": -400, "message": "请求错误"})
            return FakeAppResponse({"code": 0, "data": {"ok": True}})

    data = await bili._app_get(cast(httpx.AsyncClient, RotatingClient()), "/x/test", {})

    assert data == {"ok": True}
    assert RotatingClient.calls == 2


@pytest.mark.asyncio
async def test_app_get_rejects_missing_data() -> None:
    class BadClient:
        async def get(
            self, _url: str, params: dict[str, str] | None = None
        ) -> FakeAppResponse:
            return FakeAppResponse({"code": 0})

    with pytest.raises(bili.BilibiliRequestError):
        await bili._app_get(cast(httpx.AsyncClient, BadClient()), "/x/test", {})


@pytest.mark.asyncio
async def test_app_get_marks_http_errors_retryable() -> None:
    class FailingClient:
        async def get(
            self, _url: str, params: dict[str, str] | None = None
        ) -> FakeAppResponse:
            raise RuntimeError("HTTP 503")

    with pytest.raises(bili.BilibiliRequestError) as exc_info:
        await bili._app_get(cast(httpx.AsyncClient, FailingClient()), "/x/test", {})

    assert exc_info.value.retryable


@pytest.mark.asyncio
async def test_fetch_profile_prefers_app(monkeypatch: pytest.MonkeyPatch) -> None:
    use_app_responses(
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

    profile = await bili.fetch_author_profile("123")

    assert profile.name == "作者"
    assert profile.description == "签名"
    assert FakeAppClient.requests[0]["url"].endswith("/x/v2/space")


@pytest.mark.asyncio
async def test_fetch_profile_falls_back_to_web(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_app_responses(monkeypatch, {"code": -400, "message": "请求错误"})
    monkeypatch.setattr(bili, "User", FakeUser)

    profile = await bili.fetch_author_profile("123")

    assert profile.name == "作者"


@pytest.mark.asyncio
async def test_fetch_video_works_prefers_app(monkeypatch: pytest.MonkeyPatch) -> None:
    use_app_responses(
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

    works = await bili.fetch_video_works("123")

    assert [work.work_id for work in works] == ["BV1AbcdefG12"]
    assert works[0].author.author_id == "123"
    assert works[0].create_time == 200
    assert FakeAppClient.requests[0]["params"]["vmid"] == "123"


@pytest.mark.asyncio
async def test_app_video_pagination_uses_last_aid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_app_responses(
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

    works = await bili.fetch_video_works("123", cursor=Cursor("BV1"))

    assert [work.work_id for work in works] == ["BV100", "BV99"]
    assert FakeAppClient.requests[1]["params"]["aid"] == "100"


@pytest.mark.asyncio
async def test_app_video_pagination_stops_at_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_app_responses(
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

    works = await bili.fetch_video_works("123", cursor=Cursor("BV99"))

    assert [work.work_id for work in works] == ["BV100", "BV99"]
    assert len(FakeAppClient.requests) == 1


@pytest.mark.asyncio
async def test_empty_app_video_list_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    use_app_responses(monkeypatch, {"code": 0, "data": {"item": [], "has_next": False}})

    def fail_user(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("不应回退网页接口")

    monkeypatch.setattr(bili, "User", fail_user)

    assert await bili.fetch_video_works("123") == []


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

    works = await bili.fetch_video_works("123")

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

    works = await bili.fetch_video_works("123", cursor=Cursor("BV99"))

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

    works = await bili.fetch_dynamic_works("123")

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

    with pytest.raises(bili.BilibiliRequestError) as exc_info:
        await bili.fetch_video_works("123")

    assert exc_info.value.retryable


def test_dynamics_enabled_only_with_cookie(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bili.config, "ifollow_bilibili_cookie", None)
    assert not bili.bilibili_dynamics_enabled()

    monkeypatch.setattr(bili.config, "ifollow_bilibili_cookie", "SESSDATA=x")
    assert bili.bilibili_dynamics_enabled()


@pytest.mark.asyncio
async def test_warm_up_swallows_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(bili, "get_buvid", fail)

    await bili.warm_up_bilibili()


@pytest.mark.asyncio
async def test_warm_up_calls_buvid(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    async def ok() -> None:
        calls.append(True)

    monkeypatch.setattr(bili, "get_buvid", ok)

    await bili.warm_up_bilibili()

    assert calls == [True]
