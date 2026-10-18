"""平台无关的内容模型与适配器契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from .settings import Settings

Platform = Literal["douyin", "bilibili"]
FeedKind = Literal["video", "dynamic"]


@dataclass(frozen=True, slots=True)
class Cursor:
    """一个 feed 已经发送到哪一件作品。

    ``(time, work_id)`` 构成全序：判断「比游标新」只需一次元组比较，不需要
    「游标作品已被删除」「时间为 0」之类的特例。

    Attributes:
        work_id: 最后一件成功处理的作品标识。
        time: 该作品的发布时间。
    """

    work_id: str
    time: int = 0

    @property
    def order(self) -> tuple[int, str]:
        """返回用于比较新旧的全序键。"""

        return (self.time, self.work_id)


def is_newer(work: PlatformWork, cursor: Cursor) -> bool:
    """判断作品是否在游标之后。

    先按 ``work_id`` 命中——即使作品时间被平台改写也不会重复推送；否则按
    ``(发布时间, 作品 ID)`` 全序比较。
    """

    if work.work_id == cursor.work_id:
        return False
    return work.order > cursor.order


@dataclass(frozen=True, slots=True)
class AuthorInfo:
    """平台作者资料。"""

    author_id: str
    name: str
    handle: str = ""
    avatar: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class PlatformWork:
    """可发送的统一作品或动态。"""

    platform: Platform
    work_id: str
    url: str
    title: str
    description: str
    create_time: int
    author: AuthorInfo
    kind: FeedKind = "video"
    bvid: str = ""

    @property
    def identity(self) -> str:
        """返回平台内唯一内容标识，视频与转发它的动态共享同一个值。"""

        return self.bvid or self.work_id

    @property
    def order(self) -> tuple[int, str]:
        """返回用于比较新旧的全序键。"""

        return (self.create_time, self.work_id)


@dataclass(frozen=True, slots=True)
class ResolvedAuthor:
    """订阅入口解析出的作者与首页内容。"""

    author: AuthorInfo
    feeds: dict[FeedKind, list[PlatformWork]] = field(default_factory=dict)


class PlatformAdapter(Protocol):
    """平台适配器契约；每个平台模块直接实现本协议。"""

    platform: Platform

    def matches(self, value: str) -> bool:
        """判断用户输入是否属于本平台。"""

        ...

    def feeds(self, settings: Settings) -> tuple[FeedKind, ...]:
        """返回本平台要轮询的 feed。"""

        ...

    async def resolve(self, settings: Settings, value: str) -> ResolvedAuthor:
        """解析作者链接，并顺带返回首页内容用于初始化游标。"""

        ...

    async def fetch(
        self,
        settings: Settings,
        author_id: str,
        feed: FeedKind,
        cursor: Cursor | None,
    ) -> list[PlatformWork]:
        """按游标拉取一个 feed 的作品，从新到旧排列。"""

        ...
