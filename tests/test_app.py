from pathlib import Path

import pytest

from nonebot_plugin_ifollow import app
from nonebot_plugin_ifollow.models import SubscriptionStore
from nonebot_plugin_ifollow.poller import Poller
from nonebot_plugin_ifollow.settings import Settings

SETTINGS = Settings(
    interval=1800,
    max_items=5,
    media_max_bytes=0,
    use_base64=False,
)


def make_runtime(tmp_path: Path, settings: Settings = SETTINGS) -> app.Runtime:
    store = SubscriptionStore(tmp_path)
    return app.Runtime(settings, store, Poller(store, settings))


def test_runtime_requires_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "_runtime", None)

    with pytest.raises(RuntimeError, match="尚未初始化"):
        app.runtime()


@pytest.mark.asyncio
async def test_startup_starts_poller_without_bilibili_authors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = make_runtime(tmp_path)
    started: list[bool] = []
    monkeypatch.setattr(instance.poller, "start", lambda: started.append(True))

    await app.startup(instance)

    assert started == [True]


@pytest.mark.asyncio
async def test_startup_warms_bilibili_before_polling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        interval=1800,
        max_items=5,
        media_max_bytes=0,
        use_base64=False,
        bilibili_cookie="SESSDATA=x",
    )
    instance = make_runtime(tmp_path, settings)
    await instance.store.add_target("bilibili", "1", "作者", "k", {"id": "g"}, {})
    order: list[str] = []

    async def warm_up() -> None:
        order.append("warm")

    monkeypatch.setattr(app, "bilibili_warm_up", warm_up)
    monkeypatch.setattr(instance.poller, "start", lambda: order.append("start"))

    await app.startup(instance)

    assert order == ["warm", "start"]


@pytest.mark.asyncio
async def test_startup_skips_warm_up_without_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = make_runtime(tmp_path)
    await instance.store.add_target("bilibili", "1", "作者", "k", {"id": "g"}, {})
    calls: list[str] = []

    async def warm_up() -> None:
        calls.append("warm")

    monkeypatch.setattr(app, "bilibili_warm_up", warm_up)
    monkeypatch.setattr(instance.poller, "start", lambda: None)

    await app.startup(instance)

    assert calls == []


@pytest.mark.asyncio
async def test_shutdown_stops_poller_and_flushes(tmp_path: Path) -> None:
    instance = make_runtime(tmp_path)
    stopped: list[bool] = []
    flushed: list[bool] = []

    async def stop() -> None:
        stopped.append(True)

    async def flush() -> None:
        flushed.append(True)

    instance.poller.stop = stop  # type: ignore[method-assign]
    instance.store.flush = flush  # type: ignore[method-assign]

    await app.shutdown(instance)

    assert stopped == [True]
    assert flushed == [True]


def test_install_swaps_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = make_runtime(Path("/tmp"))
    monkeypatch.setattr(app, "_runtime", None)

    app.install(instance)

    assert app.runtime() is instance


def test_setup_registers_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.Settings, "load", classmethod(lambda _cls: SETTINGS))
    app.setup()

    assert app.runtime().settings is SETTINGS
