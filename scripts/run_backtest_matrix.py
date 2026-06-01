#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

import sys as _sys
_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)

from backtest_us_etf import (
    prepare_dataset,
    run_backtest,
    write_outputs,
    parse_weekday,
)

WINDOWS = [
    {"name": "full_2006_2026", "start": "2006-01-01", "end": "2026-05-29"},
    {"name": "gfc_2008_2009", "start": "2008-01-01", "end": "2009-12-31"},
    {"name": "euro_debt_2011", "start": "2011-01-01", "end": "2011-12-31"},
    {"name": "q4_2018", "start": "2018-09-01", "end": "2018-12-31"},
    {"name": "covid_2020", "start": "2020-02-01", "end": "2020-06-30"},
    {"name": "bear_2022", "start": "2022-01-01", "end": "2022-12-31"},
    {"name": "recent_2023_2026", "start": "2023-05-29", "end": "2026-05-29"},
]


def run_matrix(
    windows: List[Dict[str, str]],
    output_base: Path,
    price_source: str = "nasdaq_price_return",
    cape_source: str = "yale_shiller",
    allow_price_return_fallback: bool = False,
    alpha_vantage_api_key: str = "",
    tiingo_api_key: str = "",
    cache_dir: str = "",
    require_adjusted: bool = False,
    cape_vintage_path: str = "",
    initial_capital: float = 100_000.0,
    weekly_budget: float = 2_000.0,
    transaction_cost: float = 0.0015,
    risk_free_rate: float = 0.0,
    contribution_weekday: int = 3,
    cape_lag_bdays: int = 10,
) -> List[Dict[str, Any]]:
    summaries = []

    for win in windows:
        name = win["name"]
        start = win["start"]
        end = win["end"]
        win_dir = output_base / name
        print(f"=== Running window: {name} ({start} -> {end}) ===")

        try:
            df = prepare_dataset(
                start, end,
                cape_lag_bdays=cape_lag_bdays,
                price_source=price_source,
                cape_source=cape_source,
                allow_price_return_fallback=allow_price_return_fallback,
                alpha_vantage_api_key=alpha_vantage_api_key or None,
                tiingo_api_key=tiingo_api_key or None,
                cache_dir=cache_dir or None,
                require_adjusted=require_adjusted,
                cape_vintage_path=cape_vintage_path or None,
            )
            if pd.Timestamp(end) > df.index.max():
                end = df.index.max().strftime("%Y-%m-%d")

            payload = run_backtest(
                df, start, end,
                initial_capital=initial_capital,
                weekly_budget=weekly_budget,
                transaction_cost=transaction_cost,
                contribution_weekday=contribution_weekday,
                risk_free_rate=risk_free_rate,
            )
            write_outputs(payload, win_dir, basename=name)

            result = payload["result"]
            summary = {
                "window": name,
                "start": result["meta"]["start"],
                "end": result["meta"]["end"],
                "trading_days": result["meta"]["trading_days"],
                "strategy_xirr": result["strategy"]["xirr"],
                "benchmark_xirr": result["benchmark"]["xirr"],
                "strategy_unitized_mdd": result["strategy"]["unitized_max_drawdown"],
                "benchmark_unitized_mdd": result["benchmark"]["unitized_max_drawdown"],
                "strategy_avg_cash_pct": result["strategy"]["avg_cash_pct"],
                "trade_count": result["strategy"]["trade_count"],
                "xirr_diff": result["relative"]["xirr_diff"],
                "mdd_diff": result["relative"]["max_drawdown_diff"],
                "adjusted_for_dividends": result["meta"]["adjusted_for_dividends"],
                "price_return_only": result["meta"]["price_return_only"],
                "price_source": result["meta"]["price_source"],
                "cape_source": result["meta"]["cape_source"],
                "status": "ok",
            }
            summaries.append(summary)
            print(f"  XIRR: strategy={result['strategy']['xirr']}, benchmark={result['benchmark']['xirr']}")
        except Exception as e:
            print(f"  FAILED: {e}")
            summaries.append({
                "window": name,
                "start": start,
                "end": end,
                "status": "failed",
                "error": str(e),
            })

    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtest across multiple time windows")
    parser.add_argument("--output-dir", default="references/backtest_matrix")
    parser.add_argument("--price-source", default="nasdaq_price_return")
    parser.add_argument("--cape-source", default="yale_shiller")
    parser.add_argument("--allow-price-return-fallback", action="store_true")
    parser.add_argument("--require-adjusted", action="store_true")
    parser.add_argument("--alpha-vantage-api-key", default="")
    parser.add_argument("--tiingo-api-key", default="")
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--cape-vintage-path", default="")
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--weekly-budget", type=float, default=2_000.0)
    parser.add_argument("--transaction-cost", type=float, default=0.0015)
    parser.add_argument("--risk-free-rate", type=float, default=0.0)
    parser.add_argument("--contribution-weekday", default="thursday")
    parser.add_argument("--cape-lag-bdays", type=int, default=10)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries = run_matrix(
        windows=WINDOWS,
        output_base=output_dir,
        price_source=args.price_source,
        cape_source=args.cape_source,
        allow_price_return_fallback=args.allow_price_return_fallback,
        alpha_vantage_api_key=args.alpha_vantage_api_key,
        tiingo_api_key=args.tiingo_api_key,
        cache_dir=args.cache_dir,
        require_adjusted=args.require_adjusted,
        cape_vintage_path=args.cape_vintage_path,
        initial_capital=args.initial_capital,
        weekly_budget=args.weekly_budget,
        transaction_cost=args.transaction_cost,
        risk_free_rate=args.risk_free_rate,
        contribution_weekday=parse_weekday(args.contribution_weekday),
        cape_lag_bdays=args.cape_lag_bdays,
    )

    (output_dir / "summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md_lines = ["# Backtest Matrix Summary", ""]
    md_lines.append("| Window | Start | End | Strategy XIRR | Benchmark XIRR | XIRR Diff | Strategy MDD | Benchmark MDD | Avg Cash | Trades | Status |")
    md_lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for s in summaries:
        xirr_s = f"{s['strategy_xirr']:.2%}" if s.get("strategy_xirr") is not None else "n/a"
        xirr_b = f"{s['benchmark_xirr']:.2%}" if s.get("benchmark_xirr") is not None else "n/a"
        xirr_d = f"{s['xirr_diff']:.2%}" if s.get("xirr_diff") is not None else "n/a"
        mdd_s = f"{s['strategy_unitized_mdd']:.2%}" if s.get("strategy_unitized_mdd") is not None else "n/a"
        mdd_b = f"{s['benchmark_unitized_mdd']:.2%}" if s.get("benchmark_unitized_mdd") is not None else "n/a"
        cash = f"{s['strategy_avg_cash_pct']:.1%}" if s.get("strategy_avg_cash_pct") is not None else "n/a"
        md_lines.append(
            f"| {s['window']} | {s.get('start', '')} | {s.get('end', '')} | "
            f"{xirr_s} | {xirr_b} | {xirr_d} | {mdd_s} | {mdd_b} | {cash} | "
            f"{s.get('trade_count', '')} | {s.get('status', '')} |"
        )

    (output_dir / "summary.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
