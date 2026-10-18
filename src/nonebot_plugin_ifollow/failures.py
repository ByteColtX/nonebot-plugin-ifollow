"""失败分类与重试策略。"""

from __future__ import annotations

from .errors import PermanentError

PERMANENT_WORK_ERROR_NAMES = frozenset({"ContentUnavailableError", "UnknownPlatform"})
"""parsehub 中表示「重试也没有意义」的异常类名。

按类名判断而不是导入：导入 parsehub 会连带加载 numpy/opencv，几秒的阻塞不能落在
事件循环上。``ContentUnavailableFallbackError`` 继承自 ``ContentUnavailableError``，
按 MRO 匹配可以一并覆盖。
"""

PERMANENT_TARGET_KEYWORDS = (
    "被移出该群",
    "重新加群",
    "群不存在",
    "不在该群",
    "不在群里",
    "群号不存在",
    "not in group",
    "group not found",
)
"""发送失败信息里表示目标群已不可用的关键字。"""

WORK_RETRY_LIMIT = 3
"""单个作品发往单个目标的暂时性失败次数上限，超过后放弃并推进游标。"""

BACKOFF_BASE_SECONDS = 30.0
BACKOFF_MAX_SECONDS = 300.0


def is_permanent_work_error(exc: BaseException) -> bool:
    """判断作品级失败是否重试也没有意义。"""

    if isinstance(exc, PermanentError):
        return True
    return any(cls.__name__ in PERMANENT_WORK_ERROR_NAMES for cls in type(exc).__mro__)


def is_permanent_target_error(exc: BaseException) -> bool:
    """判断发送失败是否因为目标群已不可用（机器人被踢、群解散）。

    Args:
        exc: 发送时抛出的异常。

    Returns:
        目标群已不可用时返回 ``True``，此时重试没有意义。
    """

    info = getattr(exc, "info", None)
    if isinstance(info, dict):
        parts = [str(info.get(key) or "") for key in ("message", "msg", "wording")]
        text = " ".join(parts) or str(exc)
    else:
        text = str(exc)
    lowered = text.lower()
    return any(keyword in lowered for keyword in PERMANENT_TARGET_KEYWORDS)


def short_reason(exc: BaseException) -> str:
    """取异常信息的第一行并截断，用于日志和状态展示。"""

    text = str(exc).strip().splitlines()
    if not text:
        return type(exc).__name__
    return text[0][:80]


def backoff_seconds(attempt: int) -> float:
    """按连续失败次数计算退避时间。"""

    return min(BACKOFF_BASE_SECONDS * (2 ** max(attempt - 1, 0)), BACKOFF_MAX_SECONDS)
