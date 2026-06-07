#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VixSizingConfig:
    high_vix_threshold: float = 25.0
    high_vix_multiplier: float = 0.5
    low_vix_multiplier: float = 1.0
    max_monthly_adjustments: int = 2


def vix_regime_multiplier(vix: float, config: VixSizingConfig | None = None) -> float:
    cfg = config or VixSizingConfig()
    if pd.isna(vix):
        return cfg.low_vix_multiplier
    return cfg.high_vix_multiplier if float(vix) >= cfg.high_vix_threshold else cfg.low_vix_multiplier


def throttle_monthly_adjustments(raw_multipliers: pd.Series, max_monthly_adjustments: int = 2) -> pd.Series:
    if raw_multipliers.empty:
        return raw_multipliers.copy()

    values: List[float] = []
    current = float(raw_multipliers.iloc[0])
    month_key = None
    adjustments = 0

    for dt, raw in raw_multipliers.items():
        ts = pd.Timestamp(dt)
        ym = (ts.year, ts.month)
        if ym != month_key:
            month_key = ym
            adjustments = 0

        raw = float(raw)
        if raw != current and adjustments < max_monthly_adjustments:
            current = raw
            adjustments += 1
        values.append(current)

    return pd.Series(values, index=raw_multipliers.index, name=raw_multipliers.name)


def build_vix_sizing_series(vix: pd.Series, config: VixSizingConfig | None = None) -> pd.Series:
    cfg = config or VixSizingConfig()
    raw = vix.apply(lambda x: vix_regime_multiplier(float(x), cfg))
    raw.name = "vix_sizing_multiplier"
    return throttle_monthly_adjustments(raw, cfg.max_monthly_adjustments)


def apply_sizing_to_nav(nav: pd.Series, sizing: pd.Series) -> pd.Series:
    aligned = pd.concat([nav.astype(float), sizing.astype(float)], axis=1).dropna()
    if aligned.empty:
        return nav.copy()

    base = aligned.iloc[:, 0]
    mult = aligned.iloc[:, 1].shift(1).fillna(aligned.iloc[0, 1])
    returns = base.pct_change().fillna(0.0)
    adjusted_returns = returns * mult
    adjusted = (1.0 + adjusted_returns).cumprod()
    adjusted.name = f"{nav.name or 'nav'}_vix_sized"
    return adjusted


def max_monthly_adjustments(sizing: pd.Series) -> int:
    if sizing.empty:
        return 0
    counts = []
    for _, month_values in sizing.groupby([sizing.index.year, sizing.index.month]):
        counts.append(int(month_values.astype(float).diff().fillna(0.0).ne(0.0).sum()))
    return max(counts) if counts else 0


def max_drawdown(values: pd.Series) -> float:
    clean = values.astype(float).dropna()
    if clean.empty:
        return 0.0
    return float((clean / clean.cummax() - 1.0).min())


def annualized_return(values: pd.Series) -> float:
    clean = values.astype(float).dropna()
    if len(clean) < 2:
        return 0.0
    years = max((clean.index[-1] - clean.index[0]).days / 365.25, 1 / 252)
    return float(clean.iloc[-1] ** (1 / years) - 1)
