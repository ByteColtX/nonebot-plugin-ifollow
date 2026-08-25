from typing import Any, ClassVar

import pytest

from nonebot_plugin_ifollow import bilibili_platform as bili


def test_extract_bilibili_links() -> None:
    assert bili.extract_bilibili_mid("https://space.bilibili.com/12345") == "12345"
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


@pytest.mark.asyncio
async def test_fetch_profile_and_video_works(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeUser.pages = [
        {
            "media_list": [
                {
                    "id": 100,
                    "bv_id": "BV1AbcdefG12",
                    "title": "标题",
                    "intro": "简介",
                    "cover": "cover",
                    "pubtime": 200,
                    "upper": {"mid": 123, "name": "作者", "face": "face"},
                }
            ],
            "has_more": False,
        }
    ]
    monkeypatch.setattr(bili, "User", FakeUser)
    profile = await bili.fetch_author_profile("123")
    works = await bili.fetch_video_works("123")
    assert profile.name == "作者"
    assert works[0].work_id == "BV1AbcdefG12"
    assert works[0].title == "标题"
    assert works[0].author.author_id == "123"


@pytest.mark.asyncio
async def test_video_pagination_uses_last_aid(monkeypatch: pytest.MonkeyPatch) -> None:
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
    works = await bili.fetch_video_works("123", cursor="BV99")
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
    assert works[1].url.endswith("dynamic-2")


@pytest.mark.asyncio
async def test_bilibili_errors_mark_412_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingUser(FakeUser):
        async def get_media_list(self, **_params: Any) -> dict[str, Any]:
            raise RuntimeError("HTTP 412")

    monkeypatch.setattr(bili, "User", FailingUser)
    with pytest.raises(bili.BilibiliRequestError) as exc_info:
        await bili.fetch_video_works("123")
    assert exc_info.value.retryable
