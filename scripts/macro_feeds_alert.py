#!/usr/bin/env python3
"""Failure-alert aggregator for macro_feeds_refresh.

Reads `~/.hermes/cron/macro_feeds_refresh_errors.jsonl` (one JSON record
per failure, written by `macro_feeds_refresh.py`) and emits a synthetic
"alert=true" row when a `(source, error_type)` pair has appeared 2+ times
in the last 24h.

Design
------
- File-only: per user decision (2026-06-08), we do NOT push to Telegram
  or any external channel. The user checks alerts via
  `python3 scripts/macro_feeds_alert.py --print` (RUN.md step 9) or
  `jq -c 'select(.alerted==true)' ~/.hermes/cron/macro_feeds_refresh_errors.jsonl`.
- Idempotent: an alert row is appended at most once per 24h per
  `(source, error_type)`. Subsequent failures in the same window are
  recorded as normal error rows but do not re-alert.
- Self-contained: zero imports from the rest of the skill, so it can be
  tested / run from anywhere.

Output shape
------------
  {
    "new_alerts": [
      {"source": "gdelt", "error_type": "UnavailableBlock", "count_24h": 3, "last_message": "..."}
    ],
    "alerted_rows_appended": N
  }
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ERROR_LOG = Path("~/.hermes/cron/macro_feeds_refresh_errors.jsonl").expanduser()
ALERT_THRESHOLD = 2  # consecutive failures per (source, error_type) before alert
WINDOW_HOURS = 24


def _load_window() -> List[dict]:
    """Return all records from the last 24h, oldest first."""
    if not ERROR_LOG.exists():
        return []
    cutoff = _dt.datetime.now() - _dt.timedelta(hours=WINDOW_HOURS)
    out: List[dict] = []
    for line in ERROR_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = row.get("ts")
        if not ts:
            continue
        try:
            row_dt = _dt.datetime.fromisoformat(ts)
        except ValueError:
            continue
        if row_dt >= cutoff:
            out.append(row)
    return out


def _existing_alerts(rows: List[dict]) -> set:
    """Set of (source, error_type) tuples that already have an alert row in window."""
    pairs: set = set()
    for r in rows:
        if r.get("alerted"):
            pairs.add((r.get("source", ""), r.get("error_type", "")))
    return pairs


def evaluate() -> dict:
    """Compute new alerts and append alert rows. Returns a summary dict."""
    rows = _load_window()
    already = _existing_alerts(rows)

    # Count occurrences per (source, error_type), only for non-alert rows
    # (alert rows themselves are markers and shouldn't count toward the
    # "2 consecutive failures" threshold).
    counts: Dict[Tuple[str, str], List[dict]] = {}
    for r in rows:
        if r.get("alerted"):
            continue
        key = (r.get("source", ""), r.get("error_type", ""))
        counts.setdefault(key, []).append(r)

    new_alerts: List[dict] = []
    alert_rows_to_write: List[dict] = []
    now_iso = _dt.datetime.now().isoformat(timespec="seconds")
    for (src, etype), occurrences in counts.items():
        if (src, etype) in already:
            continue
        if len(occurrences) < ALERT_THRESHOLD:
            continue
        last = occurrences[-1]
        new_alerts.append({
            "source": src,
            "error_type": etype,
            "count_24h": len(occurrences),
            "last_message": last.get("message", ""),
        })
        alert_rows_to_write.append({
            "ts": now_iso,
            "source": src,
            "error_type": etype,
            "message": f"ALERT: {len(occurrences)} failures in {WINDOW_HOURS}h (last: {last.get('message', '')[:200]})",
            "alerted": True,
        })

    if alert_rows_to_write:
        try:
            ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
            with ERROR_LOG.open("a", encoding="utf-8") as fh:
                for r in alert_rows_to_write:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        except OSError as exc:
            return {"new_alerts": new_alerts, "alerted_rows_appended": 0, "error": str(exc)}

    return {
        "new_alerts": new_alerts,
        "alerted_rows_appended": len(alert_rows_to_write),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="macro_feeds_refresh failure-aggregator")
    parser.add_argument("--print", action="store_true", help="Print human-readable summary and exit 0/1")
    parser.add_argument("--json", action="store_true", help="Print raw evaluate() result as JSON and exit 0")
    args = parser.parse_args()

    result = evaluate()
    if args.json or (not args.print):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.print:
        alerts = result.get("new_alerts", [])
        if not alerts:
            print("no alerts (all sources within tolerance)")
            return 0
        print(f"⚠ {len(alerts)} unresolved alert(s):")
        for a in alerts:
            print(f"  - {a['source']} / {a['error_type']}: {a['count_24h']} failures in 24h")
            print(f"    last: {a['last_message'][:200]}")
        return 0  # exit 0 even when alerts exist — the file is the source of truth
    return 0


if __name__ == "__main__":
    sys.exit(main())
