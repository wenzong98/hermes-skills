#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

import sys as _sys
_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)

from backtest_us_etf import prepare_dataset
from factor_signals import compute_factors, multifactor_signal, forward_return_target, information_stats, FACTOR_NAMES
from optimize_params import ParamSet, momentum_overlay_nav, performance_metrics
from risk_sizing import max_drawdown


def extra_metrics(nav: pd.Series) -> Dict[str, float]:
    nav = nav.astype(float).dropna()
    ret = nav.pct_change().dropna()
    base = performance_metrics(nav)
    downside = ret[ret < 0]
    downside_vol = float(downside.std() * np.sqrt(252)) if len(downside) > 1 else 0.0
    sortino = float(ret.mean() * 252 / downside_vol) if downside_vol > 0 else 0.0
    calmar = float(base["cagr"] / abs(base["maxdd"])) if base["maxdd"] < 0 else 0.0
    return {**base, "sortino": sortino, "calmar": calmar}


def rebalance_nav(df: pd.DataFrame, desired: pd.Series, interval: int) -> pd.Series:
    spy_ret = df["spy_close"].pct_change().fillna(0.0)
    qqq_ret = df["qqq_close"].pct_change().fillna(0.0)
    current = "SPY"
    next_rebalance = 0
    positions = []
    for i, dt in enumerate(df.index):
        if i >= next_rebalance:
            current = str(desired.reindex(df.index).ffill().iloc[i])
            next_rebalance = i + interval
        positions.append(current)
    pos = pd.Series(positions, index=df.index).shift(1).fillna("SPY")
    daily = pd.Series(0.0, index=df.index)
    daily.loc[pos == "SPY"] = spy_ret.loc[pos == "SPY"]
    daily.loc[pos == "QQQ"] = qqq_ret.loc[pos == "QQQ"]
    daily.loc[pos == "BASKET"] = 0.5 * spy_ret.loc[pos == "BASKET"] + 0.5 * qqq_ret.loc[pos == "BASKET"]
    nav = (1.0 + daily).cumprod()
    return nav


def variant_nav(df: pd.DataFrame, name: str) -> pd.Series:
    spy_mom = df["spy_close"].pct_change(63)
    qqq_mom = df["qqq_close"].pct_change(63)
    rel = qqq_mom - spy_mom
    if name == "variant_a":
        desired = pd.Series(np.where(rel > 0, "QQQ", "SPY"), index=df.index)
        return rebalance_nav(df, desired, 21)
    if name == "variant_b":
        desired = pd.Series(np.where((rel > 0) & (df["vix"] < 25), "QQQ", "SPY"), index=df.index)
        return rebalance_nav(df, desired, 21)
    if name == "variant_c":
        trend = df[["spy_close", "qqq_close"]].mean(axis=1) > df[["spy_close", "qqq_close"]].mean(axis=1).rolling(100).mean()
        desired = pd.Series(np.where(~trend, "SPY", np.where(rel > 0, "QQQ", "SPY")), index=df.index)
        return rebalance_nav(df, desired, 5)
    if name == "variant_d":
        factors = compute_factors(df)
        target = forward_return_target(df)
        ins = df.index < pd.Timestamp("2022-01-01")
        directions = {factor: (1.0 if information_stats(factors[factor].loc[ins], target.loc[ins])["IC"] >= 0 else -1.0) for factor in FACTOR_NAMES}
        signal = multifactor_signal(factors, directions)
        desired = pd.Series(np.where(signal > 0, "QQQ", "SPY"), index=df.index)
        return rebalance_nav(df, desired, 10)
    raise ValueError(f"unknown variant: {name}")


def run_variant(df: pd.DataFrame, name: str, output_dir: Path) -> Dict:
    nav = variant_nav(df, name)
    nav = nav.loc[df.index >= pd.Timestamp("2015-01-01")]
    nav = nav / nav.iloc[0]
    metrics = extra_metrics(nav)
    out = output_dir / name
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": nav.index, "nav": nav.values}).to_csv(out / "equity_curve.csv", index=False)
    payload = {"variant": name, **metrics}
    (out / "metrics.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def write_html(summary: pd.DataFrame, output: Path) -> None:
    rows = []
    for _, row in summary.iterrows():
        rows.append(
            "<tr>"
            f"<td>{row['variant']}</td><td>{row['sharpe']:.3f}</td><td>{row['maxdd']:.2%}</td>"
            f"<td>{row['cagr']:.2%}</td><td>{row['sortino']:.3f}</td><td>{row['calmar']:.3f}</td>"
            "</tr>"
        )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Strategy Variant Comparison</title>
<style>body{{font-family:Arial,sans-serif;margin:32px}}table{{border-collapse:collapse}}td,th{{border:1px solid #ddd;padding:6px 9px}}th{{background:#f5f5f5}}</style>
</head><body><h1>SPY/QQQ Strategy Variant Comparison</h1>
<table><thead><tr><th>Variant</th><th>Sharpe</th><th>MaxDD</th><th>CAGR</th><th>Sortino</th><th>Calmar</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></body></html>
"""
    output.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SPY/QQQ strategy variants in parallel")
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    df = prepare_dataset(
        args.start,
        args.end,
        price_source="yahoo_chart_adjusted",
        cache_dir=str(root / "references" / "data_cache"),
        cape_vintage_path=str(root / "references" / "cape_vintage.csv"),
    )
    out = Path(args.output_dir)
    variants = ["variant_a", "variant_b", "variant_c", "variant_d"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda v: run_variant(df, v, out), variants))
    summary = pd.DataFrame(results)
    summary.to_csv(out / "comparison_table.csv", index=False)
    write_html(summary, out / "comparison_report.html")
    print(summary[["variant", "sharpe", "maxdd", "cagr", "sortino", "calmar"]].to_json(orient="records", indent=2))


if __name__ == "__main__":
    main()
