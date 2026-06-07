#!/usr/bin/env python3
"""
Walk-forward backtest for the US ETF DCA system.

Splits the backtest into rolling train/test windows so we can see
out-of-sample (OOS) performance, not just in-sample. The pattern is
from vectorbt's WFO and the lean walk-forward toolkit.

Window config (defaults):
  train_months = 24
  test_months  = 6
  step_months  = 3   (roll forward every quarter)

For each window:
  1. Run the production-rule backtest on the train slice.
  2. Use the *same* production rule (no re-fitting!) on the test
     slice — the point of walk-forward here is to verify the
     *rule* is robust out-of-sample, not to refit the rule.
  3. Stitch test-slice equity curves into one OOS equity series.

The OOS metrics (XIRR, Sharpe, max DD) are reported separately
from the in-sample train metrics. If OOS << in-sample, the rule is
overfit; if OOS ≈ in-sample, the rule is robust.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from backtest_us_etf import prepare_dataset, run_backtest  # noqa: E402


@dataclass(frozen=True)
class Window:
    train_start: str
    train_end: str
    test_start: str
    test_end: str


def make_windows(
    start: str, end: str, train_months: int = 24, test_months: int = 6, step_months: int = 3
) -> List[Window]:
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    out: List[Window] = []
    cur = s
    while True:
        train_start = cur
        train_end = (cur + pd.DateOffset(months=train_months) - pd.offsets.BDay(1))
        test_start = (train_end + pd.offsets.BDay(1))
        test_end = (test_start + pd.DateOffset(months=test_months) - pd.offsets.BDay(1))
        if test_end > e:
            test_end = e
        if test_start >= e:
            break
        out.append(
            Window(
                train_start=train_start.strftime("%Y-%m-%d"),
                train_end=train_end.strftime("%Y-%m-%d"),
                test_start=test_start.strftime("%Y-%m-%d"),
                test_end=test_end.strftime("%Y-%m-%d"),
            )
        )
        cur = cur + pd.DateOffset(months=step_months)
    return out


def _slice_metrics(payload: dict, initial_capital: float, weekly_budget: float) -> dict:
    s = payload["result"]["strategy"]
    return {
        "xirr": s["xirr"],
        "sharpe": s["sharpe"],
        "unitized_max_dd": s["unitized_max_drawdown"],
        "final_value": s["final_value"],
        "trade_count": s["trade_count"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=str, default="2020-01-02")
    parser.add_argument("--end", type=str, default="2026-05-29")
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--weekly-budget", type=float, default=2_000.0)
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=3)
    parser.add_argument("--price-source", type=str, default="yahoo_chart_adjusted")
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=str(_ROOT / "references" / "data_cache"),
    )
    parser.add_argument(
        "--cape-vintage-path",
        type=str,
        default=str(_ROOT / "references" / "cape_vintage.csv"),
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(_ROOT / "references" / "validation" / "walk_forward"),
    )
    args = parser.parse_args()

    df = prepare_dataset(
        args.start, args.end,
        price_source=args.price_source,
        cache_dir=args.cache_dir,
        cape_vintage_path=args.cape_vintage_path,
    )
    windows = make_windows(
        args.start, args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )

    rows = []
    stitched = []
    for w in windows:
        try:
            train_payload = run_backtest(
                df, w.train_start, w.train_end,
                initial_capital=args.initial_capital,
                weekly_budget=args.weekly_budget,
            )
            test_payload = run_backtest(
                df, w.test_start, w.test_end,
                initial_capital=args.initial_capital,
                weekly_budget=args.weekly_budget,
            )
        except Exception as e:  # noqa: BLE001
            rows.append(
                {
                    "train": f"{w.train_start}/{w.train_end}",
                    "test": f"{w.test_start}/{w.test_end}",
                    "error": str(e),
                }
            )
            continue
        train_m = _slice_metrics(train_payload, args.initial_capital, args.weekly_budget)
        test_m = _slice_metrics(test_payload, args.initial_capital, args.weekly_budget)
        rows.append(
            {
                "train": f"{w.train_start}/{w.train_end}",
                "test": f"{w.test_start}/{w.test_end}",
                "train_xirr": train_m["xirr"],
                "test_xirr": test_m["xirr"],
                "train_sharpe": train_m["sharpe"],
                "test_sharpe": test_m["sharpe"],
                "train_dd": train_m["unitized_max_dd"],
                "test_dd": test_m["unitized_max_dd"],
            }
        )
        # Stitch test equity
        eq = test_payload["equity_curve"].copy()
        eq["date"] = pd.to_datetime(eq["date"])
        stitched.append(eq.set_index("date")[["strategy_nav", "benchmark_nav"]])
    stitched_df = pd.concat(stitched) if stitched else pd.DataFrame()
    stitched_df = stitched_df[~stitched_df.index.duplicated(keep="first")].sort_index()

    # Compute OOS XIRR / Sharpe
    oos_sharpe = float("nan")
    oos_xirr = float("nan")
    if not stitched_df.empty:
        rets = stitched_df["strategy_nav"].pct_change().dropna()
        if len(rets) > 1 and rets.std(ddof=1) > 0:
            oos_sharpe = float(rets.mean() / rets.std(ddof=1) * math.sqrt(252))
        # XIRR stitched: assume weekly $2000 contributions on each
        # Thursday of the stitched window.
        from backtest_us_etf import xirr  # noqa: E402
        weekly = args.weekly_budget
        cfs = []
        dts = pd.date_range(stitched_df.index[0], stitched_df.index[-1], freq="W-THU")
        for d in dts:
            if d in stitched_df.index or (stitched_df.index[0] <= d <= stitched_df.index[-1]):
                cfs.append((d, -weekly))
        cfs.insert(0, (stitched_df.index[0], -args.initial_capital))
        cfs.append((stitched_df.index[-1], float(stitched_df["strategy_nav"].iloc[-1])))
        oos_xirr = xirr(cfs) or float("nan")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df_rows = pd.DataFrame(rows)
    df_rows.to_csv(out_dir / "walk_forward_windows.csv", index=False)
    stitched_df.to_csv(out_dir / "stitched_oos_equity.csv", index_label="date")
    summary = {
        "n_windows": len(windows),
        "oos_xirr": oos_xirr,
        "oos_sharpe": oos_sharpe,
        "train_xirr_mean": float(df_rows["train_xirr"].mean()) if "train_xirr" in df_rows else None,
        "test_xirr_mean": float(df_rows["test_xirr"].mean()) if "test_xirr" in df_rows else None,
        "train_sharpe_mean": float(df_rows["train_sharpe"].mean()) if "train_sharpe" in df_rows else None,
        "test_sharpe_mean": float(df_rows["test_sharpe"].mean()) if "test_sharpe" in df_rows else None,
    }
    (out_dir / "walk_forward_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md = ["# Walk-Forward Backtest", ""]
    md.append(f"Window: {args.start} → {args.end}  ")
    md.append(f"Train: {args.train_months}m, Test: {args.test_months}m, Step: {args.step_months}m  ")
    md.append(f"Number of windows: **{len(windows)}**")
    md.append("")
    md.append("## Out-of-sample (stitched test slices)")
    md.append("")
    md.append(f"- **OOS XIRR**: {oos_xirr:.2%}" if not math.isnan(oos_xirr) else "- OOS XIRR: NaN")
    md.append(f"- **OOS Sharpe (annualized)**: {oos_sharpe:.3f}" if not math.isnan(oos_sharpe) else "- OOS Sharpe: NaN")
    md.append("")
    md.append("## In-sample vs out-of-sample")
    md.append("")
    md.append("| Metric | In-sample (train mean) | Out-of-sample (test mean) |")
    md.append("|---|---|---|")
    md.append(f"| XIRR | {summary['train_xirr_mean']:.2%} | {summary['test_xirr_mean']:.2%} |")
    md.append(f"| Sharpe | {summary['train_sharpe_mean']:.3f} | {summary['test_sharpe_mean']:.3f} |")
    md.append("")
    md.append("If OOS ≈ IS, the rule is robust. If OOS << IS, the rule is overfit.  ")
    md.append("This script does **not** re-fit the rule per window — that's a  ")
    md.append("separate optimization task. The point is to verify the *chosen*  ")
    md.append("rule performs OOS at all.")
    (out_dir / "walk_forward_report.md").write_text("\n".join(md), encoding="utf-8")

    print(f"[wf] {len(windows)} windows, OOS XIRR={oos_xirr:.2%}, OOS Sharpe={oos_sharpe:.3f}")


if __name__ == "__main__":
    main()
