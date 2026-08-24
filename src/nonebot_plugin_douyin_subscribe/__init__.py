from nonebot import require
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_parser")
require("nonebot_plugin_apscheduler")

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="抖音作者订阅",
    description="订阅抖音作者新作品并通过 nonebot-plugin-parser 下载发送",
    usage=(
        "抖音订阅 <作者主页链接或 sec_user_id>\n"
        "抖音订阅列表\n"
        "抖音取消订阅 <作者主页链接或 sec_user_id>"
    ),
    type="application",
    homepage="https://github.com/ByteColtX/nonebot-plugin-douyin-subscribe",
    config=Config,
    supported_adapters={"~onebot.v11"},
    extra={"author": "ByteColtX <umk@live.com>"},
)

from . import plugin as plugin

__all__ = ["plugin"]
