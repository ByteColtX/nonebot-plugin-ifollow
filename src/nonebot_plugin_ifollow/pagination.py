"""唯一的分页实现：翻到盖住游标或攒够上限为止。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from .platforms import Cursor, PlatformWork, is_newer


@dataclass(frozen=True, slots=True)
class Page:
    """平台接口返回的一页内容。

    Attributes:
        works: 本页作品，从新到旧。
        next_token: 下一页的翻页令牌，``None`` 表示没有下一页。
        has_more: 平台是否声明还有更多内容。
    """

    works: list[PlatformWork] = field(default_factory=list)
    next_token: str | None = None
    has_more: bool = False


PageFetcher = Callable[[str | None], Awaitable[Page]]


def reaches_cursor(works: Sequence[PlatformWork], cursor: Cursor) -> bool:
    """判断本页是否已经盖到游标。"""

    return any(not is_newer(work, cursor) for work in works)


async def paginate(
    fetch_page: PageFetcher, *, cursor: Cursor | None, limit: int
) -> list[PlatformWork]:
    """按页拉取作品，返回从新到旧、按 ``work_id`` 去重的列表。

    无游标时只取一页——首次订阅只关心最新一条；有游标时翻到盖住游标，或攒够
    ``limit`` 条就停，停机再久也不会无限追赶。

    Args:
        fetch_page: 取一页的回调，参数是上一页给出的翻页令牌。
        cursor: 目标当前的游标，``None`` 表示首次订阅。
        limit: 一次最多攒多少条作品。

    Returns:
        从新到旧排列的作品列表。
    """

    works: list[PlatformWork] = []
    seen: set[str] = set()
    token: str | None = None
    while True:
        page = await fetch_page(token)
        fresh = [work for work in page.works if work.work_id not in seen]
        seen.update(work.work_id for work in fresh)
        works.extend(fresh)
        if not fresh or not page.has_more or page.next_token is None:
            break
        if cursor is None or len(works) >= limit:
            break
        if reaches_cursor(fresh, cursor):
            break
        token = page.next_token
    works.sort(key=lambda work: work.order, reverse=True)
    return works
