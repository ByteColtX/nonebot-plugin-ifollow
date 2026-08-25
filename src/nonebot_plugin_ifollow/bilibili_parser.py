"""爱播使用的 B 站 parser 适配。"""

from __future__ import annotations

from bilibili_api import Credential
from nonebot_plugin_parser.parsers.bilibili import BilibiliParser

from .bilibili_platform import _parse_cookie
from .config import config


class IFollowBilibiliParser(BilibiliParser):
    """为 B 站 parser 注入爱播的可选 Cookie。"""

    def __init__(self) -> None:
        super().__init__()
        if config.ifollow_bilibili_cookie:
            self._credential = Credential.from_cookies(
                _parse_cookie(config.ifollow_bilibili_cookie)
            )
