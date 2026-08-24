"""订阅数据模型和 JSON 持久化。"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from nonebot import logger
from nonebot_plugin_localstore import get_plugin_data_dir


@dataclass
class TargetSubscription:
    """一个目标会话的订阅状态。"""

    target: dict[str, Any]
    last_aweme_id: str | None = None
    subscriber_ids: list[str] = field(default_factory=list)


@dataclass
class AuthorSubscription:
    """一个抖音作者及其订阅目标。"""

    sec_user_id: str
    author_name: str
    targets: dict[str, TargetSubscription] = field(default_factory=dict)


class SubscriptionStore:
    """以 JSON 文件保存订阅信息。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path / "subscriptions.json" if path else None
        self._lock = asyncio.Lock()
        self._authors: dict[str, AuthorSubscription] = {}
        self._loaded = False

    async def load(self) -> None:
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
            authors = raw.get("authors", {})
            for sec_user_id, item in authors.items():
                targets = {
                    key: TargetSubscription(**target)
                    for key, target in item.get("targets", {}).items()
                }
                self._authors[sec_user_id] = AuthorSubscription(
                    sec_user_id=sec_user_id,
                    author_name=item.get("author_name", sec_user_id),
                    targets=targets,
                )
        except (OSError, TypeError, ValueError, KeyError) as exc:
            logger.warning(f"抖音订阅数据读取失败，将使用空订阅: {exc}")

    async def snapshot(self) -> dict[str, AuthorSubscription]:
        async with self._lock:
            self._load_unlocked()
            return {
                key: AuthorSubscription(
                    value.sec_user_id,
                    value.author_name,
                    {
                        target_key: TargetSubscription(
                            target.target.copy(),
                            target.last_aweme_id,
                            list(target.subscriber_ids),
                        )
                        for target_key, target in value.targets.items()
                    },
                )
                for key, value in self._authors.items()
            }

    async def upsert_target(
        self,
        sec_user_id: str,
        author_name: str,
        target_key: str,
        target: dict[str, Any],
        last_aweme_id: str | None,
    ) -> None:
        async with self._lock:
            self._load_unlocked()
            author = self._authors.setdefault(
                sec_user_id, AuthorSubscription(sec_user_id, author_name)
            )
            author.author_name = author_name or author.author_name
            author.targets[target_key] = TargetSubscription(target, last_aweme_id)
            self._save_unlocked()

    async def add_target(
        self,
        sec_user_id: str,
        author_name: str,
        target_key: str,
        target: dict[str, Any],
        last_aweme_id: str | None,
        subscriber_id: str | None = None,
    ) -> bool:
        """新增订阅目标；目标已存在时不覆盖并返回 False。"""

        async with self._lock:
            self._load_unlocked()
            author = self._authors.get(sec_user_id)
            if author and target_key in author.targets:
                return False
            author = self._authors.setdefault(
                sec_user_id, AuthorSubscription(sec_user_id, author_name)
            )
            author.author_name = author_name or author.author_name
            subscriber_ids = [subscriber_id] if subscriber_id else []
            author.targets[target_key] = TargetSubscription(
                target, last_aweme_id, subscriber_ids
            )
            self._save_unlocked()
            return True

    async def add_subscriber(
        self, sec_user_id: str, target_key: str, subscriber_id: str
    ) -> bool:
        """为已有订阅新增订阅人，返回是否实际新增。"""

        if not subscriber_id:
            return False
        async with self._lock:
            self._load_unlocked()
            author = self._authors.get(sec_user_id)
            target = author.targets.get(target_key) if author else None
            if target is None or subscriber_id in target.subscriber_ids:
                return False
            target.subscriber_ids.append(subscriber_id)
            self._save_unlocked()
            return True

    async def remove_subscriber(
        self, sec_user_id: str, target_key: str, subscriber_id: str
    ) -> tuple[bool, bool]:
        """移除一个订阅人，返回（是否移除、是否删除整个目标）。"""

        async with self._lock:
            self._load_unlocked()
            author = self._authors.get(sec_user_id)
            if author is None:
                return False, False
            target = author.targets.get(target_key)
            if target is None:
                return False, False
            if not target.subscriber_ids:
                del author.targets[target_key]
                if not author.targets:
                    del self._authors[sec_user_id]
                self._save_unlocked()
                return True, True
            if subscriber_id not in target.subscriber_ids:
                return False, False
            target.subscriber_ids.remove(subscriber_id)
            if target.subscriber_ids:
                self._save_unlocked()
                return True, False
            del author.targets[target_key]
            if not author.targets:
                del self._authors[sec_user_id]
            self._save_unlocked()
            return True, True

    async def remove_target(self, sec_user_id: str, target_key: str) -> bool:
        async with self._lock:
            self._load_unlocked()
            author = self._authors.get(sec_user_id)
            if not author or target_key not in author.targets:
                return False
            del author.targets[target_key]
            if not author.targets:
                del self._authors[sec_user_id]
            self._save_unlocked()
            return True

    async def update_cursor(
        self, sec_user_id: str, target_key: str, aweme_id: str
    ) -> None:
        async with self._lock:
            self._load_unlocked()
            author = self._authors.get(sec_user_id)
            if author and (target := author.targets.get(target_key)):
                target.last_aweme_id = aweme_id
                self._save_unlocked()

    def _save_unlocked(self) -> None:
        path = self._get_path()
        payload = {
            "version": 1,
            "authors": {key: asdict(value) for key, value in self._authors.items()},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as temp:
            json.dump(payload, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
            temp_path = Path(temp.name)
        os.replace(temp_path, path)

    def _get_path(self) -> Path:
        if self.path is None:
            self.path = get_plugin_data_dir() / "subscriptions.json"
        return self.path
