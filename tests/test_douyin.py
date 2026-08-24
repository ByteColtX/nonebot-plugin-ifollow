from typing import ClassVar

import httpx
import pytest

from nonebot_plugin_douyin_subscribe import douyin


class FakeResponse:
    def __init__(
        self, *, url: str, payload: object = None, status_code: int = 200
    ) -> None:
        self.url = httpx.URL(url)
        self._payload = payload
        self.text = payload if isinstance(payload, str) else ""
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=httpx.Request("GET", self.url),
                response=httpx.Response(self.status_code),
            )

    def json(self) -> object:
        if isinstance(self._payload, str):
            raise ValueError("invalid JSON")
        return self._payload


class FakeAsyncClient:
    response: FakeResponse
    responses: ClassVar[list[FakeResponse]] = []
    last_kwargs: ClassVar[dict[str, object]] = {}
    last_request: ClassVar[tuple[str, dict[str, object]] | None] = None

    def __init__(self, **kwargs: object) -> None:
        type(self).last_kwargs = kwargs
        self.closed = False

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.closed = True
        return None

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        assert not self.closed
        type(self).last_request = (url, kwargs)
        if type(self).responses:
            return type(self).responses.pop(0)
        return type(self).response


def test_extract_sec_user_id_supports_profile_url_and_raw_id() -> None:
    assert (
        douyin.extract_sec_user_id(
            "https://www.douyin.com/user/MS4wLjABAAAA-test?from_tab=main"
        )
        == "MS4wLjABAAAA-test"
    )
    assert douyin.extract_sec_user_id("MS4wLjABAAAA-test") == "MS4wLjABAAAA-test"
    assert (
        douyin.extract_sec_user_id(
            "https://www.iesdouyin.com/share/user/MS4wLjABAAAA-share"
        )
        == "MS4wLjABAAAA-share"
    )
    assert douyin.extract_sec_user_id("https://www.douyin.com/video/123") is None


@pytest.mark.asyncio
async def test_resolve_sec_user_id_follows_short_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAsyncClient.response = FakeResponse(
        url="https://www.douyin.com/user/MS4wLjABAAAA-resolved"
    )
    monkeypatch.setattr(douyin.httpx, "AsyncClient", FakeAsyncClient)

    result = await douyin.resolve_sec_user_id("https://v.douyin.com/abc123/")

    assert result == "MS4wLjABAAAA-resolved"
    assert FakeAsyncClient.last_request == ("https://v.douyin.com/abc123", {})


@pytest.mark.asyncio
async def test_resolve_sec_user_id_supports_iesdouyin_author_share_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAsyncClient.response = FakeResponse(
        url="https://www.iesdouyin.com/share/user/MS4wLjABAAAA-share"
    )
    monkeypatch.setattr(douyin.httpx, "AsyncClient", FakeAsyncClient)

    result = await douyin.resolve_sec_user_id(
        "抖音订阅 https://v.douyin.com/hTjwO7-Meqg/"
    )

    assert result == "MS4wLjABAAAA-share"


@pytest.mark.asyncio
async def test_resolve_sec_user_id_extracts_author_from_work_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAsyncClient.responses = [
        FakeResponse(url="https://www.douyin.com/video/123456"),
        FakeResponse(
            url="https://www.douyin.com/aweme/v1/web/aweme/detail/",
            payload={
                "aweme_detail": {
                    "author": {
                        "nickname": "作者",
                        "sec_uid": "MS4wLjABAAAA-author-id",
                    }
                }
            },
        ),
    ]
    monkeypatch.setattr(douyin.httpx, "AsyncClient", FakeAsyncClient)

    result = await douyin.resolve_sec_user_id("https://v.douyin.com/abc123/")

    assert result == "MS4wLjABAAAA-author-id"


@pytest.mark.asyncio
async def test_fetch_latest_works_sorts_and_ignores_invalid_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAsyncClient.response = FakeResponse(
        url="https://www.douyin.com/aweme/v1/web/aweme/post/",
        payload={
            "status_code": 0,
            "aweme_list": [
                {"aweme_id": "1", "create_time": 10, "author": {"nickname": "作者"}},
                {"aweme_id": "3", "create_time": 30, "desc": "最新"},
                {
                    "aweme_id": "2",
                    "create_time": 20,
                    "author": {"unique_id": "douyin_id"},
                },
                {"desc": "missing id"},
                "invalid",
            ],
        },
    )
    monkeypatch.setattr(douyin.httpx, "AsyncClient", FakeAsyncClient)

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert [work.aweme_id for work in works] == ["3", "2", "1"]
    assert works[0].share_url == "https://www.douyin.com/video/3"
    assert works[1].author_name == "MS4wLjABAAAA-test"
    assert works[1].author_unique_id == "douyin_id"
    assert FakeAsyncClient.last_request is not None
    params = FakeAsyncClient.last_request[1].get("params")
    assert isinstance(params, dict)
    assert params["sec_user_id"] == "MS4wLjABAAAA-test"


@pytest.mark.asyncio
async def test_fetch_latest_works_rejects_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAsyncClient.response = FakeResponse(
        url="https://www.douyin.com/aweme/v1/web/aweme/post/",
        payload={"status_code": 1001, "aweme_list": []},
    )
    monkeypatch.setattr(douyin.httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(douyin.DouyinRequestError, match="1001"):
        await douyin.fetch_latest_works("MS4wLjABAAAA-test")


@pytest.mark.asyncio
async def test_fetch_latest_works_reports_http_error_and_marks_it_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAsyncClient.response = FakeResponse(
        url="https://www.douyin.com/aweme/v1/web/aweme/post/",
        payload="<html><body>Access Denied</body></html>",
        status_code=403,
    )
    monkeypatch.setattr(douyin.httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(douyin.DouyinRequestError, match="HTTP 403") as exc_info:
        await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert exc_info.value.retryable


@pytest.mark.asyncio
async def test_fetch_latest_works_reports_empty_response_as_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAsyncClient.response = FakeResponse(
        url="https://www.douyin.com/aweme/v1/web/aweme/post/",
        payload="",
    )
    monkeypatch.setattr(douyin.httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(douyin.DouyinRequestError, match="<empty>") as exc_info:
        await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert exc_info.value.retryable


@pytest.mark.asyncio
async def test_fetch_author_profile_extracts_unique_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAsyncClient.response = FakeResponse(
        url="https://www.douyin.com/aweme/v1/web/user/profile/other/",
        payload={
            "status_code": 0,
            "user": {"nickname": "Kitty", "unique_id": "Kitty06666"},
        },
    )
    monkeypatch.setattr(douyin.httpx, "AsyncClient", FakeAsyncClient)

    result = await douyin.fetch_author_profile("MS4wLjABAAAA-test")

    assert result == ("Kitty", "Kitty06666")


def test_extract_unique_id_ignores_short_id_placeholder() -> None:
    assert (
        douyin._extract_unique_id({"unique_id": "Kitty06666", "short_id": "0"})
        == "Kitty06666"
    )
    assert douyin._extract_unique_id({"short_id": "0"}) == ""
