"""多平台订阅使用的通用内容模型。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

ContentKind = Literal["video", "dynamic"]

MAX_CATCHUP_PAGES = 5
"""一次补发最多翻页数，避免停机过久时无限追赶。"""


@dataclass(frozen=True)
class Cursor:
    """一个 feed 已经发送到哪一件作品。

    Attributes:
        work_id: 最后一件成功处理的作品标识。
        time: 该作品的发布时间，``0`` 表示未知（v1 数据迁移而来）。
    """

    work_id: str
    time: int = 0


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


def page_reaches_cursor(works: Sequence[PlatformWork], cursor: Cursor) -> bool:
    """判断这一页是否已经盖到游标，可以停止翻页。

    Args:
        works: 本页解析出的作品。
        cursor: 目标当前的游标。

    Returns:
        本页包含游标作品，或包含不新于游标时间的作品时返回 ``True``。
    """

    if any(work.work_id == cursor.work_id for work in works):
        return True
    if cursor.time:
        return any(
            work.create_time and work.create_time <= cursor.time for work in works
        )
    return False
