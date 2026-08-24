from types import SimpleNamespace
from typing import Any, cast

from nonebot_plugin_parser.parsers import AudioContent, VideoContent

from nonebot_plugin_douyin_subscribe.parser import DouyinSubscribeParser


def _url_list(*urls: str) -> SimpleNamespace:
    return SimpleNamespace(url_list=list(urls))


def test_build_result_keeps_video_cover_and_music() -> None:
    parser = DouyinSubscribeParser()
    parser.create_author = lambda *_args: "author"  # type: ignore[method-assign]
    parser.create_video = (  # type: ignore[method-assign]
        lambda url, cover, duration: VideoContent(
            path_task=cast(Any, url),
            cover=cast(Any, cover),
            duration=duration,
        )
    )
    parser.create_audio = (  # type: ignore[method-assign]
        lambda url, duration: AudioContent(path_task=cast(Any, url), duration=duration)
    )

    aweme = SimpleNamespace(
        author=SimpleNamespace(
            nickname="作者",
            avatar_thumb=_url_list("https://example.com/avatar.jpg"),
            signature="签名",
        ),
        images=None,
        video=SimpleNamespace(
            play_addr=SimpleNamespace(url="https://example.com/video.mp4"),
            cover=_url_list("https://example.com/cover.jpg"),
            cover_original_scale=None,
            duration=12345,
        ),
        music=SimpleNamespace(
            is_original_sound=False,
            play_url=SimpleNamespace(uri="https://example.com/audio.mp3"),
            duration=6789,
            extra="{}",
        ),
        share_info=SimpleNamespace(text="作品文案"),
        create_time=123456,
        share_url="https://www.douyin.com/video/123?foo=bar",
    )

    result = parser._build_result(aweme)

    assert len(result.contents) == 2
    assert isinstance(result.contents[0], VideoContent)
    assert isinstance(result.contents[1], AudioContent)
    assert result.contents[0].cover == "https://example.com/cover.jpg"
    assert result.contents[0].duration == 12.345
    assert result.contents[1].duration == 6.789
