"""抖音作品解析器扩展。

本模块使用 ``nonebot-plugin-parser`` 的扩展 API 获取并构造作品媒体，
避免直接依赖其内置抖音解析器对 ``ParseResult.video`` 的限制。
"""

from __future__ import annotations

import json
from re import Match
from typing import Any, ClassVar

import httpx
from nonebot import require

require("nonebot_plugin_parser")

from nonebot_plugin_parser.parsers import BaseParser, Platform, handle
from nonebot_plugin_parser.parsers.douyin.aweme import decoder

from .douyin_platform import _request_cookies


class DouyinSubscribeParser(BaseParser):
    """使用抖音作品详情接口的项目专用解析器。"""

    platform: ClassVar[Platform] = Platform(name="douyin", display_name="抖音")

    def __init__(self) -> None:
        """初始化解析器并设置抖音网页请求头。"""

        super().__init__()
        self.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://open.douyin.com",
                "Referer": "https://open.douyin.com/",
            }
        )

    @handle("v.douyin", r"v\.douyin\.com/[a-zA-Z0-9_\-]+")
    @handle("jx.douyin", r"jx\.douyin\.com/[a-zA-Z0-9_\-]+")
    async def _parse_short_link(self, searched: Match[str]):
        """跟随抖音短链，并交给作品链接处理器。"""

        url = f"https://{searched.group(0)}"
        return await self.parse_with_redirect(url, headers=self.headers)

    @handle("douyin.com", r"douyin\.com/[a-z]+/(?P<aweme_id>\d+)")
    @handle("iesdouyin.com", r"iesdouyin\.com/share/[a-z]+/(?P<aweme_id>\d+)")
    @handle("m.douyin.com", r"m\.douyin\.com/share/[a-z]+/(?P<aweme_id>\d+)")
    @handle(
        "jingxuan.douyin.com",
        r"jingxuan\.douyin\.com/m/[a-z]+/(?P<aweme_id>\d+)",
    )
    async def _parse_work(self, searched: Match[str]):
        """解析抖音作品链接。"""

        return await self.parse_aweme(searched.group("aweme_id"))

    async def parse_aweme(self, aweme_id: str):
        """获取作品详情并构造包含全部媒体的标准解析结果。"""

        aweme = await self._fetch_aweme(aweme_id)
        return self._build_result(aweme)

    async def _fetch_aweme(self, aweme_id: str) -> Any:
        """从抖音作品详情接口获取解码后的作品对象。"""

        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=self.timeout,
            follow_redirects=True,
            verify=False,
        ) as client:
            response = await client.get(
                "https://www.douyin.com/aweme/v1/web/aweme/detail/",
                params={"aweme_id": aweme_id, "aid": "6383"},
                cookies=await _request_cookies(),
            )
            response.raise_for_status()
            return decoder.decode(response.content).aweme_detail

    def _build_result(self, aweme: Any):
        """将抖音作品对象转换成 parser 的标准结果。"""

        author = self.create_author(
            aweme.author.nickname,
            aweme.author.avatar_thumb.url_list[-1],
            aweme.author.signature,
        )
        contents: list[Any] = []

        if aweme.images:
            contents.extend(self._build_image_contents(aweme.images))

        if aweme.video:
            video = aweme.video
            cover_url = (
                video.cover_original_scale.url_list[-1]
                if video.cover_original_scale
                else video.cover.url_list[-1]
            )
            contents.append(
                self.create_video(
                    video.play_addr.url,
                    cover_url,
                    video.duration / 1000,
                )
            )

        if aweme.music and not aweme.music.is_original_sound:
            music_url = self._music_url(aweme.music)
            if music_url:
                contents.append(
                    self.create_audio(music_url, aweme.music.duration / 1000)
                )

        share_info = aweme.share_info
        return self.result(
            text=share_info.text,
            author=author,
            timestamp=aweme.create_time,
            url=aweme.share_url.split("?", 1)[0],
            contents=contents,
        )

    def _build_image_contents(self, images: list[Any]) -> list[Any]:
        """构造图集或 Live Photo 的图片内容。"""

        contents: list[Any] = []
        for image in images:
            if image.clip_type == 2 or image.clip_type is None:
                contents.append(self.create_image(image.url_list[-1]))
            elif image.video:
                contents.append(self.create_image(image.video.cover.url_list[-1]))
                contents.append(self.create_video(image.video.play_addr.url))
        return contents

    @staticmethod
    def _music_url(music: Any) -> str | None:
        """从音乐对象中提取可下载的音频地址。"""

        if music.play_url.uri:
            return music.play_url.uri
        try:
            extra = json.loads(music.extra)
        except (TypeError, ValueError):
            return None
        url = extra.get("original_song_url")
        return str(url) if url else None
