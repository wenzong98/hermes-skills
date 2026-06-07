#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List

import numpy as np
import pandas as pd

import sys as _sys
_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)

from backtest_us_etf import prepare_dataset, rsi_wilder
from optimize_params import performance_metrics


FACTOR_NAMES = ["volume_ratio", "ATR_pct", "MA_cross", "VIX_regime", "breadth_thrust"]


def compute_factors(df: pd.DataFrame) -> pd.DataFrame:
    spy_close = df["spy_close"].astype(float)
    qqq_close = df["qqq_close"].astype(float)
    high = df[["spy_high", "qqq_high"]].mean(axis=1)
    low = df[["spy_low", "qqq_low"]].mean(axis=1)
    close = df[["spy_close", "qqq_close"]].mean(axis=1)
    prev_close = close.shift(1)
    true_range = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)

    avg_volume = df[["spy_volume", "qqq_volume"]].mean(axis=1)
    both_up = ((spy_close.pct_change() > 0).astype(float) + (qqq_close.pct_change() > 0).astype(float)) / 2.0

    raw = pd.DataFrame(index=df.index)
    raw["volume_ratio"] = avg_volume / avg_volume.rolling(20).mean() - 1.0
    raw["ATR_pct"] = -(true_range.rolling(14).mean() / close)
    raw["MA_cross"] = close.rolling(20).mean() / close.rolling(50).mean() - 1.0
    raw["VIX_regime"] = -(df["vix"].astype(float) / df["vix"].astype(float).rolling(20).mean() - 1.0)
    raw["breadth_thrust"] = both_up.rolling(10).mean() - 0.5

    # Shift every factor one full trading day before any target return is built.
    shifted = raw.shift(1)
    shifted.attrs["lookahead_safe_shift"] = 1
    return shifted


def forward_return_target(df: pd.DataFrame, horizon: int = 5) -> pd.Series:
    basket = df[["spy_close", "qqq_close"]].mean(axis=1).astype(float)
    return basket.shift(-horizon) / basket - 1.0


def information_stats(signal: pd.Series, target: pd.Series) -> Dict[str, float]:
    joined = pd.concat([signal, target], axis=1).dropna()
    if joined.empty:
        return {"IC": 0.0, "IR": 0.0, "IC_IR": 0.0, "decay": 0.0}
    joined.columns = ["signal", "target"]
    daily_ic = joined["signal"].rolling(63).corr(joined["target"]).dropna()
    ic = float(joined["signal"].corr(joined["target"]))
    ir = float(daily_ic.mean() / daily_ic.std()) if len(daily_ic) > 1 and daily_ic.std() > 0 else 0.0
    decay = float(joined["signal"].autocorr(lag=5)) if len(joined) > 10 else 0.0
    return {"IC": ic, "IR": ir, "IC_IR": ir, "decay": decay}


def zscore(frame: pd.DataFrame) -> pd.DataFrame:
    return (frame - frame.rolling(252, min_periods=60).mean()) / frame.rolling(252, min_periods=60).std()


def multifactor_signal(factors: pd.DataFrame, directions: Dict[str, float] | None = None) -> pd.Series:
    normalized = zscore(factors[FACTOR_NAMES]).replace([np.inf, -np.inf], np.nan)
    if directions:
        for name, direction in directions.items():
            if name in normalized:
                normalized[name] = normalized[name] * float(direction)
    signal = normalized.mean(axis=1).shift(0)
    signal.name = "multi_factor"
    return signal


def rsi_single_factor(df: pd.DataFrame) -> pd.Series:
    basket = df[["spy_close", "qqq_close"]].mean(axis=1).astype(float)
    return (50.0 - rsi_wilder(basket, 14)).shift(1) / 50.0


def signal_nav(df: pd.DataFrame, signal: pd.Series) -> pd.Series:
    basket_ret = df[["spy_close", "qqq_close"]].mean(axis=1).pct_change().fillna(0.0)
    exposure = signal.reindex(df.index).shift(1)
    exposure = exposure.rolling(5, min_periods=1).mean()
    # Long/cash research overlay: positive signal is invested, negative signal
    # moves to cash. This keeps the comparison simple and avoids leverage.
    exposure = pd.Series(np.where(exposure > 0, 1.0, 0.0), index=df.index).fillna(0.0)
    nav = (1.0 + basket_ret * exposure).cumprod()
    nav.name = signal.name or "signal_nav"
    return nav


def build_report(df: pd.DataFrame) -> Dict:
    factors = compute_factors(df)
    target = forward_return_target(df)
    oos_mask = df.index >= pd.Timestamp("2022-01-01")
    ins_mask = df.index < pd.Timestamp("2022-01-01")
    directions = {}
    for name in FACTOR_NAMES:
        ic = information_stats(factors[name].loc[ins_mask], target.loc[ins_mask])["IC"]
        directions[name] = 1.0 if ic >= 0 else -1.0
    factor_stats = {name: information_stats(factors[name].loc[oos_mask], target.loc[oos_mask]) for name in FACTOR_NAMES}
    multi = multifactor_signal(factors, directions)
    multi_stats = information_stats(multi.loc[oos_mask], target.loc[oos_mask])
    rsi = rsi_single_factor(df)

    multi_nav = signal_nav(df, multi).loc[oos_mask]
    rsi_nav = signal_nav(df, rsi.rename("rsi14")).loc[oos_mask]
    if not multi_nav.empty:
        multi_nav = multi_nav / multi_nav.iloc[0]
    if not rsi_nav.empty:
        rsi_nav = rsi_nav / rsi_nav.iloc[0]
    multi_perf = performance_metrics(multi_nav)
    rsi_perf = performance_metrics(rsi_nav)
    improvement = (
        (multi_perf["sharpe"] - rsi_perf["sharpe"]) / abs(rsi_perf["sharpe"])
        if rsi_perf["sharpe"] else 0.0
    )

    return {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "sample_out_start": "2022-01-01",
        "lookahead_safe_shift": int(factors.attrs.get("lookahead_safe_shift", 0)),
        "in_sample_factor_directions": directions,
        "factor_stats": factor_stats,
        "multi_factor": multi_stats,
        "rsi_baseline": {"Sharpe": rsi_perf["sharpe"]},
        "multi_factor_performance": {"Sharpe": multi_perf["sharpe"]},
        "sharpe_improvement_pct": improvement * 100.0,
    }


def write_markdown(payload: Dict, output: Path) -> None:
    lines = [
        "# Factor Signal Report",
        "",
        f"Generated at: `{payload['generated_at']}`",
        f"Sample-out start: `{payload['sample_out_start']}`",
        f"Lookahead-safe factor shift: `{payload['lookahead_safe_shift']}` trading day",
        "",
        "| Factor | IC | IR | IC_IR | decay |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, stats in payload["factor_stats"].items():
        lines.append(f"| {name} | {stats['IC']:.4f} | {stats['IR']:.4f} | {stats['IC_IR']:.4f} | {stats['decay']:.4f} |")
    mf = payload["multi_factor"]
    lines += [
        "",
        "## Equal-Weight Composite",
        f"- IC: {mf['IC']:.4f}",
        f"- IC_IR: {mf['IC_IR']:.4f}",
        f"- RSI baseline Sharpe: {payload['rsi_baseline']['Sharpe']:.4f}",
        f"- Multi-factor Sharpe: {payload['multi_factor_performance']['Sharpe']:.4f}",
        f"- Sharpe improvement: {payload['sharpe_improvement_pct']:.2f}%",
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_default_dataset(start: str, end: str) -> pd.DataFrame:
    root = Path(__file__).resolve().parents[1]
    return prepare_dataset(
        start,
        end,
        price_source="yahoo_chart_adjusted",
        cache_dir=str(root / "references" / "data_cache"),
        cape_vintage_path=str(root / "references" / "cape_vintage.csv"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and evaluate SPY/QQQ factor signals")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--output", default="signals/factor_report.md")
    parser.add_argument("--json-output", default="signals/factor_report.json")
    args = parser.parse_args()

    df = load_default_dataset(args.start, args.end)
    payload = build_report(df)
    write_markdown(payload, Path(args.output))
    Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"IC_IR": payload["multi_factor"]["IC_IR"], "sharpe_improvement_pct": payload["sharpe_improvement_pct"]}, indent=2))


if __name__ == "__main__":
    main()
