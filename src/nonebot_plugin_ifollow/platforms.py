"""多平台订阅使用的通用内容模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ContentKind = Literal["video", "dynamic"]


@dataclass(frozen=True)
class AuthorInfo:
    """平台作者资料。"""

    author_id: str
    name: str
    handle: str = ""
    avatar: str = ""
    description: str = ""


@dataclass(frozen=True)
class PlatformWork:
    """可发送的统一作品或动态。"""

    platform: str
    work_id: str
    url: str
    title: str
    description: str
    create_time: int
    author: AuthorInfo
    kind: ContentKind = "video"
    bvid: str = ""

    @property
    def identity(self) -> str:
        """返回平台内唯一内容标识。"""

        return self.bvid or self.work_id
