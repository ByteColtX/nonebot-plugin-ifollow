from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nonebot_plugin_ifollow import bilibili_media, media
from nonebot_plugin_ifollow.platforms import AuthorInfo, PlatformWork
from nonebot_plugin_ifollow.settings import Settings

CREATE_TIME = 1791073726


def write(path: Path, suffix: str = ".png") -> Path:
    target = path.with_suffix(suffix)
    target.write_bytes(b"x" * 8)
    return target


def make_work(
    url: str, *, kind: str = "video", author_name: str = "原神"
) -> PlatformWork:
    return PlatformWork(
        platform="bilibili",
        work_id="1",
        url=url,
        title="",
        description="",
        create_time=CREATE_TIME,
        author=AuthorInfo("123", author_name),
        kind="video" if kind == "video" else "dynamic",
    )


def make_result(
    *,
    suffix: str,
    title: str,
    content: str,
    created: dict[str, Any] | None = None,
    duration: int = 0,
) -> Any:
    class FakeResult:
        def __init__(self) -> None:
            self.title = title
            self.content = content

        async def download(self, path: Path) -> SimpleNamespace:
            if created is not None:
                created["dir"] = Path(path)
            media_file = write(Path(path) / "work", suffix=suffix)
            return SimpleNamespace(
                media=[
                    SimpleNamespace(
                        path=media_file,
                        duration=duration,
                        width=2560,
                        height=1440,
                    )
                ]
            )

    return FakeResult()


@pytest.mark.asyncio
async def test_bilibili_media_messages_sends_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, Any] = {}

    async def fake_parse(url: str, cookie: str | None) -> Any:
        created["url"] = url
        created["cookie"] = cookie
        return make_result(
            suffix=".mp4",
            title="视频标题",
            content="视频简介",
            created=created,
            duration=450,
        )

    monkeypatch.setattr(media, "parse_work", fake_parse)

    url = "https://www.bilibili.com/video/BV1AbcdefG12"
    settings = Settings(
        interval=1800,
        max_items=5,
        media_max_bytes=100 * 1024 * 1024,
        use_base64=False,
        bilibili_cookie="cookie=x",
    )
    async with bilibili_media.bilibili_media_messages(
        make_work(url), settings
    ) as messages:
        assert created["dir"].is_dir()
        caption = messages[0].extract_plain_text()
        assert caption.startswith(
            f"B站 · 原神 更新了\n{media.SEPARATOR}\n视频标题\n视频简介\n\n时长 07:30"
        )
        assert [segment.type for segment in messages[1]] == ["video"]

    assert not created["dir"].exists()
    assert created["url"] == url
    assert created["cookie"] == "cookie=x"


@pytest.mark.asyncio
async def test_bilibili_media_messages_falls_back_to_subscription_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作品没带作者名时（联合创作资料拉取失败），文案用订阅里的名字。"""

    async def fake_parse(_url: str, _cookie: str | None) -> Any:
        return make_result(suffix=".mp4", title="视频标题", content="")

    monkeypatch.setattr(media, "parse_work", fake_parse)

    url = "https://www.bilibili.com/video/BV1AbcdefG12"
    settings = Settings(
        interval=1800,
        max_items=5,
        media_max_bytes=100 * 1024 * 1024,
        use_base64=False,
    )
    async with bilibili_media.bilibili_media_messages(
        make_work(url, author_name=""), settings, "三角洲行动"
    ) as messages:
        caption = messages[0].extract_plain_text()
        assert caption.startswith("B站 · 三角洲行动 更新了")


@pytest.mark.asyncio
async def test_bilibili_media_messages_sends_dynamic_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, Any] = {}

    async def fake_parse(url: str, cookie: str | None) -> Any:
        created["url"] = url
        created["cookie"] = cookie
        return make_result(suffix=".png", title="", content="动态正文")

    monkeypatch.setattr(media, "parse_work", fake_parse)

    url = "https://t.bilibili.com/1255716547539763206"
    settings = Settings(
        interval=1800,
        max_items=5,
        media_max_bytes=100 * 1024 * 1024,
        use_base64=False,
    )
    async with bilibili_media.bilibili_media_messages(
        make_work(url, kind="dynamic"), settings
    ) as messages:
        caption = messages[0].extract_plain_text()
        assert caption.startswith(f"B站 · 原神 更新了\n{media.SEPARATOR}\n动态正文")
        assert [segment.type for segment in messages[1]] == ["image"]

    assert created["url"] == url
    assert created["cookie"] is None
