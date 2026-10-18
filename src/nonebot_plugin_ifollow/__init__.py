"""爱播作者更新订阅插件。"""

from nonebot import require
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_localstore")

from .app import setup
from .settings import Config

__plugin_meta__ = PluginMetadata(
    name="爱播作者订阅",
    description="订阅抖音和 B 站作者的新作品及动态并发送",
    usage=(
        "爱播帮助\n爱播订阅 <作者主页或作品链接>\n爱播订阅列表\n"
        "爱播取消订阅 <序号或作者主页或作品链接>\n爱播状态（超级用户）"
    ),
    type="application",
    homepage="https://github.com/ByteColtX/nonebot-plugin-ifollow",
    config=Config,
    supported_adapters={"~onebot.v11"},
    extra={"author": "ByteColtX <umk@live.com>"},
)

setup()

__all__ = ["__plugin_meta__"]
