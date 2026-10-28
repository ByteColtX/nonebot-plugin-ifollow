from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nonebot_plugin_ifollow import media

CREATE_TIME = 1791073726
META = media.WorkMeta(
    author_name="呆呆丸",
    create_time=CREATE_TIME,
    duration_unit="ms",
    platform_name="抖音",
)


def make_file(path: Path, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(path=path, **extra)


def policy(max_bytes: int = 0, *, base64_media: bool = False) -> media.MediaPolicy:
    """构造媒体策略。"""

    return media.MediaPolicy(max_bytes=max_bytes, base64_media=base64_media)


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
        "抖音 · 呆呆丸 发布了新作品\n"
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


def test_caption_reports_skipped_media_before_timestamp() -> None:
    result = SimpleNamespace(title="标题", content="")
    bundle = media.MediaBundle(
        count=1,
        total_bytes=2048,
        skipped=2,
        max_bytes=100 * 1024 * 1024,
    )

    lines = media.caption(META, result, bundle).splitlines()

    assert lines[-2] == "另有 2 个媒体超过 100MB 未发送"
    assert lines[-1] == stamp()


def test_caption_reports_skipped_media_without_detail_line() -> None:
    result = SimpleNamespace(title="", content="")
    bundle = media.MediaBundle(skipped=1, max_bytes=100 * 1024 * 1024)

    lines = media.caption(media.WorkMeta("作者", 0), result, bundle).splitlines()

    assert lines[-1] == "另有 1 个媒体超过 100MB 未发送"


async def test_media_messages_sends_single_video(tmp_path: Path) -> None:
    video = write(tmp_path / "work", size=1049051, suffix=".mp4")

    bundle = await media.media_messages(
        [make_file(video, duration=7534, width=720, height=1280)],
        policy(),
        "抖音",
        "ms",
    )

    assert len(bundle.messages) == 1
    assert [segment.type for segment in bundle.messages[0]] == ["video"]
    assert bundle.video_seconds == 7
    assert (bundle.width, bundle.height) == (720, 1280)
    assert bundle.total_bytes == 1049051


async def test_media_messages_treats_uppercase_suffix_as_video(tmp_path: Path) -> None:
    video = write(tmp_path / "work", suffix=".MP4")

    bundle = await media.media_messages([make_file(video)], policy(), "B站")

    assert [segment.type for segment in bundle.messages[0]] == ["video"]


async def test_media_messages_batches_images(tmp_path: Path) -> None:
    files = [
        make_file(write(tmp_path / f"img{index}"))
        for index in range(media.IMAGE_BATCH_SIZE + 1)
    ]

    bundle = await media.media_messages(files, policy(), "B站")

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

    bundle = await media.media_messages(files, policy(), "B站")

    assert sum(len(message) for message in bundle.messages) == media.MAX_MEDIA_COUNT


async def test_media_messages_skips_oversized_file(tmp_path: Path) -> None:
    big = write(tmp_path / "big", size=64)

    bundle = await media.media_messages([make_file(big)], policy(8), "抖音")

    assert bundle.messages == []
    assert bundle.count == 0
    assert bundle.skipped == 1
    assert bundle.max_bytes == 8


async def test_media_messages_skips_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.png"

    bundle = await media.media_messages([make_file(missing)], policy(), "抖音")

    assert bundle.messages == []


async def test_media_messages_sends_live_photo_as_image_and_video(
    tmp_path: Path,
) -> None:
    image = write(tmp_path / "live")
    video = write(tmp_path / "live-motion", suffix=".mp4")

    bundle = await media.media_messages(
        [make_file(image, video_path=video, duration=3000)], policy(), "抖音", "ms"
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
        result, download, META, label="B站", policy=policy()
    )

    assert (
        messages[0]
        .extract_plain_text()
        .startswith(f"抖音 · 呆呆丸 发布了新作品\n{media.SEPARATOR}\n标题")
    )
    assert [segment.type for segment in messages[1]] == ["image"]


async def test_media_segments_use_local_path_by_default(tmp_path: Path) -> None:
    """默认把本地路径交给协议端，不读字节也不编码。"""

    video = write(tmp_path / "work", suffix=".mp4")
    image = write(tmp_path / "img")

    bundle = await media.media_messages(
        [make_file(video), make_file(image)], policy(), "抖音"
    )

    segments = [segment for message in bundle.messages for segment in message]
    assert segments
    assert all(segment.path for segment in segments)
    assert all(segment.raw is None for segment in segments)
    assert all(segment.url is None for segment in segments)


async def test_media_segments_switch_to_raw_bytes_when_enabled(tmp_path: Path) -> None:
    """跨主机部署时交给导出层编码，不自己拼 base64:// 地址。"""

    video = write(tmp_path / "work", suffix=".mp4")
    image = write(tmp_path / "img")

    bundle = await media.media_messages(
        [make_file(video), make_file(image)], policy(base64_media=True), "抖音"
    )

    segments = [segment for message in bundle.messages for segment in message]
    assert segments
    assert all(segment.path is None for segment in segments)
    assert all(segment.url is None for segment in segments)
    assert all(segment.raw for segment in segments)
    assert [segment.raw for segment in segments] == [
        video.read_bytes(),
        image.read_bytes(),
    ]


class FakeRef:
    """媒体引用替身，只需要探测用到的字段。"""

    def __init__(
        self, url: str, ext: str = "mp4", video_url: str | None = None
    ) -> None:
        self.url = url
        self.ext = ext
        self.video_url = video_url


async def test_oversized_video_is_dropped_before_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """视频体积超限时直接剔除，不再下载。"""

    async def fake_probe(_client: Any, _url: str) -> int:
        return 10**9

    monkeypatch.setattr(media, "_probe_remote_size", fake_probe)
    result = SimpleNamespace(media=[FakeRef("https://cdn/video.mp4")])

    dropped = await media._drop_oversized_media(result, policy(1024), "抖音")

    assert result.media is None
    assert dropped == 1


async def test_unknown_size_keeps_media_for_post_download_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """探测失败或拿不到长度时保留媒体，交给下载后检查。"""

    async def fake_probe(_client: Any, _url: str) -> None:
        return None

    monkeypatch.setattr(media, "_probe_remote_size", fake_probe)
    ref = FakeRef("https://cdn/video.mp4")
    result = SimpleNamespace(media=[ref])

    dropped = await media._drop_oversized_media(result, policy(1024), "抖音")

    assert result.media == [ref]
    assert dropped == 0


async def test_images_are_not_probed(monkeypatch: pytest.MonkeyPatch) -> None:
    """图片不探测，避免图集多打请求。"""

    probed: list[str] = []

    async def fake_probe(_client: Any, url: str) -> int:
        probed.append(url)
        return 10**9

    monkeypatch.setattr(media, "_probe_remote_size", fake_probe)
    ref = FakeRef("https://cdn/image.jpg", ext="jpg")
    result = SimpleNamespace(media=[ref])

    await media._drop_oversized_media(result, policy(1024), "抖音")

    assert probed == []
    assert result.media == [ref]


async def test_live_photo_probes_motion_video(monkeypatch: pytest.MonkeyPatch) -> None:
    """实况照片要探测动图部分，超限则整条剔除。"""

    async def fake_probe(_client: Any, url: str) -> int:
        return 10**9 if url.endswith(".mp4") else 10

    monkeypatch.setattr(media, "_probe_remote_size", fake_probe)
    ref = FakeRef("https://cdn/live.jpg", ext="jpg", video_url="https://cdn/live.mp4")
    result = SimpleNamespace(media=[ref])

    await media._drop_oversized_media(result, policy(1024), "抖音")

    assert result.media is None


async def test_mixed_album_keeps_media_within_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """图集里只剔除超限的那条，其余保持原顺序。"""

    async def fake_probe(_client: Any, url: str) -> int:
        return 10**9 if url.endswith("big.mp4") else 10

    monkeypatch.setattr(media, "_probe_remote_size", fake_probe)
    image = FakeRef("https://cdn/a.jpg", ext="jpg")
    big = FakeRef("https://cdn/big.mp4")
    small = FakeRef("https://cdn/c.mp4")
    result = SimpleNamespace(media=[image, big, small])

    dropped = await media._drop_oversized_media(result, policy(1024), "抖音")

    assert result.media == [image, small]
    assert dropped == 1


async def test_media_messages_for_skips_download_when_everything_is_too_big(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """全部媒体都超限时不下载，只发文案。"""

    media_at_download: list[Any] = []

    class FakeResult:
        title = "标题"
        content = ""

        def __init__(self) -> None:
            self.media = [FakeRef("https://cdn/video.mp4")]

        async def download(self, path: Path) -> SimpleNamespace:
            del path
            media_at_download.append(self.media)
            return SimpleNamespace(media=[])

    async def fake_parse(_url: str, _cookie: str | None) -> FakeResult:
        return FakeResult()

    async def fake_probe(_client: Any, _url: str) -> int:
        return 10**9

    monkeypatch.setattr(media, "parse_work", fake_parse)
    monkeypatch.setattr(media, "_probe_remote_size", fake_probe)

    async with media.media_messages_for(
        META,
        "https://www.douyin.com/video/1",
        policy=policy(1024 * 1024),
        cookie=None,
        label="抖音",
        temp_prefix="ifollow-test-",
    ) as messages:
        text = messages[0].extract_plain_text()
        assert text.startswith("抖音 · 呆呆丸 发布了新作品")
        assert "另有 1 个媒体超过 1MB 未发送" in text

    assert media_at_download == [None]


async def test_policy_limits_count_and_image_batch(tmp_path: Path) -> None:
    """数量上限与图片批次来自策略对象，不是写死的常量。"""

    files = [make_file(write(tmp_path / f"img{index}")) for index in range(5)]
    custom = media.MediaPolicy(max_bytes=0, max_count=3, image_batch=2)

    bundle = await media.media_messages(files, custom, "抖音")

    assert bundle.count == 3
    assert [len(message) for message in bundle.messages] == [2, 1]
