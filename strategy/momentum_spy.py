#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd

import sys as _sys
_scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)

from optimize_params import (
    HOLDINGS,
    LOOKBACKS,
    THRESHOLDS,
    ParamSet,
    load_default_dataset,
    momentum_overlay_nav,
    run_search,
)


DEFAULT_PARAMS = ParamSet(lookback=20, threshold=0.02, holding=5)
PARAM_GRID = {
    "lookback": LOOKBACKS,
    "threshold": THRESHOLDS,
    "holding": HOLDINGS,
}


def backtest_params(df: pd.DataFrame, lookback: int, threshold: float, holding: int) -> pd.Series:
    params = ParamSet(int(lookback), round(float(threshold), 2), int(holding))
    return momentum_overlay_nav(df, params)


def grid_search(start: str = "2018-01-01", end: str | None = None) -> Dict:
    if end is None:
        end = pd.Timestamp.today().date().isoformat()
    df = load_default_dataset(start, end)
    return run_search(df)
