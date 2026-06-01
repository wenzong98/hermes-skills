from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backtest_us_etf.py"
SPEC = importlib.util.spec_from_file_location("backtest_us_etf_metrics", SCRIPT)
assert SPEC and SPEC.loader
bt = importlib.util.module_from_spec(SPEC)
sys.modules["backtest_us_etf_metrics"] = bt
SPEC.loader.exec_module(bt)


def test_unitized_nav_removes_external_flow_effect() -> None:
    dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"])
    values = pd.Series([100.0, 200.0, 210.0], index=dates)
    flows = pd.Series([0.0, 100.0, 0.0], index=dates)

    nav = bt.unitized_nav(values, flows)

    assert nav.iloc[0] == 1.0
    assert nav.iloc[1] == 1.0
    assert round(float(nav.iloc[2]), 6) == 1.05


def test_parse_weekday_accepts_names_and_rejects_weekend() -> None:
    assert bt.parse_weekday("thursday") == 3
    assert bt.parse_weekday("4") == 4
    try:
        bt.parse_weekday("5")
    except ValueError:
        pass
    else:
        raise AssertionError("weekday 5 should be rejected for this weekly schedule")
