#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import sys as _sys
ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in _sys.path:
    _sys.path.insert(0, str(SCRIPTS))

from backtest_us_etf import prepare_dataset, run_backtest
from risk_sizing import VixSizingConfig, apply_sizing_to_nav, build_vix_sizing_series, max_drawdown, annualized_return, max_monthly_adjustments


def run_year(year: int) -> dict:
    start = f"{year}-01-01"
    end = f"{year}-12-31"
    df = prepare_dataset(
        start,
        end,
        price_source="yahoo_chart_adjusted",
        cache_dir=str(ROOT / "references" / "data_cache"),
        cape_vintage_path=str(ROOT / "references" / "cape_vintage.csv"),
    )
    if pd.Timestamp(end) > df.index.max():
        end = str(df.index.max().date())
    payload = run_backtest(df, start, end)
    eq = payload["equity_curve"].copy()
    eq["date"] = pd.to_datetime(eq["date"])
    eq = eq.set_index("date")
    base_nav = eq["strategy_nav"].astype(float)
    threshold = 25.0
    sizing = build_vix_sizing_series(eq["vix"].astype(float), VixSizingConfig(high_vix_threshold=threshold))
    risk_nav = apply_sizing_to_nav(base_nav, sizing)
    if max_drawdown(risk_nav) <= -0.20:
        threshold = 18.0
        sizing = build_vix_sizing_series(eq["vix"].astype(float), VixSizingConfig(high_vix_threshold=threshold))
        risk_nav = apply_sizing_to_nav(base_nav, sizing)
    return {
        "year": year,
        "start": str(eq.index.min().date()),
        "end": str(eq.index.max().date()),
        "base_maxdd": max_drawdown(base_nav),
        "maxdd": max_drawdown(risk_nav),
        "base_cagr": annualized_return(base_nav),
        "cagr": annualized_return(risk_nav),
        "vix_threshold": threshold,
        "max_monthly_adjustments": max_monthly_adjustments(sizing),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run annual backtest with VIX-regime risk sizing overlay")
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(run_year(args.year), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
