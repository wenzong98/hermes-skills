#!/usr/bin/env python3
"""Incremental macro-feeds refresh for the us-etf-quant-system dashboard.

Why this script exists
----------------------
`build_dashboard.py:_macro_feeds_block()` is the *only* place that pulls
news (Bloomberg RSS / GDELT / NewsAPI fallback) and the economic calendar
(ForexFactory + Federal Reserve). It is invoked once per day inside
`usetf-daily-refresh.sh` at 05:30 Mon-Sat. Between refreshes, the
dashboard sees a stale news/calendar snapshot — anything posted in the
intervening hours (US after-hours FOMC statement, weekend G20, etc.) is
invisible until the next 05:30 run.

This script provides an *incremental* refresh: it pulls the three feeds
again, runs them through the LLM translator, and overwrites **only**
`data.json["macroFeeds"]` — leaving every other dashboard field (the
advice that daily-refresh wrote, the backtest snapshot, etc.) untouched.

Failure handling
----------------
- The whole `try/except` in `_macro_feeds_block` already swallows errors
  and returns `{"available": False, "error": "..."}`. We *also* wrap the
  whole script in our own try/except so any other failure (data.json
  missing, JSON decode, disk full) is recorded.
- On *any* source-level failure inside the macro block, we log a row to
  `~/.hermes/cron/macro_feeds_refresh_errors.jsonl`. `macro_feeds_alert.py`
  reads this file and flags 2+ consecutive failures per
  `(source, error_type)`.
- Before writing `data.json` we copy the current file to `data.json.bak`
  so a partial write never leaves the dashboard broken.

Persistence
-----------
- `references/dashboard/data.json` is the inlined payload of
  `references/dashboard/index.html` (the latter is rebuilt by
  build_dashboard.py from the former; they live in lock-step at
  build time). For an incremental refresh we **only** patch the JSON
  payload — the HTML wrapper continues to read from the inlined
  `data.json` blob, so the change is visible the next time the
  browser refetches `index.html`. (ECharts charts, signal cards, etc.
  are regenerated client-side from the JSON.)
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import shutil
import sys
import traceback
from pathlib import Path

# Run from the project root with `python3 scripts/macro_feeds_refresh.py`
# — Python automatically prepends the script's parent dir (i.e. `scripts/`)
# to sys.path, so sibling modules (build_dashboard, persistence) import
# directly without a `scripts.` prefix.

from build_dashboard import _macro_feeds_block  # noqa: E402

ERROR_LOG = Path("~/.hermes/cron/macro_feeds_refresh_errors.jsonl").expanduser()
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
DASHBOARD_JSON = ROOT / "references" / "dashboard" / "data.json"
DASHBOARD_JSON_BAK = ROOT / "references" / "dashboard" / "data.json.bak"

logger = logging.getLogger("macro_feeds_refresh")
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )


def _log_error(source: str, error_type: str, message: str) -> None:
    """Append a single error record. Best-effort; never raises."""
    try:
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": _dt.datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "error_type": error_type,
            "message": message[:500],
        }
        with ERROR_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to append to error log: %s", exc)


def main() -> int:
    if not DASHBOARD_JSON.exists():
        _log_error("data_json", "missing", f"{DASHBOARD_JSON} does not exist; run build_dashboard.py first")
        print(f"ERROR: {DASHBOARD_JSON} missing — run scripts/build_dashboard.py once first", file=sys.stderr)
        return 2

    # ---- 1. Pull the macro block ----
    try:
        block = _macro_feeds_block(advice={})  # advice arg is unused
    except Exception as exc:  # noqa: BLE001
        _log_error("macro_feeds_block", exc.__class__.__name__, str(exc))
        logger.error("macro_feeds_block raised: %s", exc)
        return 1

    # The block itself returns {"available": False, "error": "..."} on
    # failure; surface that as a recorded error too so the alert script
    # can pick it up.
    if not block.get("available", False):
        err = block.get("error", "macro feeds returned available=False")
        _log_error("macro_feeds_block", "UnavailableBlock", err)
        # We still continue to write what we got — the dashboard will
        # show whatever partial data was available. The alert script
        # will see this row in the jsonl.
        logger.warning("macro block reports unavailable: %s", err)
    else:
        # Record per-source success counts so we can see "RSS gave 8,
        # GDELT gave 0" without grepping.
        for src in ("rss", "gdelt", "calendar"):
            count = len(block.get(src) or [])
            if count == 0:
                _log_error(src, "EmptyResult", f"{src} returned 0 items")

    # ---- 2. Patch data.json["macroFeeds"] with backup ----
    try:
        original = json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _log_error("data_json", "ReadFailed", str(exc))
        logger.error("could not read data.json: %s", exc)
        return 1

    if "macroFeeds" in original and isinstance(original["macroFeeds"], dict):
        original["macroFeeds"].update(block)
    else:
        original["macroFeeds"] = block

    try:
        # Backup first — `shutil.copy2` preserves mtime so the .bak
        # timestamp can serve as a "last good snapshot" reference.
        shutil.copy2(DASHBOARD_JSON, DASHBOARD_JSON_BAK)
        # Write to a temp file then rename — atomic on POSIX.
        tmp = DASHBOARD_JSON.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
        tmp.replace(DASHBOARD_JSON)
    except Exception as exc:  # noqa: BLE001
        _log_error("data_json", "WriteFailed", str(exc))
        logger.error("could not write data.json: %s", exc)
        return 1

    summary = {
        "rss": len(block.get("rss") or []),
        "gdelt": len(block.get("gdelt") or []),
        "calendar": len(block.get("calendar") or []),
        "available": block.get("available", False),
        "translated": block.get("translated", False),
        "fetchedAt": block.get("fetchedAt"),
    }
    print(json.dumps({"status": "ok", "summary": summary}, ensure_ascii=False))

    # ---- 3. Trigger alert aggregation (best-effort) ----
    try:
        from macro_feeds_alert import evaluate as _alert_evaluate
        result = _alert_evaluate()
        if result.get("new_alerts"):
            print(json.dumps({"alerts": result["new_alerts"]}, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        # Never fail the refresh because of a downstream alert issue.
        logger.warning("alert aggregation failed: %s", exc)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        # Last-resort guard: if *anything* above this point blew up
        # without being caught, log a crash row and exit non-zero.
        _log_error("macro_feeds_refresh", exc.__class__.__name__, "".join(traceback.format_exception_only(type(exc), exc)).strip())
        print(f"CRASH: {exc}", file=sys.stderr)
        sys.exit(1)
