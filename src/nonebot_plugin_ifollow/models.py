"""爱播订阅数据模型和 JSON 持久化。"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Literal

from nonebot import logger
from nonebot_plugin_localstore import get_plugin_data_dir

from .platforms import Cursor

Platform = Literal["douyin", "bilibili"]
STORAGE_VERSION = 2
SUPPORTED_VERSIONS = (1, STORAGE_VERSION)
"""可读入的历史版本；读入后统一按 ``STORAGE_VERSION`` 写回。"""

_TARGET_KEY_FIELDS = (
    "id",
    "parent_id",
    "channel",
    "private",
    "adapter",
    "scope",
    "platforms",
)
_TARGET_KEY_DEFAULTS: dict[str, Any] = {
    "id": "",
    "parent_id": "",
    "channel": False,
    "private": False,
    "adapter": "",
    "scope": "",
    "platforms": [],
}


def make_target_key(target: dict[str, Any]) -> str:
    """根据目标身份字段生成稳定键。"""

    identity = {
        key: target.get(key, _TARGET_KEY_DEFAULTS[key])
        if target.get(key) is not None
        else _TARGET_KEY_DEFAULTS[key]
        for key in _TARGET_KEY_FIELDS
    }
    return json.dumps(identity, ensure_ascii=False, sort_keys=True)


def _compact_target(target: dict[str, Any]) -> dict[str, Any]:
    """删除目标序列化中的默认字段。"""

    compact = {"id": target["id"]}
    for key in ("parent_id", "scope", "adapter"):
        value = target.get(key)
        if value:
            compact[key] = value
    for key in ("channel", "private"):
        if target.get(key):
            compact[key] = True
    for key in ("self_id", "platforms", "extra"):
        value = target.get(key)
        if value:
            compact[key] = value
    return compact


@dataclass
class TargetSubscription:
    """一个目标会话的订阅状态。"""

    target: dict[str, Any]
    cursors: dict[str, Cursor] = field(default_factory=dict)
    subscriber_ids: list[str] = field(default_factory=list)


def _load_cursor(value: Any) -> Cursor | None:
    """读取一个 feed 的游标，兼容 v1 的纯字符串格式。

    Args:
        value: v2 的 ``{"id": ..., "time": ...}``，或 v1 的作品标识字符串。

    Returns:
        解析后的游标，内容为空时返回 ``None``。

    Raises:
        TypeError: 游标结构无法识别。
    """

    if value is None:
        return None
    if isinstance(value, str):
        return Cursor(value, 0) if value else None
    if isinstance(value, dict):
        work_id = str(value.get("id") or "")
        if not work_id:
            return None
        return Cursor(work_id, int(value.get("time") or 0))
    raise TypeError("订阅游标格式无效")


def _dump_cursor(cursor: Cursor) -> dict[str, Any]:
    """把游标序列化成 v2 结构。"""

    return {"id": cursor.work_id, "time": cursor.time}


@dataclass
class AuthorSubscription:
    """一个平台作者及其订阅目标。"""

    platform: Platform
    author_id: str
    author_name: str
    targets: dict[str, TargetSubscription] = field(default_factory=dict)
    author_handle: str = ""
    last_update_time: int = 0

    @property
    def key(self) -> str:
        """返回跨平台唯一作者键。"""

        return f"{self.platform}:{self.author_id}"


class SubscriptionStore:
    """以 JSON 文件保存多平台订阅信息。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path / "subscriptions.json" if path else None
        self._lock = asyncio.Lock()
        self._authors: dict[str, AuthorSubscription] = {}
        self._loaded = False
        self._last_saved: str | None = None

    async def load(self) -> None:
        """加载订阅数据。"""

        async with self._lock:
            self._load_unlocked()

    def _load_unlocked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self._get_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(raw, dict)
                or raw.get("version") not in SUPPORTED_VERSIONS
                or not isinstance(raw.get("subscriptions"), list)
            ):
                raise ValueError("不支持的爱播订阅数据结构")
            for item in raw["subscriptions"]:
                self._load_author(item)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            logger.warning(f"爱播订阅数据读取失败，将使用空订阅: {exc}")

    def _load_author(self, item: Any) -> None:
        if not isinstance(item, dict):
            raise TypeError("订阅作者必须是对象")
        platform = item["platform"]
        author_id = str(item["author_id"])
        if platform not in ("douyin", "bilibili") or not author_id:
            raise ValueError("订阅作者的平台或 ID 无效")
        targets: dict[str, TargetSubscription] = {}
        for target_item in item.get("targets", []):
            if not isinstance(target_item, dict):
                raise TypeError("目标订阅必须是对象")
            target = target_item["target"]
            if not isinstance(target, dict):
                raise TypeError("目标信息必须是对象")
            cursors = target_item.get("cursors", {})
            if not isinstance(cursors, dict):
                raise TypeError("订阅游标必须是对象")
            subscribers = target_item.get("subscribers", [])
            if not isinstance(subscribers, list):
                raise TypeError("订阅人必须是数组")
            target_key = make_target_key(target)
            targets[target_key] = TargetSubscription(
                target=target,
                cursors={
                    str(feed): loaded
                    for feed, value in cursors.items()
                    if (loaded := _load_cursor(value)) is not None
                },
                subscriber_ids=[str(value) for value in subscribers],
            )
        author = AuthorSubscription(
            platform=platform,
            author_id=author_id,
            author_name=str(item.get("name") or author_id),
            targets=targets,
            author_handle=str(item.get("handle") or ""),
            last_update_time=int(item.get("updated_at") or 0),
        )
        self._authors[author.key] = author

    async def snapshot(self) -> dict[str, AuthorSubscription]:
        """返回订阅数据的深拷贝快照。"""

        async with self._lock:
            self._load_unlocked()
            return {
                key: AuthorSubscription(
                    platform=value.platform,
                    author_id=value.author_id,
                    author_name=value.author_name,
                    targets={
                        target_key: TargetSubscription(
                            target.target.copy(),
                            dict(target.cursors),
                            list(target.subscriber_ids),
                        )
                        for target_key, target in value.targets.items()
                    },
                    author_handle=value.author_handle,
                    last_update_time=value.last_update_time,
                )
                for key, value in self._authors.items()
            }

    async def upsert_target(
        self,
        platform: Platform,
        author_id: str,
        author_name: str,
        target_key: str,
        target: dict[str, Any],
        cursors: dict[str, Cursor],
        author_handle: str = "",
        last_update_time: int = 0,
    ) -> None:
        """写入一个目标订阅，主要用于测试和恢复状态。"""

        async with self._lock:
            self._load_unlocked()
            author = self._authors.setdefault(
                f"{platform}:{author_id}",
                AuthorSubscription(platform, author_id, author_name),
            )
            author.author_name = author_name or author.author_name
            author.author_handle = author_handle or author.author_handle
            author.last_update_time = max(author.last_update_time, last_update_time)
            author.targets[target_key] = TargetSubscription(target, dict(cursors))
            await self._save_unlocked()

    async def add_target(
        self,
        platform: Platform,
        author_id: str,
        author_name: str,
        target_key: str,
        target: dict[str, Any],
        cursors: dict[str, Cursor],
        subscriber_id: str | None = None,
        author_handle: str = "",
        last_update_time: int = 0,
    ) -> bool:
        """新增订阅目标；目标已存在时不覆盖并返回 False。"""

        async with self._lock:
            self._load_unlocked()
            author_key = f"{platform}:{author_id}"
            author = self._authors.get(author_key)
            if author and target_key in author.targets:
                return False
            author = self._authors.setdefault(
                author_key,
                AuthorSubscription(platform, author_id, author_name),
            )
            author.author_name = author_name or author.author_name
            author.author_handle = author_handle or author.author_handle
            author.last_update_time = max(author.last_update_time, last_update_time)
            author.targets[target_key] = TargetSubscription(
                target,
                dict(cursors),
                [subscriber_id] if subscriber_id else [],
            )
            await self._save_unlocked()
            return True

    async def update_author_metadata(
        self,
        platform: Platform,
        author_id: str,
        author_name: str,
        author_handle: str,
        last_update_time: int,
    ) -> None:
        """更新作者缓存信息；内容未变化时不写盘。"""

        async with self._lock:
            self._load_unlocked()
            author = self._authors.get(f"{platform}:{author_id}")
            if author is None:
                return
            name = author_name or author.author_name
            handle = author_handle or author.author_handle
            updated_at = max(author.last_update_time, last_update_time)
            if (name, handle, updated_at) == (
                author.author_name,
                author.author_handle,
                author.last_update_time,
            ):
                return
            author.author_name = name
            author.author_handle = handle
            author.last_update_time = updated_at
            await self._save_unlocked()

    async def add_subscriber(
        self, platform: Platform, author_id: str, target_key: str, subscriber_id: str
    ) -> bool:
        """为已有订阅新增订阅人。"""

        if not subscriber_id:
            return False
        async with self._lock:
            self._load_unlocked()
            author = self._authors.get(f"{platform}:{author_id}")
            target = author.targets.get(target_key) if author else None
            if target is None or subscriber_id in target.subscriber_ids:
                return False
            target.subscriber_ids.append(subscriber_id)
            await self._save_unlocked()
            return True

    async def remove_subscriber(
        self,
        platform: Platform,
        author_id: str,
        target_key: str,
        subscriber_id: str,
    ) -> tuple[bool, bool]:
        """移除一个订阅人，返回（是否移除、是否删除整个目标）。"""

        async with self._lock:
            self._load_unlocked()
            author = self._authors.get(f"{platform}:{author_id}")
            if author is None:
                return False, False
            target = author.targets.get(target_key)
            if target is None:
                return False, False
            if not target.subscriber_ids:
                del author.targets[target_key]
                if not author.targets:
                    del self._authors[author.key]
                await self._save_unlocked()
                return True, True
            if subscriber_id not in target.subscriber_ids:
                return False, False
            target.subscriber_ids.remove(subscriber_id)
            if target.subscriber_ids:
                await self._save_unlocked()
                return True, False
            del author.targets[target_key]
            if not author.targets:
                del self._authors[author.key]
            await self._save_unlocked()
            return True, True

    async def remove_target(
        self, platform: Platform, author_id: str, target_key: str
    ) -> bool:
        """删除一个目标订阅。"""

        async with self._lock:
            self._load_unlocked()
            author = self._authors.get(f"{platform}:{author_id}")
            if not author or target_key not in author.targets:
                return False
            del author.targets[target_key]
            if not author.targets:
                del self._authors[author.key]
            await self._save_unlocked()
            return True

    async def update_cursor(
        self,
        platform: Platform,
        author_id: str,
        target_key: str,
        feed: str,
        cursor: Cursor,
    ) -> None:
        """更新目标指定 feed 的游标。"""

        async with self._lock:
            self._load_unlocked()
            author = self._authors.get(f"{platform}:{author_id}")
            target = author.targets.get(target_key) if author else None
            if target is not None and target.cursors.get(feed) != cursor:
                target.cursors[feed] = cursor
                await self._save_unlocked()

    async def _save_unlocked(self) -> None:
        """内容有变化时才落盘，写入放到线程里避免阻塞事件循环。"""

        text = self._serialize()
        if text == self._last_saved:
            return
        await asyncio.to_thread(self._write, text)
        self._last_saved = text

    def _serialize(self) -> str:
        payload = {
            "version": STORAGE_VERSION,
            "subscriptions": [
                self._serialize_author(author) for author in self._authors.values()
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def _write(self, text: str) -> None:
        path = self._get_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as temp:
            temp.write(text)
            temp_path = Path(temp.name)
        os.replace(temp_path, path)

    @staticmethod
    def _serialize_author(author: AuthorSubscription) -> dict[str, Any]:
        """将作者模型转换为精简 JSON 结构。"""

        item: dict[str, Any] = {
            "platform": author.platform,
            "author_id": author.author_id,
            "name": author.author_name,
            "targets": [],
        }
        if author.author_handle:
            item["handle"] = author.author_handle
        if author.last_update_time:
            item["updated_at"] = author.last_update_time
        for target in author.targets.values():
            target_item: dict[str, Any] = {"target": _compact_target(target.target)}
            if target.cursors:
                target_item["cursors"] = {
                    feed: _dump_cursor(cursor)
                    for feed, cursor in target.cursors.items()
                }
            if target.subscriber_ids:
                target_item["subscribers"] = target.subscriber_ids
            item["targets"].append(target_item)
        return item

    def _get_path(self) -> Path:
        if self.path is None:
            self.path = get_plugin_data_dir() / "subscriptions.json"
        return self.path
