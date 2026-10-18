import pytest

from nonebot_plugin_ifollow.pagination import Page, paginate, reaches_cursor
from nonebot_plugin_ifollow.platforms import AuthorInfo, Cursor, PlatformWork


def make_work(work_id: str, timestamp: int) -> PlatformWork:
    return PlatformWork(
        platform="bilibili",
        work_id=work_id,
        url=f"https://example.com/{work_id}",
        title=work_id,
        description="",
        create_time=timestamp,
        author=AuthorInfo("123", "作者", "123"),
    )


def test_reaches_cursor_matches_by_id_even_without_time() -> None:
    works = [make_work("a", 10), make_work("b", 20)]

    assert reaches_cursor(works, Cursor("a"))
    assert not reaches_cursor(works, Cursor("c", 5))


@pytest.mark.asyncio
async def test_paginate_without_cursor_takes_one_page() -> None:
    pages = [Page([make_work("b", 20), make_work("a", 10)], "next", True)]

    async def fetch_page(_token: str | None) -> Page:
        return pages[0]

    works = await paginate(fetch_page, cursor=None, limit=5)

    assert [work.work_id for work in works] == ["b", "a"]


@pytest.mark.asyncio
async def test_paginate_stops_at_cursor() -> None:
    pages = {
        None: Page([make_work("c", 30)], "p2", True),
        "p2": Page([make_work("b", 20)], "p3", True),
    }

    async def fetch_page(token: str | None) -> Page:
        return pages[token]

    works = await paginate(fetch_page, cursor=Cursor("b", 20), limit=5)

    assert [work.work_id for work in works] == ["c", "b"]


@pytest.mark.asyncio
async def test_paginate_stops_when_limit_reached() -> None:
    calls: list[str | None] = []

    async def fetch_page(token: str | None) -> Page:
        calls.append(token)
        return Page([make_work("c", 30), make_work("b", 20)], "p2", True)

    works = await paginate(fetch_page, cursor=Cursor("a", 10), limit=2)

    assert [work.work_id for work in works] == ["c", "b"]
    assert calls == [None]


@pytest.mark.asyncio
async def test_paginate_dedupes_and_sorts() -> None:
    pages = {
        None: Page([make_work("c", 30), make_work("b", 20)], "p2", True),
        "p2": Page([make_work("b", 20), make_work("a", 10)], None, False),
    }

    async def fetch_page(token: str | None) -> Page:
        return pages[token]

    works = await paginate(fetch_page, cursor=Cursor("x", 1), limit=10)

    assert [work.work_id for work in works] == ["c", "b", "a"]


@pytest.mark.asyncio
async def test_paginate_stops_when_platform_reports_no_more() -> None:
    async def fetch_page(_token: str | None) -> Page:
        return Page([make_work("a", 10)], None, False)

    works = await paginate(fetch_page, cursor=Cursor("x", 1), limit=10)

    assert [work.work_id for work in works] == ["a"]
