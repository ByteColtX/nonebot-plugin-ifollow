#!/usr/bin/env python3
"""从 CHANGELOG.md 提取指定版本的发布说明。

CI 在发布前调用本脚本；缺少对应版本段落时返回非零退出码，从而阻止发布。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_HEADING = re.compile(
    r"^##\s+\[?v?(?P<version>\d+\.\d+\.\d+)\]?(?:\s*-\s*\d{4}-\d{2}-\d{2})?\s*$"
)


class MissingSectionError(LookupError):
    """CHANGELOG 中缺少可用的版本段落。"""


def extract_release_notes(changelog: str, version: str) -> str:
    """提取某个版本的发布说明正文。

    Args:
        changelog: CHANGELOG 全文。
        version: 目标版本号，允许带 ``v`` 前缀。

    Returns:
        版本标题之后、下一个二级标题之前的正文，已去掉首尾空白。

    Raises:
        MissingSectionError: 找不到该版本，或该版本段落为空。
    """
    target = version.removeprefix("v")
    lines = changelog.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        if start is not None:
            return _body(lines[start:index], version)
        match = _HEADING.match(line)
        if match is not None and match.group("version") == target:
            start = index + 1
    if start is None:
        raise MissingSectionError(f"CHANGELOG 中没有 {version} 的段落")
    return _body(lines[start:], version)


def _body(lines: list[str], version: str) -> str:
    body = "\n".join(lines).strip()
    if not body:
        raise MissingSectionError(f"CHANGELOG 中 {version} 的段落为空")
    return body


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 命令行参数；默认读取 ``sys.argv``。

    Returns:
        进程退出码：0 表示成功，1 表示读取失败或缺少版本段落。
    """
    parser = argparse.ArgumentParser(description="提取 CHANGELOG 中的版本发布说明")
    parser.add_argument("--version", required=True, help="版本号，允许带 v 前缀")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=Path("CHANGELOG.md"),
        help="CHANGELOG 路径，默认 ./CHANGELOG.md",
    )
    parser.add_argument("--output", type=Path, help="写入文件；省略时打印到标准输出")
    args = parser.parse_args(argv)

    try:
        notes = extract_release_notes(
            args.changelog.read_text(encoding="utf-8"), args.version
        )
    except (OSError, MissingSectionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.output is None:
        print(notes)
    else:
        args.output.write_text(f"{notes}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
