#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

import sys as _sys
_scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)

from factor_signals import (
    FACTOR_NAMES,
    build_report,
    compute_factors,
    forward_return_target,
    information_stats,
    multifactor_signal,
    rsi_single_factor,
    signal_nav,
)
