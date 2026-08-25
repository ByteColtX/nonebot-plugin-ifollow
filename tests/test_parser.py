from types import SimpleNamespace

from nonebot_plugin_ifollow.parser import DouyinSubscribeParser


def make_music(*, uri: str = "", extra: str = "") -> SimpleNamespace:
    return SimpleNamespace(play_url=SimpleNamespace(uri=uri), extra=extra)


def test_music_url_prefers_play_url() -> None:
    music = make_music(uri="https://example.com/play.mp3", extra="invalid")

    assert DouyinSubscribeParser._music_url(music) == "https://example.com/play.mp3"


def test_music_url_reads_original_song_url_from_extra() -> None:
    music = make_music(extra='{"original_song_url": "https://example.com/song.mp3"}')

    assert DouyinSubscribeParser._music_url(music) == "https://example.com/song.mp3"


def test_music_url_returns_none_for_invalid_or_missing_url() -> None:
    invalid_json = make_music(extra="invalid")
    missing_url = make_music(extra='{"original_song_url": ""}')

    assert DouyinSubscribeParser._music_url(invalid_json) is None
    assert DouyinSubscribeParser._music_url(missing_url) is None
