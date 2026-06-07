from __future__ import annotations

import pandas as pd


def test_strategy_momentum_spy_exposes_default_params_and_grid() -> None:
    from strategy.momentum_spy import DEFAULT_PARAMS, PARAM_GRID

    assert DEFAULT_PARAMS.lookback == 20
    assert DEFAULT_PARAMS.threshold == 0.02
    assert DEFAULT_PARAMS.holding == 5
    assert len(PARAM_GRID["lookback"]) * len(PARAM_GRID["threshold"]) * len(PARAM_GRID["holding"]) >= 200


def test_risk_position_sizer_exposes_vix_sizing() -> None:
    from risk.position_sizer import size_for_vix, sizing_series

    assert size_for_vix(10.0) == 1.0
    assert size_for_vix(30.0) == 0.5
    idx = pd.bdate_range("2024-01-01", periods=3)
    out = sizing_series(pd.Series([10.0, 30.0, 10.0], index=idx))
    assert list(out) == [1.0, 0.5, 1.0]


def test_signals_multi_factor_exposes_factor_names() -> None:
    from signals.multi_factor import FACTOR_NAMES

    assert set(FACTOR_NAMES) == {"volume_ratio", "ATR_pct", "MA_cross", "VIX_regime", "breadth_thrust"}
