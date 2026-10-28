"""爱播作者更新订阅插件。"""

from nonebot import require
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_alconna")
require("nonebot_plugin_localstore")

from .app import setup
from .formatting import usage
from .settings import Config

__plugin_meta__ = PluginMetadata(
    name="爱播作者订阅",
    description="订阅抖音和 B站作者的新作品及动态并发送",
    usage=usage(),
    type="application",
    homepage="https://github.com/ByteColtX/nonebot-plugin-ifollow",
    config=Config,
    supported_adapters={"~onebot.v11"},
    extra={"author": "ByteColtX <umk@live.com>"},
)

setup()

__all__ = ["__plugin_meta__"]
