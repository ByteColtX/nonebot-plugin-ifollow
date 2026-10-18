"""把旧订阅数据里每个作者的游标推进到当前最新作品。

用法：uv run python tools/advance_cursors.py <旧 subscriptions.json> <输出路径>

直接读写 JSON，不经过 SubscriptionStore，避免依赖 localstore 的数据目录。
抖音在限流时会返回空列表，所以空结果会重试几次，仍为空则保留旧游标并报告。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import nonebot

nonebot.init()

from nonebot_plugin_ifollow import bilibili_platform, douyin_platform

RETRIES = 3
RETRY_DELAY_SECONDS = 5.0


async def _latest_cursors(platform: str, author_id: str) -> dict[str, str]:
    if platform == "douyin":
        works = await douyin_platform.fetch_latest_works(author_id)
        return {"video": works[0].work_id} if works else {}
    cursors: dict[str, str] = {}
    videos = await bilibili_platform.fetch_video_works(author_id)
    if videos:
        cursors["video"] = videos[0].work_id
    dynamics = await bilibili_platform.fetch_dynamic_works(author_id, None)
    if dynamics:
        cursors["dynamic"] = dynamics[0].work_id
    return cursors


async def _latest_cursors_with_retry(platform: str, author_id: str) -> dict[str, str]:
    for attempt in range(1, RETRIES + 1):
        latest = await _latest_cursors(platform, author_id)
        if latest:
            return latest
        if attempt < RETRIES:
            print(f"     empty, retry {attempt}/{RETRIES - 1}")
            await asyncio.sleep(RETRY_DELAY_SECONDS * attempt)
    return {}


async def main(source: Path, target_path: Path) -> int:
    data: dict[str, Any] = json.loads(
        await asyncio.to_thread(source.read_text, "utf-8")
    )
    failures: list[str] = []
    now = int(time.time())

    for item in data["subscriptions"]:
        platform = item["platform"]
        author_id = str(item["author_id"])
        name = item.get("name", author_id)
        try:
            latest = await _latest_cursors_with_retry(platform, author_id)
        except Exception as exc:  # 逐个作者隔离失败
            failures.append(f"{platform}:{name}: {exc}")
            print(f"FAIL {platform} {name}: {exc}")
            continue
        if not latest:
            failures.append(f"{platform}:{name}: 连续返回空列表")
            print(f"EMPTY {platform} {name}: 保留旧游标")
            continue

        changed = 0
        for target_state in item.get("targets", []):
            cursors = target_state.setdefault("cursors", {})
            for feed, value in latest.items():
                if feed in cursors and cursors[feed] != value:
                    cursors[feed] = value
                    changed += 1
        item["updated_at"] = now
        print(f"OK   {platform} {name}: {latest} (更新 {changed} 个游标)")
        await asyncio.sleep(1.0)

    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    await asyncio.to_thread(target_path.write_text, payload, "utf-8")
    print(f"\n写入 {target_path}，失败 {len(failures)} 个")
    for failure in failures:
        print("  !", failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(Path(sys.argv[1]), Path(sys.argv[2]))))
