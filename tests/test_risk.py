from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from risk_sizing import (
    VixSizingConfig,
    apply_sizing_to_nav,
    build_vix_sizing_series,
    max_drawdown,
    max_monthly_adjustments,
    throttle_monthly_adjustments,
    vix_regime_multiplier,
)


def test_vix_regime_default_threshold() -> None:
    assert vix_regime_multiplier(24.99) == 1.0
    assert vix_regime_multiplier(25.0) == 0.5
    assert vix_regime_multiplier(30.0) == 0.5


def test_monthly_adjustment_throttle_caps_changes() -> None:
    idx = pd.bdate_range("2024-01-01", "2024-01-31")
    pattern = ([1.0, 0.5, 1.0, 0.5, 1.0] * 5)[: len(idx)]
    raw = pd.Series(pattern, index=idx)
    throttled = throttle_monthly_adjustments(raw, max_monthly_adjustments=2)
    assert max_monthly_adjustments(throttled) <= 2


def test_apply_sizing_reduces_drawdown() -> None:
    idx = pd.bdate_range("2024-01-01", periods=5)
    nav = pd.Series([1.0, 0.9, 0.8, 0.82, 0.84], index=idx)
    sizing = pd.Series([0.5, 0.5, 0.5, 0.5, 0.5], index=idx)
    adjusted = apply_sizing_to_nav(nav, sizing)
    assert max_drawdown(adjusted) > max_drawdown(nav)


def test_build_vix_sizing_series_uses_config() -> None:
    idx = pd.bdate_range("2024-01-01", periods=4)
    vix = pd.Series([17.0, 18.0, 26.0, 16.0], index=idx)
    sizing = build_vix_sizing_series(vix, VixSizingConfig(high_vix_threshold=18.0))
    assert list(sizing.iloc[:3]) == [1.0, 0.5, 0.5]


def test_2022_backtest_maxdd_under_20pct() -> None:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "run_backtest.py"), "--year", "2022"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["maxdd"] > -0.20
    assert payload["max_monthly_adjustments"] <= 2


def test_2023_2024_bull_market_cagr_loss_under_3pct() -> None:
    for year in ("2023", "2024"):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "run_backtest.py"), "--year", year],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(proc.stdout)
        assert payload["base_cagr"] - payload["cagr"] < 0.03
