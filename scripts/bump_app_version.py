#!/usr/bin/env python3
"""Push 前に APP_VERSION（YYYY-MM-DD + 英字）を更新する"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_FILES = (ROOT / "uemura_hp.py", ROOT / "uemura.py")
VERSION_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})([a-z]+)$')
APP_VERSION_RE = re.compile(r'^(APP_VERSION\s*=\s*")([^"]+)(".*)$')


def increment_suffix(suffix: str) -> str:
    if not suffix:
        return "a"
    chars = list(suffix)
    index = len(chars) - 1
    while index >= 0:
        if chars[index] != "z":
            chars[index] = chr(ord(chars[index]) + 1)
            return "".join(chars)
        chars[index] = "a"
        index -= 1
    return "a" + "".join(chars)


def next_version(current: str, today: date | None = None) -> str:
    today = today or date.today()
    today_str = today.isoformat()
    matched = VERSION_RE.match(clean_data_str(current))
    if not matched:
        return f"{today_str}a"
    version_date, suffix = matched.group(1), matched.group(2)
    if version_date != today_str:
        return f"{today_str}a"
    return f"{today_str}{increment_suffix(suffix)}"


def clean_data_str(value) -> str:
    return str(value or "").strip()


def read_current_version() -> str:
    for path in TARGET_FILES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            matched = APP_VERSION_RE.match(line)
            if matched:
                return matched.group(2)
    return ""


def update_file(path: Path, new_version: str) -> bool:
    text = path.read_text(encoding="utf-8")
    updated_lines = []
    changed = False
    for line in text.splitlines():
        matched = APP_VERSION_RE.match(line)
        if matched:
            new_line = f'{matched.group(1)}{new_version}{matched.group(3)}'
            if new_line != line:
                changed = True
            updated_lines.append(new_line)
        else:
            updated_lines.append(line)
    if changed:
        path.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    return changed


def main() -> int:
    current = read_current_version()
    new_version = next_version(current)
    if new_version == current:
        print(f"APP_VERSION unchanged: {current}")
        return 0

    changed_any = False
    for path in TARGET_FILES:
        if path.exists() and update_file(path, new_version):
            changed_any = True
            print(f"Updated {path.name}: {current} -> {new_version}")

    if not changed_any:
        print("No target files updated.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
