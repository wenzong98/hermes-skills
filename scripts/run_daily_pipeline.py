#!/usr/bin/env python3
"""Run the production data/advice/backtest/dashboard pipeline in order."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import List

from persistence import archive_decision, init_trades_db


def run_step(name: str, command: List[str], root: Path) -> None:
    print(f"[daily-pipeline] {name}: {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=root, check=True)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run daily US ETF production pipeline")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--start", default="2023-05-29")
    parser.add_argument("--portfolio-config", default=str(Path("~/.hermes/portfolio_config.json").expanduser()))
    parser.add_argument("--price-source", default="yahoo_chart_adjusted")
    parser.add_argument("--cape-vintage-path", default=str(root / "references" / "cape_vintage.csv"))
    parser.add_argument("--cache-dir", default=str(root / "references" / "data_cache"))
    parser.add_argument("--advice-output", default=str(root / "references" / "current_run_strict"))
    parser.add_argument("--backtest-output", default=str(root / "references"))
    args = parser.parse_args()

    py = args.python
    strict_data_args = [
        "--end", args.end,
        "--price-source", args.price_source,
        "--cache-dir", args.cache_dir,
        "--cape-vintage-path", args.cape_vintage_path,
        "--require-adjusted",
    ]
    run_step("update market databases", [py, "scripts/update_data_pipeline.py", "--end", args.end], root)
    run_step(
        "generate advice",
        [py, "scripts/current_market_advice.py", "--start", args.start, "--portfolio-config", args.portfolio_config,
         "--output-dir", args.advice_output, *strict_data_args],
        root,
    )
    advice_path = Path(args.advice_output) / "current_market_advice.json"
    advice = json.loads(advice_path.read_text(encoding="utf-8"))
    archived_date = archive_decision(root / "data" / "decisions.db", advice)
    init_trades_db(root / "data" / "trades.db")
    run_step(
        "rerun backtest",
        [py, "scripts/backtest_us_etf.py", "--start", args.start, "--output-dir", args.backtest_output, *strict_data_args],
        root,
    )
    run_step("build dashboard", [py, "scripts/build_dashboard.py", "--no-open"], root)
    print(json.dumps({"status": "ok", "decision_date": archived_date}, ensure_ascii=False))


if __name__ == "__main__":
    main()
