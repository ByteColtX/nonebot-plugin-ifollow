from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nonebot_plugin_ifollow import douyin_media, media


def write(path: Path, suffix: str = ".mp4") -> Path:
    target = path.with_suffix(suffix)
    target.write_bytes(b"x" * 8)
    return target


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
            return SimpleNamespace(media=[SimpleNamespace(path=video)])

    async def fake_parse(url: str, cookie: str | None) -> FakeResult:
        created["url"] = url
        created["cookie"] = cookie
        return FakeResult()

    monkeypatch.setattr(media, "parse_work", fake_parse)
    monkeypatch.setattr(douyin_media.config, "ifollow_douyin_cookie", "ttwid=x")

    async with douyin_media.douyin_media_messages(
        "https://www.douyin.com/video/1"
    ) as messages:
        assert created["dir"].is_dir()
        assert (
            messages[0].extract_plain_text() == "标题\nhttps://www.douyin.com/video/1"
        )
        assert [segment.type for segment in messages[1]] == ["video"]

    assert not created["dir"].exists()
    assert created["url"] == "https://www.douyin.com/video/1"
    assert created["cookie"] == "ttwid=x"
