"""订阅数据模型与 JSON 持久化。

写入策略：内存里的改动只标脏，由调用方在合适边界调用 :meth:`flush`（投递结束、
命令处理结束）批量落盘，一次投递只产生一次磁盘写。读写都放进线程，事件循环不被
阻塞。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from nonebot import logger
from nonebot_plugin_localstore import get_plugin_data_dir

from .platforms import Cursor, Platform

STORAGE_VERSION = 3

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
        key: (
            target.get(key, _TARGET_KEY_DEFAULTS[key])
            if target.get(key) is not None
            else _TARGET_KEY_DEFAULTS[key]
        )
        for key in _TARGET_KEY_FIELDS
    }
    return json.dumps(identity, ensure_ascii=False, sort_keys=True)


def _compact_target(target: dict[str, Any]) -> dict[str, Any]:
    """删除目标序列化中的默认字段。"""

    compact: dict[str, Any] = {"id": target["id"]}
    for key in ("parent_id", "scope", "adapter"):
        if value := target.get(key):
            compact[key] = value
    for key in ("channel", "private"):
        if target.get(key):
            compact[key] = True
    for key in ("self_id", "platforms", "extra"):
        if value := target.get(key):
            compact[key] = value
    return compact


@dataclass
class TargetSubscription:
    """一个目标会话的订阅状态。"""

    target: dict[str, Any]
    cursors: dict[str, Cursor] = field(default_factory=dict)
    subscriber_ids: list[str] = field(default_factory=list)


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
        self._dirty = False

    async def load(self) -> None:
        """加载订阅数据；结构不认识时回退为空订阅。"""

        async with self._lock:
            await self._ensure_loaded()

    async def _ensure_loaded(self) -> None:
        """在已持有锁的前提下按需加载，避免重入死锁。"""

        if self._loaded:
            return
        self._loaded = True
        path = self._get_path()
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as exc:
            logger.warning(f"爱播订阅数据读取失败，将使用空订阅：{exc}")
            return
        try:
            self._load_json(text)
        except (TypeError, ValueError, KeyError) as exc:
            self._authors.clear()
            logger.warning(f"爱播订阅数据结构无效，将使用空订阅：{exc}")

    def _load_json(self, text: str) -> None:
        raw = json.loads(text)
        if not isinstance(raw, dict) or raw.get("version") != STORAGE_VERSION:
            raise ValueError(f"不支持的爱播订阅数据版本：{raw.get('version')!r}")
        subscriptions = raw.get("subscriptions")
        if not isinstance(subscriptions, list):
            raise TypeError("订阅列表必须是数组")
        for item in subscriptions:
            self._load_author(item)

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
                    str(feed): Cursor(str(value["id"]), int(value.get("time") or 0))
                    for feed, value in cursors.items()
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
            await self._ensure_loaded()
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
        """新增订阅目标；目标已存在时返回 ``False``。"""

        async with self._lock:
            await self._ensure_loaded()
            author = self._authors.get(f"{platform}:{author_id}")
            if author and target_key in author.targets:
                return False
            author = self._authors.setdefault(
                f"{platform}:{author_id}",
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
            self._dirty = True
            return True

    async def add_subscriber(
        self, platform: Platform, author_id: str, target_key: str, subscriber_id: str
    ) -> bool:
        """为已有订阅新增订阅人。"""

        if not subscriber_id:
            return False
        async with self._lock:
            await self._ensure_loaded()
            author = self._authors.get(f"{platform}:{author_id}")
            target = author.targets.get(target_key) if author else None
            if target is None or subscriber_id in target.subscriber_ids:
                return False
            target.subscriber_ids.append(subscriber_id)
            self._dirty = True
            return True

    async def remove_subscriber(
        self,
        platform: Platform,
        author_id: str,
        target_key: str,
        subscriber_id: str,
        *,
        force: bool = False,
    ) -> tuple[bool, bool]:
        """移除订阅人，返回（是否移除、是否删除整个目标）。

        Args:
            platform: 平台。
            author_id: 作者 ID。
            target_key: 目标会话键。
            subscriber_id: 发起人。
            force: 管理员路径：不要求自己是订阅人，直接删掉整个目标；
                订阅人列表为空的老数据也只有这条路径能删。

        Returns:
            ``(是否移除, 是否删除整个目标)``。
        """

        async with self._lock:
            await self._ensure_loaded()
            author = self._authors.get(f"{platform}:{author_id}")
            if author is None:
                return False, False
            target = author.targets.get(target_key)
            if target is None:
                return False, False
            if force:
                del author.targets[target_key]
                if not author.targets:
                    del self._authors[author.key]
                self._dirty = True
                return True, True
            # 老数据没有订阅人列表：普通群员不能删，否则谁都能删别人的订阅
            if not target.subscriber_ids:
                return False, False
            if subscriber_id not in target.subscriber_ids:
                return False, False
            target.subscriber_ids.remove(subscriber_id)
            removed_target = not target.subscriber_ids
            if removed_target:
                del author.targets[target_key]
                if not author.targets:
                    del self._authors[author.key]
            self._dirty = True
            return True, removed_target

    async def update_cursor(
        self,
        platform: Platform,
        author_id: str,
        target_key: str,
        feed: str,
        cursor: Cursor,
    ) -> None:
        """更新目标指定 feed 的游标；只标脏，由 :meth:`flush` 落盘。"""

        async with self._lock:
            await self._ensure_loaded()
            author = self._authors.get(f"{platform}:{author_id}")
            target = author.targets.get(target_key) if author else None
            if target is not None and target.cursors.get(feed) != cursor:
                target.cursors[feed] = cursor
                self._dirty = True

    async def update_author_metadata(
        self,
        platform: Platform,
        author_id: str,
        author_name: str,
        author_handle: str,
        last_update_time: int,
    ) -> None:
        """更新作者缓存信息；内容未变化时不标脏。"""

        async with self._lock:
            await self._ensure_loaded()
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
            self._dirty = True

    async def flush(self) -> None:
        """把内存里的改动一次写入磁盘。"""

        async with self._lock:
            if not self._dirty:
                return
            text = self._serialize()
            await asyncio.to_thread(self._write, text)
            self._dirty = False

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SubscriptionStore]:
        """在边界处批量落盘；异常时也会把已生效的改动写回。

        落盘失败只告警、不向上抛：否则磁盘错误会盖掉业务异常，而且改动仍是脏的，
        下一次 flush 会重试。
        """

        try:
            yield self
        finally:
            try:
                await self.flush()
            except OSError as exc:
                logger.warning(f"爱播订阅数据写入失败，改动保留在内存待重试：{exc}")

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
                    feed: {"id": cursor.work_id, "time": cursor.time}
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
