"""插件装配：配置、存储、轮询与命令的唯一切入点。"""

from __future__ import annotations

from dataclasses import dataclass

from nonebot import get_driver, logger

from .bilibili_platform import warm_up as bilibili_warm_up
from .models import SubscriptionStore
from .poller import Poller
from .settings import Settings


@dataclass(frozen=True, slots=True)
class Runtime:
    """插件运行期依赖。"""

    settings: Settings
    store: SubscriptionStore
    poller: Poller


_runtime: Runtime | None = None


def runtime() -> Runtime:
    """返回已装配的运行期依赖。

    Raises:
        RuntimeError: 插件尚未完成装配。
    """

    if _runtime is None:
        raise RuntimeError("爱播插件尚未初始化")
    return _runtime


def install(instance: Runtime) -> None:
    """安装运行期依赖，供装配层与测试使用。"""

    global _runtime
    _runtime = instance


def setup() -> None:
    """读取配置、装配依赖并注册命令与生命周期钩子。"""

    settings = Settings.load()
    store = SubscriptionStore()
    poller = Poller(store, settings)
    install(Runtime(settings, store, poller))

    from . import commands  # noqa: F401  导入即注册命令

    driver = get_driver()

    @driver.on_startup
    async def _startup() -> None:
        await startup(runtime())

    @driver.on_shutdown
    async def _shutdown() -> None:
        await shutdown(runtime())


async def startup(instance: Runtime) -> None:
    """加载订阅、预热 B 站指纹，然后启动轮询。

    预热必须排在轮询启动之前，否则首轮可能在没有 buvid3 的情况下走网页兜底。
    """

    store = instance.store
    await store.load()
    authors = await store.snapshot()
    target_count = sum(len(author.targets) for author in authors.values())
    logger.info(f"成功加载 {len(authors)} 个爱播作者订阅，覆盖 {target_count} 个会话。")
    has_bilibili = any(author.platform == "bilibili" for author in authors.values())
    if has_bilibili and not instance.settings.dynamics_enabled:
        logger.info("未配置 B 站 Cookie，动态轮询已跳过，仅推送视频")
    if has_bilibili and instance.settings.dynamics_enabled:
        await bilibili_warm_up()
    instance.poller.start()


async def shutdown(instance: Runtime) -> None:
    """停止轮询并把内存里的改动写回磁盘。"""

    await instance.poller.stop()
    await instance.store.flush()
