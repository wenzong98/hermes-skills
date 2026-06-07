"""Tests for scripts/vol_targeting.py — realized vol + scale.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))

from vol_targeting import (  # noqa: E402
    VolTargetConfig,
    apply_vol_targeting,
    realized_vol,
    vol_targeting_scale,
)


def test_realized_vol_annualizes_to_known_value():
    """Constant *return* of 0.01 daily → realized vol = std of returns = 0
    (no variation), but if we add a tiny noise we get the expected √252 scale.

    Use a long series of returns that has std exactly equal to 0.01.
    """
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.0, 0.01, 5000))
    rv = realized_vol(r, lookback=5000)
    assert abs(rv.iloc[-1] - 0.01 * math.sqrt(252)) < 0.001


def test_scale_under_low_vol_is_ceiling():
    """Very low realized vol → scale clipped to vol_ceiling."""
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.0, 0.0001, 5000))  # extremely low vol
    cfg = VolTargetConfig(target_vol=0.10, lookback=63, vol_floor=0.5, vol_ceiling=1.5)
    rv = realized_vol(r, lookback=63)
    scale = vol_targeting_scale(rv, cfg)
    valid = scale.dropna()
    assert (valid <= 1.5).all() and (valid >= 0.5).all()
    # In this regime the realized vol is so low that scale should be at the
    # ceiling.
    assert (valid == 1.5).mean() > 0.95


def test_scale_under_high_vol_is_floor():
    """Very high realized vol → scale clipped to vol_floor."""
    rng = np.random.default_rng(0)
    r = pd.Series(rng.normal(0.0, 0.10, 5000))  # 10% daily vol
    cfg = VolTargetConfig(target_vol=0.10, lookback=63, vol_floor=0.5, vol_ceiling=1.5)
    rv = realized_vol(r, lookback=63)
    scale = vol_targeting_scale(rv, cfg)
    valid = scale.dropna()
    assert (valid >= 0.5).all() and (valid <= 1.5).all()
    # In this regime the realized vol is so high that scale should be at the
    # floor.
    assert (valid == 0.5).mean() > 0.95


def test_apply_vol_targeting_composes():
    """effective = rule * scale, clipped to 0..3."""
    idx = pd.date_range("2020-01-01", periods=200, freq="B")
    df = pd.DataFrame({"spy_close": 100 * (1 + 0.01 * pd.Series(range(200))).values}, index=idx)
    rule = pd.Series(1.0, index=idx)
    cfg = VolTargetConfig(target_vol=0.10, lookback=63)
    eff, scale, rv = apply_vol_targeting(df, rule, cfg)
    valid = eff.dropna()
    assert (valid >= 0).all() and (valid <= 3.0).all()
    # Where scale == 1.0, eff should equal rule.
    no_scale = scale[~scale.isna() & (scale == 1.0)].index
    assert (eff.loc[no_scale] == rule.loc[no_scale]).all()
