#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import pandas as pd

import sys as _sys
_scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)

from risk_sizing import (
    VixSizingConfig,
    apply_sizing_to_nav,
    build_vix_sizing_series,
    max_monthly_adjustments,
    throttle_monthly_adjustments,
    vix_regime_multiplier,
)


DEFAULT_CONFIG = VixSizingConfig()


def size_for_vix(vix: float, config: VixSizingConfig | None = None) -> float:
    return vix_regime_multiplier(vix, config or DEFAULT_CONFIG)


def sizing_series(vix: pd.Series, config: VixSizingConfig | None = None) -> pd.Series:
    return build_vix_sizing_series(vix, config or DEFAULT_CONFIG)
