from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nonebot_plugin_ifollow import bilibili_media, media


def write(path: Path, suffix: str = ".png") -> Path:
    target = path.with_suffix(suffix)
    target.write_bytes(b"x" * 8)
    return target


def make_result(
    *, suffix: str, title: str, content: str, created: dict[str, Any] | None = None
) -> Any:
    class FakeResult:
        def __init__(self) -> None:
            self.title = title
            self.content = content

        async def download(self, path: Path) -> SimpleNamespace:
            if created is not None:
                created["dir"] = Path(path)
            media_file = write(Path(path) / "work", suffix=suffix)
            return SimpleNamespace(media=[SimpleNamespace(path=media_file)])

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
            suffix=".mp4", title="视频标题", content="视频简介", created=created
        )

    monkeypatch.setattr(media, "parse_work", fake_parse)
    monkeypatch.setattr(bilibili_media.config, "ifollow_bilibili_cookie", "SESSDATA=x")

    url = "https://www.bilibili.com/video/BV1AbcdefG12"
    async with bilibili_media.bilibili_media_messages(url) as messages:
        assert created["dir"].is_dir()
        assert messages[0].extract_plain_text() == f"视频标题\n视频简介\n{url}"
        assert [segment.type for segment in messages[1]] == ["video"]

    assert not created["dir"].exists()
    assert created["url"] == url
    assert created["cookie"] == "SESSDATA=x"


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
    monkeypatch.setattr(bilibili_media.config, "ifollow_bilibili_cookie", None)

    url = "https://t.bilibili.com/1255716547539763206"
    async with bilibili_media.bilibili_media_messages(url) as messages:
        assert messages[0].extract_plain_text() == f"动态正文\n{url}"
        assert [segment.type for segment in messages[1]] == ["image"]

    assert created["url"] == url
    assert created["cookie"] is None
