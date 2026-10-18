import random

import pytest

from nonebot_plugin_ifollow import douyin_abogus as abogus

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BROWSER_FP = "1280|720|1304|795|0|0|0|0|1440|900|1440|875|1280|720|24|24|MacIntel"


def test_generate_abogus_matches_known_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """锁定签名算法输出，算法被无意改动时立刻失败。"""

    monkeypatch.setattr(abogus.time, "time", lambda: 1700000000.0)
    state = random.getstate()
    try:
        random.seed(42)
        signature = abogus.generate_abogus(
            "device_platform=webapp&aid=6383&sec_user_id=MS4wLjABAAAA-test"
            "&max_cursor=0&count=20",
            user_agent=USER_AGENT,
            browser_fp=BROWSER_FP,
        )
    finally:
        random.setstate(state)

    assert signature == (
        "O7m0/QzVkVxPhESY56KLfY3q6Ar3YQ5I0SVkMD2fSnvLqL39HMYD9exoIBGvXY8jwG/"
        "-IeEjy4hbYNQprQ2n01wfHSko/2o/sfSkKl5Q5xSSs1XHCyUgJUkNmkt-tec2RvM5rOXMw7"
        "lHKREk09oHmhK4b1dzFgf3qJLzQD=="
    )


def test_generate_browser_fingerprint_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = random.getstate()
    try:
        random.seed(42)
        fingerprint = abogus.generate_browser_fingerprint()
    finally:
        random.setstate(state)

    assert (
        fingerprint
        == "1678|825|1702|908|0|0|0|0|1252|839|1384|1079|1678|825|24|24|MacIntel"
    )


def test_generate_browser_fingerprint_uses_given_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = random.getstate()
    try:
        random.seed(7)
        fingerprint = abogus.generate_browser_fingerprint("Win32")
    finally:
        random.setstate(state)

    assert fingerprint.endswith("|Win32")
    assert len(fingerprint.split("|")) == 17
