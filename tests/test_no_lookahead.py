from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backtest_us_etf.py"
SPEC = importlib.util.spec_from_file_location("backtest_us_etf", SCRIPT)
assert SPEC and SPEC.loader
bt = importlib.util.module_from_spec(SPEC)
sys.modules["backtest_us_etf"] = bt
SPEC.loader.exec_module(bt)


def synthetic_dataset() -> pd.DataFrame:
    idx = pd.to_datetime(["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"])
    df = pd.DataFrame(
        {
            "spy_open": [100.0, 101.0, 102.0, 103.0],
            "spy_close": [100.0, 110.0, 120.0, 130.0],
            "qqq_open": [200.0, 202.0, 204.0, 206.0],
            "qqq_close": [200.0, 220.0, 240.0, 260.0],
            "vix": [15.0, 15.0, 15.0, 15.0],
            "cape": [30.0, 45.0, 30.0, 30.0],
            "spy_sma50": [90.0, 90.0, 90.0, 90.0],
            "spy_sma200": [80.0, 80.0, 80.0, 80.0],
            "qqq_sma200": [180.0, 180.0, 180.0, 180.0],
            "spy_rsi14": [50.0, 80.0, 50.0, 50.0],
            "spy_ret_21d": [0.01, 0.01, 0.01, 0.01],
            "spy_ret_63d": [0.02, 0.02, 0.02, 0.02],
            "qqq_ret_63d": [0.03, 0.03, 0.03, 0.03],
            "spy_drawdown_252d": [0.0, 0.0, 0.0, 0.0],
            "vix_sma20": [15.0, 15.0, 15.0, 15.0],
            "qqq_rel_63d": [0.04, 0.04, 0.04, 0.04],
            "qqq_rel_126d": [0.04, 0.04, 0.04, 0.04],
            "trend_up": [True, True, True, True],
            "qqq_trend_up": [True, True, True, True],
            "trend_strong": [True, True, True, True],
            "risk_off": [False, False, False, False],
        },
        index=idx,
    )
    df.attrs["cape_lag_bdays"] = 10
    return df


def test_default_execution_uses_previous_signal_and_next_open() -> None:
    payload = bt.run_backtest(
        synthetic_dataset(),
        "2024-01-03",
        "2024-01-08",
        initial_capital=1_000.0,
        weekly_budget=100.0,
        transaction_cost=0.0,
    )

    first_trade = payload["trades"][0]
    assert first_trade["action"] == "INITIAL_ALLOC"
    assert first_trade["date"] == "2024-01-04"
    assert first_trade["signal_date"] == "2024-01-03"
    assert first_trade["spy_trade_price"] == 101.0
    assert payload["result"]["meta"]["signal_timing"] == "previous_close_signal"
    assert payload["result"]["meta"]["execution_price"] == "next_open"
    assert payload["result"]["meta"]["lookahead_warning"] is None

    for trade in payload["trades"]:
        assert trade["signal_date"] < trade["date"]


def test_same_close_mode_is_explicitly_marked_as_lookahead() -> None:
    payload = bt.run_backtest(
        synthetic_dataset(),
        "2024-01-03",
        "2024-01-08",
        initial_capital=1_000.0,
        weekly_budget=100.0,
        transaction_cost=0.0,
        execution_price="same_close",
    )

    first_trade = payload["trades"][0]
    assert first_trade["date"] == "2024-01-03"
    assert first_trade["signal_date"] == "2024-01-03"
    assert first_trade["spy_trade_price"] == 100.0
    assert payload["result"]["meta"]["signal_timing"] == "same_close_signal"
    assert payload["result"]["meta"]["lookahead_warning"]
