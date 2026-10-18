"""基于 ``httpx.MockTransport`` 的请求替身。

手写 FakeClient 要复刻上下文管理器、响应对象与 JSON 解析，很容易和真实行为脱节
（例如 raise_for_status 的语义、重定向、请求头大小写）。这里只替换传输层：请求
真的经过 httpx，响应按顺序回放，测试里断言的是真实 ``httpx.Request``。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
import pytest


class Stub:
    """按顺序回放预设响应，并记录收到的请求。

    请求数超过预设响应数后重复最后一条，方便「一直失败」「一直成功」这类场景。
    """

    def __init__(self, responses: Sequence[httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[index]


def install(monkeypatch: pytest.MonkeyPatch, stub: Stub) -> Stub:
    """把 ``httpx.AsyncClient`` 换成走 MockTransport 的客户端。"""

    transport = httpx.MockTransport(stub)
    real = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return real(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return stub
