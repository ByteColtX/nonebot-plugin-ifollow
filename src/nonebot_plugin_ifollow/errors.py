"""统一的错误模型。

平台模块只负责把各自的响应映射成这三类错误，轮询层据此决定退避、停用作者还是
放弃作品——不再有「设置了 retryable 却没人读」的情况。
"""

from __future__ import annotations


class IfollowError(Exception):
    """插件内所有预期错误的基类。"""


class TransientError(IfollowError):
    """可重试：网络抖动、限流、风控、临时 5xx。"""


class PermanentError(IfollowError):
    """重试无意义：作者不存在、作品已删除、平台不支持。"""
