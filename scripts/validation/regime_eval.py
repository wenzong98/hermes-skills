#!/usr/bin/env python3
"""
Regime-aware evaluation for the US ETF DCA backtest.

Splits a backtest equity curve into named market regimes and reports
per-regime XIRR / unitized max DD / Sharpe / cash statistics. This
catches the failure mode where one big bull run inflates the
aggregate metrics and hides regime-specific underperformance.

Regime windows
--------------
The defaults are the well-known US-equity regime breaks 2020-2026:

- 2020-01-02 → 2020-03-23  COVID crash
- 2020-03-24 → 2021-12-31  Post-COVID melt-up
- 2022-01-01 → 2022-10-12  Bear market / hiking cycle
- 2022-10-13 → 2023-12-31  2023 rebound
- 2024-01-01 → 2025-12-31  AI rally (capped at run end)
- The remainder is bucketed as 'other'.

Override via --regime-json for custom windows.

Outputs (under references/validation/regime_eval/):
  regime_report.md
  regime_report.json
  regime_equity.csv — daily equity with `regime` column
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from backtest_us_etf import prepare_dataset, run_backtest, xirr, max_drawdown  # noqa: E402

DEFAULT_REGIMES: List[Tuple[str, str, str]] = [
    # (name, start, end) inclusive
    ("covid_crash", "2020-01-02", "2020-03-23"),
    ("post_covid_melt_up", "2020-03-24", "2021-12-31"),
    ("bear_2022", "2022-01-01", "2022-10-12"),
    ("rebound_2023", "2022-10-13", "2023-12-31"),
    ("ai_rally_2024_2025", "2024-01-01", "2026-05-29"),
]


def tag_regimes(eq: pd.DataFrame, regimes: List[Tuple[str, str, str]]) -> pd.Series:
    """Return a Series aligned to `eq.index` with the regime name for
    each row, or '' for rows outside any regime window."""
    tag = pd.Series("", index=eq.index, dtype=object)
    for name, start, end in regimes:
        s = pd.Timestamp(start)
        e = pd.Timestamp(end)
        in_window = (eq.index >= s) & (eq.index <= e)
        tag = tag.mask(in_window, name)
    return tag


def per_regime_metrics(
    eq: pd.DataFrame, tag: pd.Series
) -> List[Dict]:
    out: List[Dict] = []
    for regime in sorted(tag.unique()):
        if not regime:
            continue
        mask = tag == regime
        sub = eq[mask].copy()
        if sub.empty:
            continue
        nav = sub["strategy_nav"].astype(float)
        bench = sub["benchmark_nav"].astype(float)
        cash = sub.get("cash", pd.Series(0.0, index=sub.index)).astype(float)
        # Per-regime cumulative return
        ret = float(nav.iloc[-1] / nav.iloc[0] - 1) if nav.iloc[0] > 0 else float("nan")
        bench_ret = float(bench.iloc[-1] / bench.iloc[0] - 1) if bench.iloc[0] > 0 else float("nan")
        # Per-regime Sharpe (annualized)
        rets = nav.pct_change().dropna()
        if len(rets) > 1 and rets.std(ddof=1) > 0:
            sr = float(rets.mean() / rets.std(ddof=1) * math.sqrt(252))
        else:
            sr = float("nan")
        # Per-regime unitized max DD
        mdd = float(max_drawdown(nav))
        # Cash trajectory
        avg_cash = float(cash.mean()) if not cash.empty else 0.0
        out.append(
            {
                "regime": regime,
                "start": str(sub.index[0].date()),
                "end": str(sub.index[-1].date()),
                "n_days": int(len(sub)),
                "strategy_cum_return": ret,
                "benchmark_cum_return": bench_ret,
                "alpha_vs_bench": (
                    ret - bench_ret
                    if not (math.isnan(ret) or math.isnan(bench_ret))
                    else float("nan")
                ),
                "annualized_sharpe": sr,
                "unitized_max_dd": mdd,
                "avg_cash_pct": avg_cash,
            }
        )
    return out


def write_report(
    metrics: List[Dict],
    eq_with_regime: pd.DataFrame,
    aggregate: Dict,
    out_dir: Path,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # Markdown
    md = ["# Regime-aware Backtest Evaluation", ""]
    md.append(f"Window: {eq_with_regime.index[0].date()} → {eq_with_regime.index[-1].date()}")
    md.append(f"Total trading days: **{len(eq_with_regime)}**")
    md.append("")
    md.append("## Aggregate (full window)")
    md.append("")
    md.append(f"- Strategy XIRR: **{aggregate['xirr']:.2%}**")
    md.append(f"- Strategy final value: ${aggregate['final_value']:,.0f}")
    md.append(f"- Benchmark XIRR: {aggregate['bench_xirr']:.2%}")
    md.append(f"- Strategy unitized max DD: {aggregate['max_dd']:.2%}")
    md.append("")
    md.append("## Per-regime breakdown")
    md.append("")
    md.append("| Regime | Days | Strat cum ret | Bench cum ret | Alpha | Sharpe | Max DD | Avg cash |")
    md.append("|---|---|---|---|---|---|---|---|")
    for m in metrics:
        alpha = "—" if math.isnan(m["alpha_vs_bench"]) else f"{m['alpha_vs_bench']:+.2%}"
        sr = "—" if math.isnan(m["annualized_sharpe"]) else f"{m['annualized_sharpe']:.2f}"
        md.append(
            f"| {m['regime']} | {m['n_days']} | "
            f"{m['strategy_cum_return']:+.2%} | {m['benchmark_cum_return']:+.2%} | "
            f"{alpha} | {sr} | {m['unitized_max_dd']:.2%} | {m['avg_cash_pct']:.2%} |"
        )
    md.append("")
    md.append("## Interpretation")
    md.append("")
    md.append(
        "A regime table that shows the strategy **trailing the benchmark in "
        "bull regimes but leading in bear regimes** is the expected behavior "
        "of a risk-aware DCA. A regime table that shows the strategy "
        "**trailing the benchmark in *all* regimes** is a sign the rule is "
        "broken or the trial grid is overfit to one regime."
    )
    md_path = out_dir / "regime_report.md"
    md_path.write_text("\n".join(md), encoding="utf-8")

    # JSON
    json_path = out_dir / "regime_report.json"
    json_path.write_text(
        json.dumps({"aggregate": aggregate, "per_regime": metrics}, indent=2),
        encoding="utf-8",
    )

    # Equity curve with regime tag
    eq_path = out_dir / "regime_equity.csv"
    eq_with_regime.to_csv(eq_path, index_label="date")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=str, default="2020-01-02")
    parser.add_argument("--end", type=str, default="2026-05-29")
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--weekly-budget", type=float, default=2_000.0)
    parser.add_argument(
        "--price-source",
        type=str,
        default="yahoo_chart_adjusted",
    )
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
        "--regime-json",
        type=str,
        default=None,
        help="Optional JSON file with custom regime windows.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(_ROOT / "references" / "validation" / "regime_eval"),
    )
    args = parser.parse_args()

    if args.regime_json:
        raw = json.loads(Path(args.regime_json).read_text())
        regimes = [(r["name"], r["start"], r["end"]) for r in raw]
    else:
        regimes = DEFAULT_REGIMES

    df = prepare_dataset(
        args.start,
        args.end,
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

    tag = tag_regimes(eq, regimes)
    eq["regime"] = tag
    metrics = per_regime_metrics(eq, tag)

    # Aggregate (full window)
    nav = eq["strategy_nav"].astype(float)
    bench = eq["benchmark_nav"].astype(float)
    flows = eq.get("strategy_flow", pd.Series(0.0, index=eq.index)).astype(float)
    bench_flows = eq.get("benchmark_flow", pd.Series(0.0, index=eq.index)).astype(float)
    strategy_cfs = [
        (pd.Timestamp(d), -float(f))
        for d, f in zip(eq.index, flows)
        if float(f) > 0
    ]
    bench_cfs = [
        (pd.Timestamp(d), -float(f))
        for d, f in zip(eq.index, bench_flows)
        if float(f) > 0
    ]
    strategy_cfs.insert(0, (eq.index[0], -args.initial_capital))
    bench_cfs.insert(0, (eq.index[0], -args.initial_capital))
    strategy_cfs.append((eq.index[-1], float(nav.iloc[-1])))
    bench_cfs.append((eq.index[-1], float(bench.iloc[-1])))
    aggregate = {
        "xirr": xirr(strategy_cfs) or 0.0,
        "bench_xirr": xirr(bench_cfs) or 0.0,
        "final_value": float(nav.iloc[-1]),
        "max_dd": float(max_drawdown(nav)),
    }

    out_dir = Path(args.output_dir)
    write_report(metrics, eq, aggregate, out_dir)
    print(f"[regime] report written to {out_dir}", flush=True)
    for m in metrics:
        print(
            f"  {m['regime']:>20}: ret={m['strategy_cum_return']:+.2%}  "
            f"alpha={m['alpha_vs_bench']:+.2%}  "
            f"sharpe={m['annualized_sharpe']:>5.2f}  "
            f"dd={m['unitized_max_dd']:+.2%}"
        )


if __name__ == "__main__":
    main()
