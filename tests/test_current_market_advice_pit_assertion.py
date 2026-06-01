from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest


def _import_advice():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import current_market_advice  # noqa: E402
    return current_market_advice


def _make_df(latest_used_cape_available_at: str = "") -> pd.DataFrame:
    idx = pd.bdate_range("2024-05-01", "2024-05-10")
    df = pd.DataFrame({
        "spy_close": [100.0] * len(idx),
        "qqq_close": [100.0] * len(idx),
        "spy_sma50": [100.0] * len(idx),
        "spy_sma200": [100.0] * len(idx),
        "spy_rsi14": [50.0] * len(idx),
        "spy_ret_21d": [0.0] * len(idx),
        "spy_drawdown_252d": [0.0] * len(idx),
        "vix": [20.0] * len(idx),
        "vix_sma20": [20.0] * len(idx),
        "cape": [32.0] * len(idx),
        "qqq_rel_63d": [0.0] * len(idx),
        "qqq_rel_126d": [0.0] * len(idx),
        "qqq_trend_up": [True] * len(idx),
        "trend_up": [True] * len(idx),
        "trend_strong": [True] * len(idx),
        "risk_off": [False] * len(idx),
    }, index=idx)
    df.attrs["latest_used_cape_available_at"] = latest_used_cape_available_at
    df.attrs["latest_used_cape_observation_month"] = "2024-04-01"
    df.attrs["cape_vintage_path"] = "/tmp/fake_vintage.csv"
    df.attrs["price_source"] = "yahoo_chart_adjusted"
    df.attrs["actual_price_source_spy"] = "yahoo_chart_adjusted"
    df.attrs["actual_price_source_qqq"] = "yahoo_chart_adjusted"
    df.attrs["cape_source"] = "yale_shiller_vintage"
    df.attrs["adjusted_for_dividends"] = True
    df.attrs["price_return_only"] = False
    return df


def test_assert_latest_cape_pit_raises_on_future_dated_cape() -> None:
    mod = _import_advice()
    df = _make_df(latest_used_cape_available_at="2024-05-20")
    with pytest.raises(AssertionError, match="CAPE PIT violation"):
        mod.assert_latest_cape_pit(df, pd.Timestamp("2024-05-15"))


def test_assert_latest_cape_pit_passes_on_pit_correct_cape() -> None:
    mod = _import_advice()
    df = _make_df(latest_used_cape_available_at="2024-05-10")
    # Should not raise.
    mod.assert_latest_cape_pit(df, pd.Timestamp("2024-05-15"))


def test_assert_latest_cape_pit_is_noop_when_no_cape_used() -> None:
    mod = _import_advice()
    df = _make_df(latest_used_cape_available_at="")
    # No CAPE in attrs means we have nothing to check; should not raise.
    mod.assert_latest_cape_pit(df, pd.Timestamp("2024-05-15"))


def test_build_payload_aborts_on_future_dated_cape() -> None:
    """Integration: build_payload should refuse to emit advice when the chosen
    CAPE was not actually available at the latest market close."""
    mod = _import_advice()
    df = _make_df(latest_used_cape_available_at="2024-05-20")
    args = MagicMock()
    args.skill_dir = str(Path(__file__).resolve().parent.parent)
    args.start = "2024-05-01"
    args.end = "2024-05-15"
    args.cape_lag_bdays = 10
    args.price_source = "yahoo_chart_adjusted"
    args.cape_source = "yale_shiller"
    args.allow_price_return_fallback = False
    args.alpha_vantage_api_key = None
    args.tiingo_api_key = None
    args.cache_dir = None
    args.require_adjusted = False
    args.cape_vintage_path = "/tmp/fake_vintage.csv"
    args.portfolio_config = None
    args.weekly_budget = None
    args.initial_capital = 100_000.0
    args.transaction_cost = 0.0015
    args.trim_state_file = "/tmp/fake_trim_state.json"
    args.record_trim_execution = False
    args.recent_days = 3
    args.model_cash_reservoir = True

    fake_bt = MagicMock()
    fake_bt.prepare_dataset.return_value = df
    with patch.object(mod, "load_backtest_module", return_value=fake_bt):
        with pytest.raises(AssertionError, match="CAPE PIT violation"):
            mod.build_payload(args)
