from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nonebot_plugin_ifollow import douyin_media


def make_file(path: Path, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(path=path, **extra)


def write(path: Path, size: int = 8, suffix: str = ".png") -> Path:
    target = path.with_suffix(suffix)
    target.write_bytes(b"x" * size)
    return target


def test_caption_keeps_title_and_url() -> None:
    result = SimpleNamespace(title="作品标题", content="")

    assert (
        douyin_media._caption(result, "https://www.douyin.com/video/1")
        == "作品标题\nhttps://www.douyin.com/video/1"
    )


def test_caption_deduplicates_repeated_text() -> None:
    result = SimpleNamespace(title="同一段文案", content="同一段文案")

    assert (
        douyin_media._caption(result, "https://www.douyin.com/video/1")
        == "同一段文案\nhttps://www.douyin.com/video/1"
    )


def test_media_messages_sends_single_video(tmp_path: Path) -> None:
    video = write(tmp_path / "work", suffix=".mp4")

    messages = douyin_media._media_messages([make_file(video)], 0)

    assert len(messages) == 1
    assert [segment.type for segment in messages[0]] == ["video"]


def test_media_messages_treats_uppercase_suffix_as_video(tmp_path: Path) -> None:
    video = write(tmp_path / "work", suffix=".MP4")

    messages = douyin_media._media_messages([make_file(video)], 0)

    assert [segment.type for segment in messages[0]] == ["video"]


def test_media_messages_batches_images(tmp_path: Path) -> None:
    files = [
        make_file(write(tmp_path / f"img{index}"))
        for index in range(douyin_media.IMAGE_BATCH_SIZE + 1)
    ]

    messages = douyin_media._media_messages(files, 0)

    assert [len(message) for message in messages] == [
        douyin_media.IMAGE_BATCH_SIZE,
        1,
    ]
    assert all(segment.type == "image" for message in messages for segment in message)


def test_media_messages_caps_total_media(tmp_path: Path) -> None:
    files = [
        make_file(write(tmp_path / f"img{index}"))
        for index in range(douyin_media.MAX_MEDIA_COUNT + 5)
    ]

    messages = douyin_media._media_messages(files, 0)

    assert sum(len(message) for message in messages) == douyin_media.MAX_MEDIA_COUNT


def test_media_messages_skips_oversized_file(tmp_path: Path) -> None:
    big = write(tmp_path / "big", size=64)

    assert douyin_media._media_messages([make_file(big)], max_bytes=8) == []


def test_media_messages_skips_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.png"

    assert douyin_media._media_messages([make_file(missing)], 0) == []


def test_media_messages_sends_live_photo_as_image_and_video(tmp_path: Path) -> None:
    image = write(tmp_path / "live")
    video = write(tmp_path / "live-motion", suffix=".mp4")

    messages = douyin_media._media_messages([make_file(image, video_path=video)], 0)

    assert [segment.type for message in messages for segment in message] == [
        "image",
        "video",
    ]


def test_build_messages_puts_caption_first(tmp_path: Path) -> None:
    image = write(tmp_path / "img")
    result = SimpleNamespace(title="标题", content="")
    download = SimpleNamespace(media=[make_file(image)])

    messages = douyin_media._build_messages(
        result, download, "https://www.douyin.com/video/1"
    )

    assert messages[0].extract_plain_text() == "标题\nhttps://www.douyin.com/video/1"
    assert [segment.type for segment in messages[1]] == ["image"]


@pytest.mark.asyncio
async def test_douyin_media_messages_downloads_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, Path] = {}

    class FakeResult:
        title = "标题"
        content = ""

        async def download(self, path: Path) -> SimpleNamespace:
            created["dir"] = Path(path)
            video = write(Path(path) / "work", suffix=".mp4")
            return SimpleNamespace(media=[make_file(video)])

    async def fake_parse(url: str) -> FakeResult:
        created["url"] = Path(url)
        return FakeResult()

    monkeypatch.setattr(douyin_media, "_parse", fake_parse)

    async with douyin_media.douyin_media_messages(
        "https://www.douyin.com/video/1"
    ) as messages:
        assert created["dir"].is_dir()
        assert (
            messages[0].extract_plain_text() == "标题\nhttps://www.douyin.com/video/1"
        )
        assert [segment.type for segment in messages[1]] == ["video"]

    assert not created["dir"].exists()
