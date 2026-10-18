"""从被引用的消息里取出链接。"""

from __future__ import annotations

from typing import Any

from nonebot import logger
from nonebot.adapters import Bot, Event, Message


async def replied_text(event: Event, bot: Bot) -> str:
    """返回被引用消息的文本；没有引用或读取失败时返回空串。"""

    reply = getattr(event, "reply", None)
    if reply is not None:
        text = _message_text(getattr(reply, "message", None))
        if text:
            return text
    reply_id = _reply_id(event.get_message())
    if reply_id is None:
        return ""
    try:
        result = await bot.call_api("get_msg", message_id=reply_id)
    except Exception as exc:
        logger.warning(f"读取引用消息 {reply_id} 失败: {exc}")
        return ""
    if not isinstance(result, dict):
        return ""
    return _message_text(result.get("message"))


def _reply_id(message: Message) -> str | None:
    for segment in message:
        if getattr(segment, "type", None) != "reply":
            continue
        data = getattr(segment, "data", {})
        if isinstance(data, dict):
            message_id = data.get("id") or data.get("message_id")
            if message_id is not None:
                return str(message_id)
    return None


def _message_text(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    if isinstance(message, (list, tuple)):
        parts: list[str] = []
        for segment in message:
            data = segment.get("data") if isinstance(segment, dict) else None
            if not isinstance(data, dict):
                data = getattr(segment, "data", {})
            if isinstance(data, dict) and isinstance(data.get("text"), str):
                parts.append(data["text"])
        return "".join(parts)
    return ""


__all__ = ["replied_text"]
