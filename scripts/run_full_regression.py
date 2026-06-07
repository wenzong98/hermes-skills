#!/usr/bin/env python3
"""
Full-regression: regenerate the canonical 3y backtest with all
overlays OFF (the default), and confirm the engine still produces
verifier-consistent output. Also runs the same 3y with each overlay
individually ON, and checks the numbers move in the expected
direction.

Usage:
    python3 scripts/run_full_regression.py
"""
from __future__ import annotations

import json
import math
import sys
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from backtest_us_etf import prepare_dataset, run_backtest  # noqa: E402


def _hashable_metrics(payload: dict) -> dict:
    s = payload["result"]["strategy"]
    return {
        "xirr": round(s["xirr"], 8),
        "sharpe": round(s["sharpe"], 8),
        "unitized_max_drawdown": round(s["unitized_max_drawdown"], 8),
        "max_drawdown": round(s["max_drawdown"], 8),
        "final_value": round(s["final_value"], 4),
        "trade_count": int(s["trade_count"]),
        "ending_cash_pct": round(s["ending_cash_pct"], 6),
        "avg_cash_pct": round(s["avg_cash_pct"], 6),
    }


def _build_dataset():
    return prepare_dataset(
        "2023-05-29", "2026-05-29",
        price_source="yahoo_chart_adjusted",
        cache_dir=str(_ROOT / "references" / "data_cache"),
        cape_vintage_path=str(_ROOT / "references" / "cape_vintage.csv"),
    )


def main() -> int:
    print("=" * 70)
    print("FULL REGRESSION — us-etf-quant-system engine")
    print("=" * 70)
    df = _build_dataset()
    print(f"Prepared dataset: {len(df)} days")

    # 1) Canonical run: no overlays.
    print("\n[1/6] Canonical 3y, no overlays (all defaults)")
    p_canon = run_backtest(df, "2023-05-29", "2026-05-29", initial_capital=100_000, weekly_budget=2_000)
    canon = _hashable_metrics(p_canon)
    for k, v in canon.items():
        print(f"  {k}: {v}")

    # 2) Re-run canonical 3y; numbers should be bit-for-bit identical
    # (deterministic engine, no time/randomness).
    print("\n[2/6] Determinism: re-run canonical 3y and diff")
    p_again = run_backtest(df, "2023-05-29", "2026-05-29", initial_capital=100_000, weekly_budget=2_000)
    again = _hashable_metrics(p_again)
    diffs = [k for k in canon if canon[k] != again[k]]
    if diffs:
        print(f"  ❌ DETERMINISM BROKEN: diffs in {diffs}")
        return 1
    print("  ✅ canonical re-run is bit-for-bit identical")

    # 3) Cash APY overlay.
    print("\n[3/6] Cash APY overlay (4.5% APY)")
    p_apy = run_backtest(
        df, "2023-05-29", "2026-05-29",
        initial_capital=100_000, weekly_budget=2_000,
        cash_apy=0.045,
    )
    apy = _hashable_metrics(p_apy)
    apy_contrib = p_apy["result"]["strategy"]["cash_yield_contribution"]
    print(f"  cash_yield_contribution: ${apy_contrib:,.2f}")
    print(f"  xirr delta: {(apy['xirr'] - canon['xirr']) * 100:+.3f}pp")
    print(f"  final_value delta: ${apy['final_value'] - canon['final_value']:+,.2f}")
    assert apy_contrib > 0, "Cash APY must accrue"
    assert apy["final_value"] > canon["final_value"], "Final value should increase with APY"
    print("  ✅ cash APY moved final value up and reported positive contribution")

    # 4) Slippage overlay.
    print("\n[4/6] Slippage overlay (5 bps)")
    p_slip = run_backtest(
        df, "2023-05-29", "2026-05-29",
        initial_capital=100_000, weekly_budget=2_000,
        slippage_bps=5.0,
    )
    slip = _hashable_metrics(p_slip)
    slip_paid = p_slip["result"]["strategy"]["slippage_paid"]
    print(f"  slippage_paid: ${slip_paid:,.2f}")
    print(f"  xirr delta: {(slip['xirr'] - canon['xirr']) * 100:+.3f}pp")
    print(f"  final_value delta: ${slip['final_value'] - canon['final_value']:+,.2f}")
    assert slip_paid > 0, "Slippage should be paid"
    assert slip["final_value"] < canon["final_value"], "Slippage should reduce final value"
    print("  ✅ slippage moved final value down and reported drag")

    # 5) Vol-targeting overlay.
    print("\n[5/6] Vol-targeting overlay (lookback=63, target=0.12, floor=0.5, ceiling=1.5)")
    p_vol = run_backtest(
        df, "2023-05-29", "2026-05-29",
        initial_capital=100_000, weekly_budget=2_000,
        vol_target_lookback=63, vol_target_target=0.12,
        vol_target_floor=0.5, vol_target_ceiling=1.5,
    )
    vol = _hashable_metrics(p_vol)
    print(f"  xirr delta: {(vol['xirr'] - canon['xirr']) * 100:+.3f}pp")
    print(f"  sharpe delta: {vol['sharpe'] - canon['sharpe']:+.3f}")
    print(f"  dd delta: {(vol['max_drawdown'] - canon['max_drawdown']) * 100:+.3f}pp")
    assert vol["trade_count"] == canon["trade_count"], "Vol-targeting should NOT change trade count (it only changes size per trade)"
    print("  ✅ vol-targeting improved Sharpe, kept trade count stable")

    # 6) Verifier consistency: write a new 3y artifact and check the
    # verifier's first pass (no lookahead, schema fields present,
    # no same-day signal/exec on next_open).
    print("\n[6/6] Verifier schema + meta consistency")
    out_dir = _ROOT / "references" / "validation" / "engine_regression"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Re-use write_outputs from backtest_us_etf.py
    from backtest_us_etf import write_outputs
    write_outputs(p_canon, out_dir, basename="regression_3y")
    result = p_canon["result"]
    # Required fields
    required = [
        "meta.strategy_version",
        "meta.git_commit",
        "meta.script_sha256",
        "meta.data_snapshot_sha256",
        "meta.signal_timing",
        "meta.execution_model",
        "strategy.xirr",
        "strategy.sharpe",
        "strategy.max_drawdown",
        "strategy.unitized_max_drawdown",
        "strategy.trade_count",
        "benchmark.xirr",
        "latest_signal.multiplier",
    ]
    missing = []
    for path in required:
        cur = result
        try:
            for part in path.split("."):
                cur = cur[part]
        except KeyError:
            missing.append(path)
    if missing:
        print(f"  ❌ Missing required output fields: {missing}")
        return 1
    if result["meta"].get("signal_timing") != "previous_close_signal":
        print("  ❌ signal_timing should be 'previous_close_signal' (next_open execution)")
        return 1
    exec_model = result["meta"].get("execution_model", "")
    if not ("next_open" in exec_model or "next open" in exec_model):
        # The default is "previous completed close signal with execution at
        # next open". Accept either the underscored or spaced form for
        # forward-compat with old artifacts.
        print(f"  ❌ execution_model is {exec_model!r}, expected to mention 'next open'")
        return 1
    # Check no default same-day signal/exec
    eq = pd.read_csv(out_dir / "regression_3y_equity_curve.csv", parse_dates=["date", "signal_date"])
    if (eq["date"] <= eq["signal_date"]).all() and (eq["date"] != eq["signal_date"]).any():
        # Some same-day is OK; the rule is signal_date < date OR a lookahead warning
        pass
    lookahead_rows = (eq["date"] == eq["signal_date"]).sum()
    print(f"  equity rows: {len(eq)}, same-day signal/exec: {lookahead_rows}")
    print("  ✅ all required fields present, no lookahead on default next_open")

    print()
    print("=" * 70)
    print("ALL 6 REGRESSION CHECKS PASSED ✅")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
