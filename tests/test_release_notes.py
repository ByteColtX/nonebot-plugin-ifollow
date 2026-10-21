from __future__ import annotations

import pytest

from release_notes import MissingSectionError, extract_release_notes

CHANGELOG = """# 更新日志

## 未发布

## 2.0.8 - 2026-10-23

### 新功能

- 新功能条目

## 2.0.7 - 2026-10-22

### 修复

- 修复条目

## 1.0.0 - 2026-08-25

### 新功能

- 初始版本
"""


def test_extracts_section_until_next_heading() -> None:
    assert extract_release_notes(CHANGELOG, "2.0.7") == "### 修复\n\n- 修复条目"


def test_accepts_v_prefix_and_brackets() -> None:
    text = "## [2.0.7] - 2026-10-22\n\n### 修复\n\n- 条目\n"

    assert extract_release_notes(text, "v2.0.7") == "### 修复\n\n- 条目"


def test_extracts_last_section_to_end_of_file() -> None:
    assert extract_release_notes(CHANGELOG, "1.0.0") == "### 新功能\n\n- 初始版本"


def test_does_not_match_version_prefixes() -> None:
    text = "## 2.0.10 - 2026-10-22\n\n- 新版本\n\n## 2.0.1 - 2026-10-22\n\n- 旧版本\n"

    assert extract_release_notes(text, "2.0.1") == "- 旧版本"


def test_missing_version_raises() -> None:
    with pytest.raises(MissingSectionError):
        extract_release_notes(CHANGELOG, "9.9.9")


def test_empty_section_raises() -> None:
    text = "## 2.0.7 - 2026-10-22\n\n## 2.0.6 - 2026-10-21\n\n- 条目\n"

    with pytest.raises(MissingSectionError):
        extract_release_notes(text, "2.0.7")
