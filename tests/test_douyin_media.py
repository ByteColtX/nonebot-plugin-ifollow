from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nonebot_plugin_ifollow import douyin_media, media
from nonebot_plugin_ifollow.platforms import AuthorInfo, PlatformWork
from nonebot_plugin_ifollow.settings import Settings

CREATE_TIME = 1791073726


def write(path: Path, suffix: str = ".mp4") -> Path:
    target = path.with_suffix(suffix)
    target.write_bytes(b"x" * 8)
    return target


def make_work(url: str) -> PlatformWork:
    return PlatformWork(
        platform="douyin",
        work_id="7695356981436382393",
        url=url,
        title="",
        description="",
        create_time=CREATE_TIME,
        author=AuthorInfo("MS4wLjABAAAA", "呆呆丸", "yurrt"),
    )


@pytest.mark.asyncio
async def test_douyin_media_messages_downloads_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, Any] = {}

    class FakeResult:
        title = "标题"
        content = ""

        async def download(self, path: Path) -> SimpleNamespace:
            created["dir"] = Path(path)
            video = write(Path(path) / "work")
            return SimpleNamespace(
                media=[
                    SimpleNamespace(path=video, duration=7534, width=720, height=1280)
                ]
            )

    async def fake_parse(url: str, cookie: str | None) -> FakeResult:
        created["url"] = url
        created["cookie"] = cookie
        return FakeResult()

    monkeypatch.setattr(media, "parse_work", fake_parse)

    url = "https://www.douyin.com/video/7695356981436382393"
    settings = Settings(
        interval=1800,
        max_items=5,
        media_max_bytes=100 * 1024 * 1024,
        use_base64=False,
        douyin_cookie="cookie=x",
    )
    async with douyin_media.douyin_media_messages(make_work(url), settings) as messages:
        assert created["dir"].is_dir()
        caption = messages[0].extract_plain_text()
        assert caption.startswith(
            f"抖音 · 呆呆丸 发布了新作品\n{media.SEPARATOR}\n标题\n\n00:07"
        )
        assert "720×1280" in caption
        assert [segment.type for segment in messages[1]] == ["video"]

    assert not created["dir"].exists()
    assert created["url"] == url
    assert created["cookie"] == "cookie=x"
