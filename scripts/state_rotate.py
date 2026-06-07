#!/usr/bin/env python3
"""Weekly state-rotation for the us-etf-quant-system skill.

Designed to be invoked from com.hermes.usetf-state-rotate LaunchAgent
(weekly, Sun 03:00 local) via the ~/.hermes/scripts/usetf-state-rotate.sh
wrapper. Idempotent and safe to re-run.

Three rotation policies:

1. **llm_usage.jsonl — weekly logrotate, keep 4 generations**
   references/llm_usage.jsonl is append-only and grows ~10 KB / week.
   When it crosses 500 KB we rotate:
     llm_usage.jsonl -> llm_usage.jsonl.1 (current week, just-finished)
     llm_usage.jsonl.1 -> llm_usage.jsonl.2
     ...up to .3
   We keep .1 / .2 / .3 (last 3 finished weeks) plus the live file
   (current week). 4 generations total. The .2 / .3 files are renamed
   first so we never lose data if the script crashes mid-rotation.

2. **current_run/ + current_run_strict/ — weekly tar.gz archive, keep 4 weeks**
   Both dirs are overwritten on every daily-refresh run. We tar them up
   into ~/.hermes/state/us_etf/history/{ISO-week}.tar.gz and gzip. After
   the archive is verified, we leave the dirs alone (next daily-refresh
   will overwrite — that's fine, we have the snapshot).

3. **.advice_state.json — monthly timestamped backup, keep 6 months**
   This single file in references/cron_run/ is the dedupe key for the
   Telegram push. We don't rotate it (always live) but we copy it to
   ~/.hermes/state/us_etf/advice_state/YYYY-MM.json on the 1st of each
   month, keeping the last 6 months.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tarfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERMES_STATE = Path("~/.hermes/state").expanduser()
LLM_USAGE = ROOT / "references" / "llm_usage.jsonl"
CURRENT_RUN = ROOT / "references" / "current_run"
CURRENT_RUN_STRICT = ROOT / "references" / "current_run_strict"
ADVICE_STATE = ROOT / "references" / "cron_run" / ".advice_state.json"

HISTORY_DIR = HERMES_STATE / "us_etf" / "history"
ADVICE_STATE_BACKUP_DIR = HERMES_STATE / "us_etf" / "advice_state"

LLM_USAGE_MAX_BYTES = 500_000          # rotate when above this
LLM_USAGE_KEEP = 4                     # live + .1 + .2 + .3
HISTORY_KEEP_WEEKS = 4
ADVICE_STATE_KEEP_MONTHS = 6


def iso_week_tag(now: datetime) -> str:
    """Return ISO year-week tag like '2026-W23'."""
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def rotate_llm_usage(now: datetime) -> dict:
    """Rotate llm_usage.jsonl if it exceeds threshold. Returns action log."""
    log: dict = {"skipped": True, "reason": ""}
    if not LLM_USAGE.exists():
        log["reason"] = "llm_usage.jsonl does not exist"
        return log
    size = LLM_USAGE.stat().st_size
    if size < LLM_USAGE_MAX_BYTES:
        log["reason"] = f"size={size} < {LLM_USAGE_MAX_BYTES}, no rotation"
        return log

    log["skipped"] = False
    log["size"] = size
    # Shift older rotations first: .3 -> drop, .2 -> .3, .1 -> .2
    oldest = LLM_USAGE.with_suffix(".jsonl.3")
    if oldest.exists():
        oldest.unlink()
    for n in (2, 1):
        src = LLM_USAGE.with_suffix(f".jsonl.{n}")
        dst = LLM_USAGE.with_suffix(f".jsonl.{n + 1}")
        if src.exists():
            src.rename(dst)
    # Move live -> .1
    LLM_USAGE.rename(LLM_USAGE.with_suffix(".jsonl.1"))
    # Create fresh empty file with same mode
    LLM_USAGE.touch(mode=0o644)
    log["rotated_to"] = "llm_usage.jsonl.1"
    log["timestamp"] = now.isoformat(timespec="seconds")
    return log


def archive_current_run(now: datetime) -> dict:
    """Tar + gzip current_run/ and current_run_strict/ into a single archive."""
    tag = iso_week_tag(now)
    archive_path = HISTORY_DIR / f"{tag}.tar.gz"
    log: dict = {"skipped": False, "tag": tag, "members": []}

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        log["skipped"] = True
        log["reason"] = f"{archive_path.name} already exists this week"
        return log

    sources = []
    for d in (CURRENT_RUN, CURRENT_RUN_STRICT):
        if d.exists() and any(d.iterdir()):
            sources.append(d)
            log["members"].append(d.relative_to(ROOT).as_posix())

    if not sources:
        log["skipped"] = True
        log["reason"] = "no current_run / current_run_strict content to archive"
        return log

    with tarfile.open(archive_path, "w:gz") as tf:
        for src in sources:
            tf.add(src, arcname=src.relative_to(ROOT.parent).as_posix())
    log["archive"] = str(archive_path)
    log["bytes"] = archive_path.stat().st_size
    return log


def prune_history(now: datetime) -> list:
    """Keep only the most recent HISTORY_KEEP_WEEKS archives."""
    if not HISTORY_DIR.exists():
        return []
    archives = sorted(HISTORY_DIR.glob("*-W*.tar.gz"))
    keep = archives[-HISTORY_KEEP_WEEKS:]
    dropped = []
    for old in archives:
        if old not in keep:
            old.unlink()
            dropped.append(old.name)
    return dropped


def backup_advice_state(now: datetime) -> dict:
    """On the 1st of each month, snapshot .advice_state.json with a month tag."""
    log: dict = {"skipped": True, "reason": ""}
    if not ADVICE_STATE.exists():
        log["reason"] = ".advice_state.json does not exist"
        return log
    if now.day != 1:
        log["reason"] = f"today is day {now.day}, not the 1st — no monthly backup"
        return log

    ADVICE_STATE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    month_tag = now.strftime("%Y-%m")
    backup_path = ADVICE_STATE_BACKUP_DIR / f"{month_tag}.json"
    if backup_path.exists():
        log["reason"] = f"{backup_path.name} already exists for this month"
        return log
    shutil.copy2(ADVICE_STATE, backup_path)
    log["skipped"] = False
    log["backup"] = str(backup_path)

    # Prune old monthly backups beyond the keep window
    backups = sorted(ADVICE_STATE_BACKUP_DIR.glob("*.json"))
    keep = backups[-ADVICE_STATE_KEEP_MONTHS:]
    for old in backups:
        if old not in keep:
            old.unlink()
    return log


def main() -> int:
    parser = argparse.ArgumentParser(description="us-etf-quant-system state-rotate")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print per-step details")
    args = parser.parse_args()

    now = datetime.now()
    summary: dict = {"timestamp": now.isoformat(timespec="seconds")}

    actions = {
        "rotate_llm_usage": rotate_llm_usage(now),
        "archive_current_run": archive_current_run(now),
        "prune_history": {"dropped": []},
        "backup_advice_state": backup_advice_state(now),
    }
    if not actions["archive_current_run"].get("skipped", False):
        actions["prune_history"] = {"dropped": prune_history(now)}

    summary["actions"] = actions

    if args.dry_run:
        print(json.dumps({"dry_run": True, **summary}, ensure_ascii=False, indent=2))
        return 0

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
