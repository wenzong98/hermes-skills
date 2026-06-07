#!/usr/bin/env python3
"""
Vol-targeting smoothing layer for the US ETF DCA system.

Wraps the discrete 0x-3x DCA multiplier with a continuous
realized-volatility adjustment in [vol_floor, vol_ceiling]. The idea
is from Lean's Mean-VarianceOptimizationPortfolioConstructionModel
and Riskfolio-Lib's volatility-targeting primitive:

  realized_vol = std(returns, lookback) * sqrt(252)
  scale = clip(target_vol / realized_vol, vol_floor, vol_ceiling)
  effective_multiplier = rule_multiplier * scale

We *multiply* (not replace) the rule so the discrete CAPE bands
remain the dominant control. The smooth layer only attenuates or
amplifies in unusually quiet or noisy windows.

Why not full riskfolio? riskfolio-lib requires cvxpy (not installed)
and solves a convex optimization per rebalance. We don't need that
here — a 1-asset 1-target-vol scalar solves in closed form. This
module can be swapped for riskfolio.riskfolio.Portfolio(method="...",
asset_returns=df, ...).volatility_optimization(...) without
changing the call sites.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VolTargetConfig:
    """Vol-targeting config — matches the Riskfolio-Lib convention.

    target_vol
        Annualized volatility the system tries to keep realized vol
        near. Default 0.12 (12% ann) is a common "balanced" target.
    lookback
        Realized-vol window in trading days. 63 = quarterly, 21 =
        monthly.
    vol_floor
        Minimum scaling factor (caps the multiplier from going too
        high in a quiet market). 0.5 = never buy more than 0.5x above
        the rule.
    vol_ceiling
        Maximum scaling factor. 1.5 = can buy up to 1.5x in a
        panic. Set to 1.0 to disable amplification.
    min_periods
        Minimum non-NaN observations to trust the realized vol.
        Below this we return scale=1.0 (no adjustment).
    """
    target_vol: float = 0.12
    lookback: int = 63
    vol_floor: float = 0.5
    vol_ceiling: float = 1.5
    min_periods: int = 21


def realized_vol(returns: pd.Series, lookback: int, periods_per_year: int = 252) -> pd.Series:
    """Rolling annualized realized vol. NaN-padded until lookback."""
    r = returns.astype(float)
    return r.rolling(window=lookback, min_periods=lookback).std(ddof=1) * math.sqrt(periods_per_year)


def vol_targeting_scale(
    realized: pd.Series,
    config: VolTargetConfig | None = None,
) -> pd.Series:
    """Map realized vol → scale in [vol_floor, vol_ceiling]."""
    cfg = config or VolTargetConfig()
    out = pd.Series(1.0, index=realized.index, dtype=float)
    valid = realized.notna() & (realized > 0)
    raw = cfg.target_vol / realized[valid]
    clipped = raw.clip(lower=cfg.vol_floor, upper=cfg.vol_ceiling)
    out.loc[valid] = clipped
    return out


def apply_vol_targeting(
    df: pd.DataFrame,
    rule_multiplier: pd.Series,
    config: VolTargetConfig | None = None,
    return_col: str = "spy_close",
    periods_per_year: int = 252,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Given a backtest DataFrame and the rule-driven discrete
    multiplier, return (effective_multiplier, scale, realized_vol).

    `df` must contain the price column the vol is computed from.
    For the production system the natural choice is the *blended*
    SPY+QQQ return, but using SPY is conservative (a more volatile
    proxy leads to a more cautious multiplier).
    """
    cfg = config or VolTargetConfig()
    if return_col not in df.columns:
        raise KeyError(f"{return_col} not in df columns")
    rets = df[return_col].astype(float).pct_change()
    rv = realized_vol(rets, cfg.lookback, periods_per_year=periods_per_year)
    scale = vol_targeting_scale(rv, cfg)
    eff = rule_multiplier.astype(float) * scale
    eff = eff.clip(lower=0.0, upper=3.0)  # safety cap; matches rule max
    return eff, scale, rv


__all__ = [
    "VolTargetConfig",
    "realized_vol",
    "vol_targeting_scale",
    "apply_vol_targeting",
]
