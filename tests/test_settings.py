from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from nonebot_plugin_ifollow import settings as settings_module
from nonebot_plugin_ifollow.settings import Config, Settings


@pytest.fixture
def cookie_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ParseHub 默认 Cookie 路径指向临时文件。"""

    path = tmp_path / "cookies.toml"
    monkeypatch.setattr("parsehub.cli_config.default_cookie_path", lambda: path)
    return path


@pytest.fixture
def load(monkeypatch: pytest.MonkeyPatch) -> Any:
    """按指定配置项构造 Settings，不依赖 nonebot 全局配置。"""

    def factory(**kwargs: Any) -> Settings:
        monkeypatch.setattr(
            settings_module, "get_plugin_config", lambda _: Config(**kwargs)
        )
        return Settings.load()

    return factory


def test_explicit_cookie_wins(cookie_path: Path, load: Any) -> None:
    cookie_path.write_text(
        '[cookie]\ndouyin = "file-douyin"\nbilibili = "file-bilibili"\n',
        encoding="utf-8",
    )

    runtime = load(
        ifollow_douyin_cookie="env-douyin",
        ifollow_bilibili_cookie="env-bilibili",
    )

    assert runtime.douyin_cookie == "env-douyin"
    assert runtime.bilibili_cookie == "env-bilibili"


def test_falls_back_to_parsehub_cookie(cookie_path: Path, load: Any) -> None:
    cookie_path.write_text(
        '[cookie]\ndouyin = "file-douyin"\nbilibili = "file-bilibili"\n',
        encoding="utf-8",
    )

    runtime = load()

    assert runtime.douyin_cookie == "file-douyin"
    assert runtime.bilibili_cookie == "file-bilibili"
    assert runtime.dynamics_enabled


def test_empty_explicit_cookie_falls_back(cookie_path: Path, load: Any) -> None:
    cookie_path.write_text('[cookie]\ndouyin = "file-douyin"\n', encoding="utf-8")

    runtime = load(ifollow_douyin_cookie="")

    assert runtime.douyin_cookie == "file-douyin"


def test_missing_cookie_file_is_ignored(cookie_path: Path, load: Any) -> None:
    assert not cookie_path.exists()

    runtime = load()

    assert runtime.douyin_cookie is None
    assert runtime.bilibili_cookie is None
    assert not runtime.dynamics_enabled


def test_malformed_cookie_file_is_ignored(cookie_path: Path, load: Any) -> None:
    cookie_path.write_text("[cookie", encoding="utf-8")

    runtime = load()

    assert runtime.douyin_cookie is None
    assert runtime.bilibili_cookie is None
