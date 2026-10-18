from typing import Any, cast

import pytest

from nonebot_plugin_ifollow import douyin_platform as douyin
from nonebot_plugin_ifollow.platforms import Cursor


def test_extract_sec_user_id() -> None:
    assert (
        douyin.extract_sec_user_id(
            "https://www.douyin.com/user/MS4wLjABAAAA-test?from_tab=main"
        )
        == "MS4wLjABAAAA-test"
    )
    assert douyin.extract_sec_user_id("MS4wLjABAAAA-test") == "MS4wLjABAAAA-test"
    assert douyin.extract_sec_user_id("https://www.douyin.com/video/123") is None


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        content: bytes = b"{}",
        data: Any = None,
    ) -> None:
        self.status_code = status_code
        self.content = content
        self.text = content.decode("utf-8", errors="replace")
        self._data = data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = douyin.httpx.Request("GET", "https://www.douyin.com/")
            raise douyin.httpx.HTTPStatusError(
                "boom",
                request=request,
                response=douyin.httpx.Response(self.status_code, request=request),
            )

    def json(self) -> Any:
        if self._data is None:
            raise ValueError("not json")
        return self._data


class FakeClient:
    """记录请求并按顺序返回预设响应的 httpx.AsyncClient 替身。"""

    def __init__(self, responses: list[FakeResponse], **_kwargs: Any) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self._responses[min(len(self.calls), len(self._responses)) - 1]


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


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """安装替身客户端，并让 ttwid 每次注册都返回新值。"""

    created: list[FakeClient] = []

    def factory(responses: list[FakeResponse]) -> Any:
        def build(**kwargs: Any) -> FakeClient:
            fake = FakeClient(responses, **kwargs)
            created.append(fake)
            return fake

        monkeypatch.setattr(douyin.httpx, "AsyncClient", build)
        return created

    counter = iter(range(100))
    ttwids: list[str] = []

    async def ensure_ttwid() -> str:
        ttwid = f"ttwid-{next(counter)}"
        ttwids.append(ttwid)
        return ttwid

    monkeypatch.setattr(douyin, "_ensure_ttwid", ensure_ttwid)
    monkeypatch.setattr(douyin, "_ttwid", None)
    monkeypatch.setattr(douyin.config, "ifollow_douyin_cookie", None)
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
    client([FakeResponse(data=_works_payload())])

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert works[0].platform == "douyin"
    assert works[0].work_id == "100"
    assert works[0].author.name == "作者"
    assert works[0].author.handle == "handle"


@pytest.mark.asyncio
async def test_fetch_latest_works_sorts_newest_first(client: Any) -> None:
    client([FakeResponse(data=_works_payload(count=3))])

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert [work.work_id for work in works] == ["102", "101", "100"]


@pytest.mark.asyncio
async def test_fetch_latest_works_reports_missing_author(client: Any) -> None:
    client([FakeResponse(data={"status_code": douyin.INVALID_AUTHOR_STATUS})])

    with pytest.raises(douyin.DouyinRequestError) as excinfo:
        await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert excinfo.value.retryable is False
    assert "不存在" in str(excinfo.value)


@pytest.mark.asyncio
async def test_fetch_latest_works_retries_with_new_ttwid_on_empty_body(
    client: Any,
) -> None:
    client([FakeResponse(content=b""), FakeResponse(data=_works_payload())])

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert works[0].work_id == "100"
    assert client.ttwids == ["ttwid-0", "ttwid-1"]
    assert douyin._ttwid is None


@pytest.mark.asyncio
async def test_fetch_latest_works_retries_on_blocked_status(client: Any) -> None:
    client(
        [
            FakeResponse(status_code=403, content=b"Blocked by ArgusSecurityPlugin"),
            FakeResponse(data=_works_payload()),
        ]
    )

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert works
    assert len(client.ttwids) == 2


@pytest.mark.asyncio
async def test_fetch_latest_works_gives_up_after_second_block(client: Any) -> None:
    client([FakeResponse(content=b""), FakeResponse(content=b"")])

    with pytest.raises(douyin.DouyinRequestError) as excinfo:
        await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert excinfo.value.retryable is True
    assert "风控" in str(excinfo.value)


@pytest.mark.asyncio
async def test_fetch_latest_works_rejects_non_json_body(client: Any) -> None:
    client([FakeResponse(content=b"<html></html>")])

    with pytest.raises(douyin.DouyinRequestError) as excinfo:
        await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert excinfo.value.retryable is True


@pytest.mark.asyncio
async def test_fetch_work_author_returns_none_when_detail_blocked() -> None:
    fake = FakeClient([FakeResponse(content=b""), FakeResponse(content=b"")])

    author = await douyin._fetch_work_author(cast(Any, fake), "100")

    assert author is None


@pytest.mark.asyncio
async def test_fetch_author_profile_reads_nickname_and_handle(client: Any) -> None:
    client(
        [
            FakeResponse(
                data={
                    "status_code": 0,
                    "user": {"nickname": "作者", "unique_id": "deagle"},
                }
            )
        ]
    )

    nickname, handle = await douyin.fetch_author_profile("MS4wLjABAAAA-test")

    assert (nickname, handle) == ("作者", "deagle")


@pytest.mark.asyncio
async def test_fetch_latest_works_pages_until_cursor(client: Any) -> None:
    """停机久了要按页补齐，直到翻到游标作品。"""

    created = client(
        [
            FakeResponse(
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
            FakeResponse(
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

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test", Cursor("100", 10))

    assert [work.work_id for work in works] == ["103", "100"]
    assert len(created[0].calls) == 2


@pytest.mark.asyncio
async def test_fetch_latest_works_without_cursor_requests_one_page(
    client: Any,
) -> None:
    created = client(
        [
            FakeResponse(
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

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert [work.work_id for work in works] == ["103"]
    assert len(created[0].calls) == 1
