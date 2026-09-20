#!/usr/bin/env python3
"""Validate local image references in a Xiaohuoche ribao HTML file."""

from __future__ import annotations

import argparse
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse


class ImageCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        values = dict(attrs)
        source = values.get("src")
        if source:
            self.sources.append(source)


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 HTML 图片是否重复引用或缺失")
    parser.add_argument("html", type=Path, help="待检查的 HTML 文件")
    args = parser.parse_args()

    html_path = args.html.resolve()
    if not html_path.is_file():
        print(f"ERROR: HTML 文件不存在：{html_path}")
        return 2

    html_content = html_path.read_text(encoding="utf-8")
    collector = ImageCollector()
    collector.feed(html_content)

    local_sources = [
        source
        for source in collector.sources
        if urlparse(source).scheme not in {"http", "https", "data"}
    ]
    duplicates = sorted(source for source, count in Counter(local_sources).items() if count > 1)
    missing = sorted(
        {
            source
            for source in local_sources
            if not (html_path.parent / unquote(urlparse(source).path)).resolve().is_file()
        }
    )

    legacy_abbreviation = "X" + "HC"
    forbidden_legacy_abbreviation = (
        re.search(rf"\b{legacy_abbreviation}\b", html_content, re.IGNORECASE) is not None
    )
    if duplicates:
        print("ERROR: 存在重复图片引用：" + ", ".join(duplicates))
    if missing:
        print("ERROR: 存在缺失图片：" + ", ".join(missing))
    if forbidden_legacy_abbreviation:
        print("ERROR: 俱乐部缩写必须使用 XZQ，不能出现旧缩写")
    if duplicates or missing or forbidden_legacy_abbreviation:
        return 1

    print(f"OK: 共检查 {len(local_sources)} 张本地图片，引用唯一、文件存在且缩写为 XZQ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
