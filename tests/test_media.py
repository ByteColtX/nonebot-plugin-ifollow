from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nonebot_plugin_ifollow import media

CREATE_TIME = 1791073726
META = media.WorkMeta(author_name="呆呆丸", create_time=CREATE_TIME, duration_unit="ms")


def make_file(path: Path, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(path=path, **extra)


def write(path: Path, size: int = 8, suffix: str = ".png") -> Path:
    target = path.with_suffix(suffix)
    target.write_bytes(b"x" * size)
    return target


def stamp() -> str:
    return datetime.fromtimestamp(CREATE_TIME).strftime("%Y-%m-%d %H:%M")


def test_caption_renders_full_block() -> None:
    result = SimpleNamespace(title="作品标题", content="")
    bundle = media.MediaBundle(
        count=1, total_bytes=1049051, width=720, height=1280, video_seconds=7
    )

    assert media.caption(META, result, bundle) == (
        "呆呆丸 发布了新作品\n"
        f"{media.SEPARATOR}\n"
        "作品标题\n"
        "\n"
        "00:07 · 720×1280 · 1.0 MB\n"
        f"{stamp()}"
    )


def test_caption_deduplicates_repeated_text() -> None:
    result = SimpleNamespace(title="同一段文案", content="同一段文案")

    text = media.caption(META, result, media.MediaBundle())

    assert "同一段文案" in text
    assert text.count("同一段文案") == 1


def test_caption_counts_image_album() -> None:
    result = SimpleNamespace(title="九图", content="")
    bundle = media.MediaBundle(count=9, total_bytes=2048, width=1080, height=1440)

    text = media.caption(META, result, bundle)

    assert "9 张 · 1080×1440 · 2 KB" in text


def test_caption_omits_empty_detail_line() -> None:
    result = SimpleNamespace(title="", content="")

    text = media.caption(media.WorkMeta("作者", 0), result, media.MediaBundle())

    assert text == f"作者 发布了新作品\n{media.SEPARATOR}"


async def test_media_messages_sends_single_video(tmp_path: Path) -> None:
    video = write(tmp_path / "work", size=1049051, suffix=".mp4")

    bundle = await media.media_messages(
        [make_file(video, duration=7534, width=720, height=1280)], 0, "抖音", "ms"
    )

    assert len(bundle.messages) == 1
    assert [segment.type for segment in bundle.messages[0]] == ["video"]
    assert bundle.video_seconds == 7
    assert (bundle.width, bundle.height) == (720, 1280)
    assert bundle.total_bytes == 1049051


async def test_media_messages_treats_uppercase_suffix_as_video(tmp_path: Path) -> None:
    video = write(tmp_path / "work", suffix=".MP4")

    bundle = await media.media_messages([make_file(video)], 0, "B站")

    assert [segment.type for segment in bundle.messages[0]] == ["video"]


async def test_media_messages_batches_images(tmp_path: Path) -> None:
    files = [
        make_file(write(tmp_path / f"img{index}"))
        for index in range(media.IMAGE_BATCH_SIZE + 1)
    ]

    bundle = await media.media_messages(files, 0, "B站")

    assert [len(message) for message in bundle.messages] == [
        media.IMAGE_BATCH_SIZE,
        1,
    ]
    assert all(
        segment.type == "image" for message in bundle.messages for segment in message
    )
    assert bundle.count == media.IMAGE_BATCH_SIZE + 1


async def test_media_messages_caps_total_media(tmp_path: Path) -> None:
    files = [
        make_file(write(tmp_path / f"img{index}"))
        for index in range(media.MAX_MEDIA_COUNT + 5)
    ]

    bundle = await media.media_messages(files, 0, "B站")

    assert sum(len(message) for message in bundle.messages) == media.MAX_MEDIA_COUNT


async def test_media_messages_skips_oversized_file(tmp_path: Path) -> None:
    big = write(tmp_path / "big", size=64)

    bundle = await media.media_messages([make_file(big)], max_bytes=8, label="抖音")

    assert bundle.messages == []
    assert bundle.count == 0


async def test_media_messages_skips_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.png"

    bundle = await media.media_messages([make_file(missing)], 0, "抖音")

    assert bundle.messages == []


async def test_media_messages_sends_live_photo_as_image_and_video(
    tmp_path: Path,
) -> None:
    image = write(tmp_path / "live")
    video = write(tmp_path / "live-motion", suffix=".mp4")

    bundle = await media.media_messages(
        [make_file(image, video_path=video, duration=3000)], 0, "抖音", "ms"
    )

    assert [segment.type for message in bundle.messages for segment in message] == [
        "image",
        "video",
    ]
    assert bundle.video_seconds == 3


async def test_build_messages_puts_caption_first(tmp_path: Path) -> None:
    image = write(tmp_path / "img")
    result = SimpleNamespace(title="标题", content="")
    download = SimpleNamespace(media=[make_file(image)])

    messages = await media.build_messages(
        result, download, META, label="B站", max_bytes=0
    )

    assert (
        messages[0]
        .extract_plain_text()
        .startswith(f"呆呆丸 发布了新作品\n{media.SEPARATOR}\n标题")
    )
    assert [segment.type for segment in messages[1]] == ["image"]


async def test_media_segments_are_sent_as_raw_bytes(tmp_path: Path) -> None:
    """协议端在另一台机器，媒体必须以 base64 发送而不是 file:// 路径。"""

    video = write(tmp_path / "work", suffix=".mp4")
    image = write(tmp_path / "img")

    bundle = await media.media_messages([make_file(video), make_file(image)], 0, "抖音")

    segments = [segment for message in bundle.messages for segment in message]
    assert segments
    assert all(segment.raw for segment in segments)
    assert all(segment.path is None for segment in segments)
