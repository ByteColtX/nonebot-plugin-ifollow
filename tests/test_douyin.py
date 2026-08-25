from typing import Any

import pytest

from nonebot_plugin_ifollow import douyin_platform as douyin


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
    status_code = 200
    text = ""

    def raise_for_status(self) -> None:
        return

    def json(self) -> dict[str, Any]:
        return {
            "status_code": 0,
            "aweme_list": [
                {
                    "aweme_id": "100",
                    "create_time": 20,
                    "desc": "作品",
                    "author": {"nickname": "作者", "unique_id": "handle"},
                }
            ],
        }


class FakeClient:
    def __init__(self, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return

    async def get(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        return FakeResponse()


@pytest.mark.asyncio
async def test_fetch_latest_works_maps_to_common_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(douyin.httpx, "AsyncClient", FakeClient)

    async def request_cookies() -> dict[str, str]:
        return {}

    monkeypatch.setattr(douyin, "_request_cookies", request_cookies)
    works = await douyin.fetch_latest_works("MS4wLjABAAAA-test")
    assert works[0].platform == "douyin"
    assert works[0].work_id == "100"
    assert works[0].author.name == "作者"
    assert works[0].author.handle == "handle"
