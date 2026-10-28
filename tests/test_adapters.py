from typing import Any

import pytest

from nonebot_plugin_ifollow import adapters
from nonebot_plugin_ifollow.platforms import ResolvedAuthor

PROTOCOL_MEMBERS = ("platform", "matches", "feeds", "resolve", "fetch")
"""``PlatformAdapter`` 的成员；``cast()`` 注册不校验，缺成员只在运行时暴露。"""


@pytest.mark.parametrize(
    "adapter", adapters.ADAPTERS, ids=lambda module: module.platform
)
def test_adapters_implement_protocol(adapter: Any) -> None:
    """适配器必须真正实现协议，否则命令层会抛 ``AttributeError``。"""

    missing = [name for name in PROTOCOL_MEMBERS if not hasattr(adapter, name)]
    assert not missing, f"{adapter.__name__} 缺 {missing}"


def test_adapter_for_known_platforms() -> None:
    assert adapters.adapter_for("douyin").platform == "douyin"
    assert adapters.adapter_for("bilibili").platform == "bilibili"


def test_adapter_for_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError, match="不支持的平台"):
        adapters.adapter_for("kuaishou")  # type: ignore[arg-type]


def test_detect_recognises_both_platforms() -> None:
    assert adapters.detect("https://www.douyin.com/user/MS4wLjABAAAA-x").platform == (
        "douyin"
    )
    assert adapters.detect("MS4wLjABAAAA-x").platform == "douyin"
    assert adapters.detect("https://space.bilibili.com/123").platform == "bilibili"
    assert adapters.detect("123456").platform == "bilibili"


def test_detect_rejects_unknown_input() -> None:
    with pytest.raises(ValueError, match="抖音或 B站"):
        adapters.detect("随便一段文字")


def test_detect_prefers_bilibili_for_bv_links() -> None:
    assert adapters.detect("https://www.bilibili.com/video/BV1AbcdefG12").platform == (
        "bilibili"
    )


def test_resolved_author_defaults_to_empty_feeds() -> None:
    from nonebot_plugin_ifollow.platforms import AuthorInfo

    resolved = ResolvedAuthor(AuthorInfo("1", "作者"))

    assert resolved.feeds == {}
