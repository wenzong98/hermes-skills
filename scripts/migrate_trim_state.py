#!/usr/bin/env python3
"""One-shot migration of the QQQ trim-state file from the legacy skill-repo
path to the new `~/.hermes/state/` location.

Run once after upgrading the skill to a version that defaults
`--trim-state-file` to `~/.hermes/state/us_etf_trim_state.json`. Safe to
re-run: if the target already exists, this exits 0 without touching it.

Legacy paths checked (in order, first match wins):
  1. `~/.hermes/us_etf_trim_state.json` (used when the cron wrapper
     symlinked the file outside the skill repo).
  2. `~/.hermes/skills/research/us-etf-quant-system/references/cron_run/
     .trim_state.json` (the original skill-internal location).

If neither exists, an empty state file is created at the new path so the
cron push has a target to write to.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

OLD_PATHS = [
    Path("~/.hermes/us_etf_trim_state.json").expanduser(),
    Path("~/.hermes/skills/research/us-etf-quant-system/references/cron_run/.trim_state.json").expanduser(),
]
NEW_PATH = Path("~/.hermes/state/us_etf_trim_state.json").expanduser()


def main() -> int:
    NEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    if NEW_PATH.exists():
        print(f"目标已存在：{NEW_PATH}；无需迁移。")
        return 0

    for old in OLD_PATHS:
        if not old.exists():
            continue
        # If the legacy path is a symlink, resolve it to the real file
        # before copying so we don't end up copying a dangling link.
        src = old.resolve() if old.is_symlink() else old
        if not src.exists():
            print(f"跳过 {old}（symlink 解析后不存在）")
            continue
        shutil.copy2(src, NEW_PATH)
        print(f"已迁移 {src} -> {NEW_PATH}")
        return 0

    NEW_PATH.write_text(json.dumps({"last_trim": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已创建空状态文件 {NEW_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
