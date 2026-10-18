from typing import Any

import httpx
import pytest

from http_stub import Stub, install
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


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """安装 MockTransport 客户端，并让 ttwid 每次注册都返回新值。"""

    counter = iter(range(100))
    ttwids: list[str] = []

    def factory(responses: list[httpx.Response]) -> Stub:
        return install(monkeypatch, Stub(responses))

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
    client([response(data=_works_payload())])

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert works[0].platform == "douyin"
    assert works[0].work_id == "100"
    assert works[0].author.name == "作者"
    assert works[0].author.handle == "handle"


@pytest.mark.asyncio
async def test_fetch_latest_works_sorts_newest_first(client: Any) -> None:
    client([response(data=_works_payload(count=3))])

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert [work.work_id for work in works] == ["102", "101", "100"]


@pytest.mark.asyncio
async def test_fetch_latest_works_reports_missing_author(client: Any) -> None:
    client([response(data={"status_code": douyin.INVALID_AUTHOR_STATUS})])

    with pytest.raises(douyin.DouyinRequestError) as excinfo:
        await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert excinfo.value.retryable is False
    assert "不存在" in str(excinfo.value)


@pytest.mark.asyncio
async def test_fetch_latest_works_retries_with_new_ttwid_on_empty_body(
    client: Any,
) -> None:
    client([response(content=b""), response(data=_works_payload())])

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert works[0].work_id == "100"
    assert client.ttwids == ["ttwid-0", "ttwid-1"]
    assert douyin._ttwid is None


@pytest.mark.asyncio
async def test_fetch_latest_works_retries_on_blocked_status(client: Any) -> None:
    client(
        [
            response(status_code=403, content=b"Blocked by ArgusSecurityPlugin"),
            response(data=_works_payload()),
        ]
    )

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert works
    assert len(client.ttwids) == 2


@pytest.mark.asyncio
async def test_fetch_latest_works_gives_up_after_second_block(client: Any) -> None:
    client([response(content=b""), response(content=b"")])

    with pytest.raises(douyin.DouyinRequestError) as excinfo:
        await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert excinfo.value.retryable is True
    assert "风控" in str(excinfo.value)


@pytest.mark.asyncio
async def test_fetch_latest_works_rejects_non_json_body(client: Any) -> None:
    client([response(content=b"<html></html>")])

    with pytest.raises(douyin.DouyinRequestError) as excinfo:
        await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert excinfo.value.retryable is True


@pytest.mark.asyncio
async def test_fetch_work_author_returns_none_when_detail_blocked() -> None:
    stub = Stub([response(content=b""), response(content=b"")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(stub)) as fake:
        author = await douyin._fetch_work_author(fake, "100")

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

    nickname, handle = await douyin.fetch_author_profile("MS4wLjABAAAA-test")

    assert (nickname, handle) == ("作者", "deagle")


@pytest.mark.asyncio
async def test_fetch_latest_works_pages_until_cursor(client: Any) -> None:
    """停机久了要按页补齐，直到翻到游标作品。"""

    created = client(
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

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test", Cursor("100", 10))

    assert [work.work_id for work in works] == ["103", "100"]
    assert len(created.requests) == 2


@pytest.mark.asyncio
async def test_fetch_latest_works_without_cursor_requests_one_page(
    client: Any,
) -> None:
    created = client(
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

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")

    assert [work.work_id for work in works] == ["103"]
    assert len(created.requests) == 1


@pytest.mark.asyncio
async def test_fetch_latest_works_omits_count_and_stops_at_limit(
    client: Any,
) -> None:
    """不再传分页条数；攒够补发上限后不再翻页。"""

    created = client(
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

    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test", Cursor("old", 1))

    assert len(works) == 5
    assert len(created.requests) == 1
    assert "count=" not in str(created.requests[0].url)
