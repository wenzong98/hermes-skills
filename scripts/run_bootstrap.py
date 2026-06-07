#!/usr/bin/env python3
"""
Block bootstrap on a backtest equity curve to estimate the
distribution of (final value, XIRR) under the *same* return
process. We block-sample 21-day windows to preserve short-term
volatility clustering — IID bootstrap would overstate diversity.

AFML Ch. 4 / Snippet 4.5 motivates block bootstrap; the choice of
block length follows the rule of thumb: sqrt(T) ≈ block size for
squared-return autocorrelation. We default to block=21 (1 month)
which is appropriate for daily equity data.

Reports:
  90% CI for final value
  90% CI for annualized Sharpe
  5th / 50th / 95th percentile equity curve bands (saved to CSV)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from backtest_us_etf import prepare_dataset, run_backtest, xirr  # noqa: E402


def block_bootstrap_returns(
    daily_returns: np.ndarray, n_boot: int, block_size: int = 21, seed: int = 0
) -> np.ndarray:
    """Generate `n_boot` synthetic return series by drawing
    consecutive blocks of `block_size` days with replacement.

    Returns array of shape (n_boot, T) where T = len(daily_returns).
    """
    rng = np.random.default_rng(seed)
    n = len(daily_returns)
    n_blocks = math.ceil(n / block_size)
    series = np.empty((n_boot, n), dtype=float)
    starts = np.arange(0, n - block_size + 1)
    for b in range(n_boot):
        idx = rng.choice(starts, size=n_blocks, replace=True)
        out = []
        for s in idx:
            out.extend(daily_returns[s : s + block_size].tolist())
        series[b] = np.array(out[:n])
    return series


def _ann_sharpe(r: np.ndarray) -> float:
    if len(r) < 2 or r.std(ddof=1) == 0:
        return float("nan")
    return float(r.mean() / r.std(ddof=1) * math.sqrt(252))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=str, default="2020-01-02")
    parser.add_argument("--end", type=str, default="2026-05-29")
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--weekly-budget", type=float, default=2_000.0)
    parser.add_argument("--n-boot", type=int, default=500)
    parser.add_argument("--block-size", type=int, default=21)
    parser.add_argument("--seed", type=int, default=20260606)
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
        default=str(_ROOT / "references" / "validation" / "bootstrap"),
    )
    args = parser.parse_args()

    df = prepare_dataset(
        args.start, args.end,
        price_source=args.price_source,
        cache_dir=args.cache_dir,
        cape_vintage_path=args.cape_vintage_path,
    )
    payload = run_backtest(
        df, args.start, args.end,
        initial_capital=args.initial_capital,
        weekly_budget=args.weekly_budget,
    )
    eq = payload["equity_curve"].copy()
    eq["date"] = pd.to_datetime(eq["date"])
    eq = eq.set_index("date")
    daily_returns = eq["strategy_nav"].astype(float).pct_change().dropna().values
    actual_dollar = float(eq["strategy_value"].iloc[-1])
    final_values = []
    sharpes = []
    # For percentile bands, build a matrix of cumulative nav paths.
    boot_nav = np.empty((args.n_boot, len(eq)))
    for i in range(args.n_boot):
        syn_returns = block_bootstrap_returns(
            daily_returns, n_boot=1, block_size=args.block_size, seed=args.seed + i
        )[0]
        # Anchor to initial capital at t=0, then grow over len(eq)-1
        # return steps to match the equity curve length.
        path = np.empty(len(eq))
        path[0] = args.initial_capital
        path[1:] = args.initial_capital * np.cumprod(1 + syn_returns)[: len(eq) - 1]
        boot_nav[i] = path
        final_values.append(float(path[-1]))
        sharpes.append(_ann_sharpe(syn_returns))
    final_values = np.array(final_values)
    sharpes = np.array(sharpes)
    p5, p50, p95 = np.percentile(final_values, [5, 50, 95])
    s5, s50, s95 = np.percentile(sharpes[~np.isnan(sharpes)], [5, 50, 95])

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / "boot_nav.npy", boot_nav)
    summary = {
        "n_boot": args.n_boot,
        "block_size": args.block_size,
        "final_value": {
            "p5": float(p5),
            "p50": float(p50),
            "p95": float(p95),
            "actual": actual_dollar,
        },
        "annualized_sharpe": {
            "p5": float(s5),
            "p50": float(s50),
            "p95": float(s95),
            "actual": float(payload["result"]["strategy"]["sharpe"]),
        },
    }
    (out_dir / "bootstrap_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    md = ["# Block bootstrap on equity curve", ""]
    md.append(f"n_boot={args.n_boot}, block_size={args.block_size} days, seed={args.seed}")
    md.append("")
    md.append("## Final value (90% CI)")
    md.append(f"- 5th percentile: **${p5:,.0f}**")
    md.append(f"- 50th percentile: **${p50:,.0f}**")
    md.append(f"- 95th percentile: **${p95:,.0f}**")
    md.append(f"- Actual: ${summary['final_value']['actual']:,.0f}")
    md.append("")
    md.append("## Annualized Sharpe (90% CI)")
    md.append(f"- 5th percentile: **{s5:.3f}**")
    md.append(f"- 50th percentile: **{s50:.3f}**")
    md.append(f"- 95th percentile: **{s95:.3f}**")
    md.append(f"- Actual: {summary['annualized_sharpe']['actual']:.3f}")
    (out_dir / "bootstrap_report.md").write_text("\n".join(md), encoding="utf-8")
    print(f"[bootstrap] 90% CI final value: ${p5:,.0f} .. ${p95:,.0f}, "
          f"actual=${summary['final_value']['actual']:,.0f}")


if __name__ == "__main__":
    main()
