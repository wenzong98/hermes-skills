#!/usr/bin/env python3
"""
US ETF Quant System Backtest
============================
SPY + QQQ valuation/risk-aware DCA system for S&P 500 and Nasdaq-100 exposure.

Data sources:
- SPY/QQQ daily OHLCV: Nasdaq public quote API price-return by default, with optional adjusted providers
- VIX daily OHLC: Cboe public CSV
- Shiller CAPE monthly: multpl.com HTML table, forward-filled to trade dates

The engine models a fixed weekly capital budget flowing into cash. The strategy
can invest 0x-3x of that weekly budget using accumulated cash reserves; the
benchmark invests exactly 1x weekly into static 50/50 SPY/QQQ. This keeps total
external capital comparable while allowing valuation timing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

import sys as _sys
from pathlib import Path as _Path
_scripts_dir = str(_Path(__file__).resolve().parent)
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)

from data_sources import (
    PRICE_SOURCES as _DS_PRICE_SOURCES,
    fetch_etf_ohlcv,
    fetch_cboe_vix,
    fetch_shiller_cape,
    load_cape_vintage,
    parse_number,
)

try:
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover - chart is optional
    plt = None

STRATEGY_VERSION = "1.3.0-total-return-pit"
EXECUTION_PRICE_MODES = {"next_open", "next_close", "same_close"}
PRICE_SOURCES = _DS_PRICE_SOURCES
WEEKDAY_NAMES = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
}
DECISION_REQUIRED_COLUMNS = [
    "cape",
    "spy_rsi14",
    "vix",
    "spy_drawdown_252d",
    "trend_up",
    "vix_sma20",
    "spy_ret_21d",
    "qqq_rel_63d",
    "qqq_rel_126d",
    "qqq_trend_up",
    "spy_close",
    "qqq_close",
]


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    # Saturate "100" → 99.99. A flat-up day with loss==0 technically yields
    # RSI=100, but that is a saturated reading, not "historically strongest
    # overbought". Clipping preserves the overbought trigger (`rsi>=70`) for
    # the decide() branch while making the displayed value honest. NaN
    # readings (when both gain and loss are zero, e.g. post-holiday flat
    # sessions) are kept as NaN; `valid_signal_row` already drops those.
    return rsi.clip(upper=99.99)


def prepare_dataset(
    start: str,
    end: str,
    warmup_days: int = 420,
    cape_lag_bdays: int = 10,
    price_source: str = "nasdaq_price_return",
    cape_source: str = "yale_shiller",
    allow_price_return_fallback: bool = False,
    alpha_vantage_api_key: Optional[str] = None,
    tiingo_api_key: Optional[str] = None,
    cache_dir: Optional[str] = None,
    require_adjusted: bool = False,
    cape_vintage_path: Optional[str] = None,
) -> pd.DataFrame:
    start_ts = pd.Timestamp(start)
    fetch_start = (start_ts - pd.Timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    spy_raw = fetch_etf_ohlcv(
        "SPY", fetch_start, end, price_source,
        allow_price_return_fallback=allow_price_return_fallback,
        alpha_vantage_api_key=alpha_vantage_api_key,
        tiingo_api_key=tiingo_api_key,
        cache_dir=cache_dir,
    )
    qqq_raw = fetch_etf_ohlcv(
        "QQQ", fetch_start, end, price_source,
        allow_price_return_fallback=allow_price_return_fallback,
        alpha_vantage_api_key=alpha_vantage_api_key,
        tiingo_api_key=tiingo_api_key,
        cache_dir=cache_dir,
    )
    if require_adjusted:
        if spy_raw.attrs.get("price_return_only") or qqq_raw.attrs.get("price_return_only"):
            raise RuntimeError(
                f"--require-adjusted is set but actual data source is price-return-only. "
                f"SPY source: {spy_raw.attrs.get('price_source')}, "
                f"QQQ source: {qqq_raw.attrs.get('price_source')}. "
                f"Use --price-source tiingo_adjusted|yahoo_chart_adjusted|alpha_vantage_adjusted "
                f"and do not use --allow-price-return-fallback."
            )
        if not cape_vintage_path:
            raise RuntimeError(
                "--require-adjusted is set but no --cape-vintage-path was provided. "
                "The 10-business-day multpl/yale fallback is research-only; production runs "
                "that demand dividend-adjusted prices must also use a PIT-correct CAPE vintage file. "
                "Run scripts/update_cape_snapshot.py first and pass --cape-vintage-path <path>."
            )
    spy = spy_raw.add_prefix("spy_")
    qqq = qqq_raw.add_prefix("qqq_")
    vix = fetch_cboe_vix(fetch_start, end)
    if cape_vintage_path:
        cape = load_cape_vintage(cape_vintage_path)
        uses_vintage = True
    else:
        cape = fetch_shiller_cape(fetch_start, end, cape_source=cape_source)
        uses_vintage = False

    df = spy.join(qqq, how="inner").join(vix, how="left")
    df["vix"] = df["vix"].ffill()
    if uses_vintage:
        df["cape"] = cape["cape"].reindex(df.index, method="ffill")
    else:
        cape_available = cape.copy()
        cape_available.index = cape_available.index + pd.offsets.BDay(cape_lag_bdays)
        df["cape"] = cape_available["cape"].reindex(df.index, method="ffill")
        # Yale path: equivalent of `assert_vintage_constraints` for the
        # non-vintage source. With a `cape_lag_bdays` publication lag, the
        # first ~10 business days of the backtest window would forward-fill
        # NaN CAPE and silently drop the first signals. Fail loudly so the
        # operator can either extend the start date, refresh the Yale source
        # via `scripts/update_cape_snapshot.py`, or build a vintage file.
        cape_window = df["cape"].iloc[: cape_lag_bdays + 5]
        if cape_window.isna().any():
            nan_count = int(cape_window.isna().sum())
            raise RuntimeError(
                f"CAPE Yale path: first {cape_lag_bdays + 5} trading days contain "
                f"{nan_count} NaN values. With the {cape_lag_bdays}-business-day "
                f"publication lag, the backtest start date must be at least that "
                f"many days after the latest CAPE observation in the Yale source. "
                f"Run `scripts/update_cape_snapshot.py` to refresh CAPE, or pass "
                f"`--cape-vintage-path` for a PIT-correct vintage file."
            )

    c = df["spy_close"]
    q = df["qqq_close"]
    df["spy_sma50"] = c.rolling(50).mean()
    df["spy_sma200"] = c.rolling(200).mean()
    df["qqq_sma200"] = q.rolling(200).mean()
    df["spy_rsi14"] = rsi_wilder(c)
    df["spy_ret_21d"] = c.pct_change(21)
    df["spy_ret_63d"] = c.pct_change(63)
    df["qqq_ret_63d"] = q.pct_change(63)
    df["spy_drawdown_252d"] = c / c.rolling(252, min_periods=60).max() - 1
    df["vix_sma20"] = df["vix"].rolling(20).mean()
    rel = q / c
    df["qqq_rel_63d"] = rel.pct_change(63)
    df["qqq_rel_126d"] = rel.pct_change(126)
    df["trend_up"] = c > df["spy_sma200"]
    df["qqq_trend_up"] = q > df["qqq_sma200"]
    df["trend_strong"] = (df["spy_sma50"] > df["spy_sma200"]) & (df["qqq_rel_63d"] > 0)
    df["risk_off"] = (df["spy_close"] < df["spy_sma200"]) & (df["vix"] > 25)
    out = df.loc[df.index >= start_ts].dropna(subset=["spy_sma200", "spy_rsi14", "cape", "vix"])
    out.attrs["cape_lag_bdays"] = int(cape_lag_bdays)
    out.attrs["price_source"] = price_source
    out.attrs["actual_price_source_spy"] = spy_raw.attrs.get("price_source")
    out.attrs["actual_price_source_qqq"] = qqq_raw.attrs.get("price_source")
    out.attrs["adjusted_for_dividends"] = bool(
        spy_raw.attrs.get("adjusted_for_dividends") and qqq_raw.attrs.get("adjusted_for_dividends")
    )
    out.attrs["price_return_only"] = bool(
        spy_raw.attrs.get("price_return_only") or qqq_raw.attrs.get("price_return_only")
    )
    out.attrs["cape_source"] = cape.attrs.get("cape_source", cape_source)
    if uses_vintage:
        out.attrs["cape_vintage_path"] = str(cape.attrs.get("cape_vintage_path", cape_vintage_path))
        if not cape.empty and "observation_month" in cape.columns:
            latest_obs = cape["observation_month"].iloc[-1]
            latest_avail = cape.index[-1]
            out.attrs["latest_vintage_file_observation_month"] = str(pd.Timestamp(latest_obs).date())
            out.attrs["latest_vintage_file_available_at"] = str(pd.Timestamp(latest_avail).date())
            out.attrs["latest_used_cape_observation_month"] = _resolve_latest_used_cape_obs(out)
            out.attrs["latest_used_cape_available_at"] = _resolve_latest_used_cape_avail(out)
        else:
            out.attrs["latest_vintage_file_observation_month"] = ""
            out.attrs["latest_vintage_file_available_at"] = ""
            out.attrs["latest_used_cape_observation_month"] = ""
            out.attrs["latest_used_cape_available_at"] = ""
    return out


def assert_vintage_constraints(df: pd.DataFrame) -> None:
    vintage_path = df.attrs.get("cape_vintage_path")
    if not vintage_path:
        return
    if df.empty:
        return
    latest_avail_str = df.attrs.get("latest_cape_available_at", "") or df.attrs.get("latest_vintage_file_available_at", "")
    if not latest_avail_str:
        return
    latest_avail = pd.Timestamp(latest_avail_str)
    last_signal_date = df.index.max()
    if pd.isna(last_signal_date):
        return
    if latest_avail > last_signal_date:
        usable_rows = df[df.index >= latest_avail]
        if len(usable_rows) > 0:
            raise AssertionError(
                f"CAPE vintage constraint violated: latest available_at ({latest_avail.date()}) > "
                f"last signal date ({last_signal_date.date()}). "
                f"There are {len(usable_rows)} signal dates after the latest CAPE availability date."
            )


def _resolve_latest_used_cape_obs(df: pd.DataFrame) -> str:
    vintage_path = df.attrs.get("cape_vintage_path", "")
    if not vintage_path or df.empty:
        return ""
    try:
        vintage_df = pd.read_csv(vintage_path, parse_dates=["observation_month", "available_at"])
        vintage_df = vintage_df.sort_values("available_at")
        last_signal = df.index.max()
        if pd.isna(last_signal):
            return ""
        matched = None
        for avail_dt in reversed(vintage_df["available_at"]):
            if avail_dt <= last_signal:
                matched = avail_dt
                break
        if matched is not None:
            row = vintage_df[vintage_df["available_at"] == matched].iloc[0]
            return str(pd.Timestamp(row["observation_month"]).date())
    except Exception:
        pass
    return ""


def _resolve_latest_used_cape_avail(df: pd.DataFrame) -> str:
    vintage_path = df.attrs.get("cape_vintage_path", "")
    if not vintage_path or df.empty:
        return ""
    try:
        vintage_df = pd.read_csv(vintage_path, parse_dates=["observation_month", "available_at"])
        vintage_df = vintage_df.sort_values("available_at")
        last_signal = df.index.max()
        if pd.isna(last_signal):
            return ""
        for avail_dt in reversed(vintage_df["available_at"]):
            if avail_dt <= last_signal:
                return str(avail_dt.date())
    except Exception:
        pass
    return ""


@dataclass
class DayDecision:
    multiplier: float
    spy_weight: float
    qqq_weight: float
    trim_spy_frac: float
    trim_qqq_frac: float
    regime: str
    reason: str
    panic_tier: int = 0
    satellite_signal: str = "neutral"
    core_spy_weight: float = 0.40
    core_qqq_weight: float = 0.40
    satellite_spy_weight: float = 0.10
    satellite_qqq_weight: float = 0.10
    cash_reservoir_pct: Optional[float] = None


def panic_ladder(row: pd.Series) -> Tuple[int, str]:
    """Scheme 8: explicit fear ladder from drawdown, VIX, and RSI."""
    rsi = float(row["spy_rsi14"])
    vix = float(row["vix"])
    dd = float(row["spy_drawdown_252d"])

    tier = 0
    triggers = []
    if (dd <= -0.08 and rsi <= 40) or (vix >= 28 and rsi <= 40):
        tier = max(tier, 1)
        triggers.append(f"tier1_dip:VIX={vix:.1f},RSI={rsi:.1f},DD={dd:.1%}")
    if dd <= -0.15 or vix >= 35 or (rsi <= 32 and dd <= -0.08):
        tier = max(tier, 2)
        triggers.append(f"tier2_panic:VIX={vix:.1f},RSI={rsi:.1f},DD={dd:.1%}")
    if dd <= -0.22 or vix >= 45:
        tier = max(tier, 3)
        triggers.append(f"tier3_crash:VIX={vix:.1f},RSI={rsi:.1f},DD={dd:.1%}")
    return tier, ",".join(triggers) if triggers else "none"


def core_satellite_allocation(row: pd.Series, panic_tier: int) -> Tuple[float, float, float, float, str]:
    """Scheme 11B + priority 3: 80% 50/50 core plus 20% momentum/risk satellite."""
    cape = float(row["cape"])
    rsi = float(row["spy_rsi14"])
    vix = float(row["vix"])
    trend_up = bool(row["trend_up"])
    qqq_trend_up = bool(row.get("qqq_trend_up", True))
    qqq_rel_63d = float(row["qqq_rel_63d"])
    qqq_rel_126d = float(row.get("qqq_rel_126d", 0.0))

    core_spy, core_qqq = 0.40, 0.40
    sat_spy, sat_qqq = 0.10, 0.10
    signal = "neutral_satellite"

    if (not trend_up) and vix >= 25:
        sat_spy, sat_qqq = 0.20, 0.00
        signal = "risk_off_satellite_to_spy"
    elif panic_tier >= 2:
        if trend_up or rsi <= 32:
            sat_spy, sat_qqq = 0.00, 0.20
            signal = f"panic_tier_{panic_tier}_satellite_to_qqq"
        else:
            sat_spy, sat_qqq = 0.20, 0.00
            signal = f"panic_tier_{panic_tier}_falling_knife_satellite_to_spy"
    elif panic_tier == 1:
        sat_spy, sat_qqq = (0.05, 0.15) if qqq_rel_63d > -0.05 else (0.10, 0.10)
        signal = "dip_buy_satellite_balanced_or_qqq_tilt"
    elif cape >= 42 and rsi >= 70:
        sat_spy, sat_qqq = 0.20, 0.00
        signal = "extreme_valuation_overbought_satellite_to_spy"
    elif qqq_rel_63d >= 0.08 and qqq_rel_126d >= 0 and qqq_trend_up and vix < 22:
        sat_spy, sat_qqq = 0.00, 0.20
        signal = "qqq_strong_satellite_to_qqq"
    elif qqq_rel_63d >= 0.03 and qqq_trend_up and vix < 25:
        sat_spy, sat_qqq = 0.05, 0.15
        signal = "qqq_mild_strength_satellite_qqq_tilt"
    elif qqq_rel_63d <= -0.05 or not qqq_trend_up:
        sat_spy, sat_qqq = 0.20, 0.00
        signal = "qqq_weak_satellite_to_spy"

    spy_w = core_spy + sat_spy
    qqq_w = core_qqq + sat_qqq
    return spy_w, qqq_w, sat_spy, sat_qqq, signal


def apply_cash_reservoir_policy(dec: DayDecision, row: pd.Series, cash_pct: Optional[float]) -> DayDecision:
    """Priority 2: avoid excessive cash drag when trend/risk are still supportive."""
    if cash_pct is None or math.isnan(cash_pct):
        return dec

    trend_up = bool(row["trend_up"])
    vix = float(row["vix"])
    cape = float(row["cape"])
    rsi = float(row["spy_rsi14"])
    qqq_rel = float(row["qqq_rel_63d"])
    mult = dec.multiplier
    reasons = [dec.reason]

    if trend_up and vix < 25 and dec.panic_tier == 0:
        if cash_pct >= 0.30:
            floor = 0.75 if (cape >= 42 and rsi >= 70) else 1.0
            if mult < floor:
                mult = floor
                reasons.append(f"cash_reservoir_floor_{floor:.2f}x:cash={cash_pct:.1%}")
        elif cash_pct >= 0.25:
            floor = 0.75 if cape >= 42 else 1.0
            if mult < floor:
                mult = floor
                reasons.append(f"cash_reservoir_floor_{floor:.2f}x:cash={cash_pct:.1%}")
        elif cash_pct >= 0.20 and vix < 20 and qqq_rel > 0 and mult < 0.75:
            mult = 0.75
            reasons.append(f"cash_reservoir_floor_0.75x:cash={cash_pct:.1%}")

    return DayDecision(
        float(np.clip(mult, 0.0, 3.0)),
        dec.spy_weight,
        dec.qqq_weight,
        dec.trim_spy_frac,
        dec.trim_qqq_frac,
        dec.regime,
        "; ".join(reasons),
        dec.panic_tier,
        dec.satellite_signal,
        dec.core_spy_weight,
        dec.core_qqq_weight,
        dec.satellite_spy_weight,
        dec.satellite_qqq_weight,
        cash_pct,
    )


def decide(row: pd.Series) -> DayDecision:
    cape = float(row["cape"])
    rsi = float(row["spy_rsi14"])
    vix = float(row["vix"])
    trend_up = bool(row["trend_up"])
    qqq_rel = float(row["qqq_rel_63d"])

    # 1) Valuation base: CAPE is slow-moving, controls the long-term DCA throttle.
    if cape < 22:
        mult, regime = 2.0, "deep_value"
    elif cape < 25:
        mult, regime = 1.5, "cheap"
    elif cape < 30:
        mult, regime = 1.25, "fair"
    elif cape < 35:
        mult, regime = 1.0, "elevated"
    elif cape < 38:
        mult, regime = 0.75, "expensive"
    elif cape < 42:
        mult, regime = 0.75, "very_expensive"
    else:
        mult, regime = 0.5, "extreme_valuation"

    reasons = [f"CAPE={cape:.1f}:{regime}"]

    # 2) Scheme 8 risk overlays: explicit fear ladder, then ordinary risk caps.
    panic_tier, panic_reason = panic_ladder(row)
    oversold = rsi <= 40 or float(row["spy_drawdown_252d"]) <= -0.08
    overbought_expensive = rsi >= 70 and cape >= 35
    falling_knife = (
        panic_tier > 0
        and (not trend_up)
        and vix > float(row.get("vix_sma20", vix))
        and float(row.get("spy_ret_21d", 0.0)) < 0
    )

    if panic_tier > 0:
        target = {1: 1.25, 2: 1.5, 3: 2.0}[panic_tier]
        if falling_knife:
            target = min(target, 1.5 if panic_tier >= 3 else 1.25)
            reasons.append("falling_knife_guard")
        mult = max(mult, target)
        reasons.append(f"panic_ladder_{panic_tier}:{panic_reason}")
    elif vix >= 25 and not oversold:
        mult = min(mult, 0.5)
        reasons.append(f"high_vix_cap:VIX={vix:.1f}")

    if not trend_up and panic_tier == 0:
        mult = min(mult, 0.5)
        reasons.append("below_sma200_cap")

    if overbought_expensive:
        mult = min(mult, 0.75)
        reasons.append(f"overbought_expensive_soft_cap:RSI={rsi:.1f}")

    # Secular bull-market guard: valuation alone should slow, not fully stop,
    # participation while trend is healthy and volatility is calm.
    if trend_up and vix < 20 and qqq_rel > 0 and cape >= 35:
        mult = max(mult, 0.75)
        reasons.append("trend_confirmed_min_0_75x")

    mult = float(np.clip(mult, 0.0, 3.0))

    # 3) Scheme 11B + priority 3: 80/20 core-satellite for new buys.
    spy_w, qqq_w, sat_spy, sat_qqq, satellite_signal = core_satellite_allocation(row, panic_tier)
    reasons.append(satellite_signal)

    # 4) Trims are throttled in the engine to at most once per month/regime.
    trim_spy = 0.0
    trim_qqq = 0.0
    if cape >= 42 and rsi >= 75:
        trim_qqq = 0.03
        reasons.append("monthly_extreme_valuation_micro_trim")
    elif cape >= 40 and rsi >= 78:
        trim_qqq = 0.03
        reasons.append("monthly_profit_lock_micro_trim")
    elif (not trend_up) and vix >= 30:
        trim_qqq = 0.10
        reasons.append("monthly_risk_off_trim")

    return DayDecision(
        mult,
        spy_w,
        qqq_w,
        trim_spy,
        trim_qqq,
        regime,
        "; ".join(reasons),
        panic_tier,
        satellite_signal,
        0.40,
        0.40,
        sat_spy,
        sat_qqq,
    )


def xirr(cashflows: List[Tuple[pd.Timestamp, float]]) -> Optional[float]:
    """Annualized money-weighted return. Negative values are contributions; positive is final value."""
    flows = [(pd.Timestamp(d), float(v)) for d, v in cashflows if abs(v) > 1e-9]
    if not flows or not (any(v < 0 for _, v in flows) and any(v > 0 for _, v in flows)):
        return None
    t0 = flows[0][0]

    def npv(rate: float) -> float:
        total = 0.0
        for d, v in flows:
            years = (d - t0).days / 365.25
            total += v / ((1 + rate) ** years)
        return total

    lo, hi = -0.999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            return mid
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def unitized_nav(values: pd.Series, flows: pd.Series) -> pd.Series:
    prev = values.shift(1)
    ret = (values - prev - flows.fillna(0.0)) / prev
    ret = ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    nav = (1.0 + ret).cumprod()
    if not nav.empty:
        nav.iloc[0] = 1.0
    return nav


def cash_flow_adjusted_returns(values: pd.Series, flows: pd.Series) -> pd.Series:
    prev = values.shift(1)
    ret = (values - prev - flows.fillna(0.0)) / prev
    return ret.replace([np.inf, -np.inf], np.nan).dropna()


def annualized_stats(values: pd.Series, flows: pd.Series, risk_free_rate: float = 0.0) -> Dict[str, float]:
    # Daily return adjusted for external capital flows into the portfolio.
    ret = cash_flow_adjusted_returns(values, flows)
    if ret.empty:
        return {"volatility": 0.0, "sharpe": 0.0, "sortino": 0.0, "win_rate": 0.0}
    vol = float(ret.std() * np.sqrt(252))
    daily_rf = (1 + risk_free_rate) ** (1 / 252) - 1 if risk_free_rate > -1 else 0.0
    excess = ret - daily_rf
    mean_ann = float(excess.mean() * 252)
    downside = excess[excess < 0]
    downside_vol = float(downside.std() * np.sqrt(252)) if len(downside) else 0.0
    return {
        "volatility": vol,
        "sharpe": mean_ann / vol if vol > 0 else 0.0,
        "sortino": mean_ann / downside_vol if downside_vol > 0 else 0.0,
        "win_rate": float((ret > 0).mean()),
    }


def max_drawdown(values: pd.Series) -> float:
    dd = values / values.cummax() - 1
    return float(dd.min())


def normalize_execution_price(mode: str) -> str:
    if mode not in EXECUTION_PRICE_MODES:
        choices = ", ".join(sorted(EXECUTION_PRICE_MODES))
        raise ValueError(f"Invalid execution price mode {mode!r}; choose one of: {choices}")
    return mode


def parse_weekday(value) -> int:
    if isinstance(value, int):
        weekday = value
    else:
        text = str(value).strip().lower()
        weekday = WEEKDAY_NAMES.get(text, None)
        if weekday is None:
            weekday = int(text)
    if weekday < 0 or weekday > 4:
        raise ValueError("contribution weekday must be 0-4 or Monday-Friday")
    return weekday


def trade_price_column(symbol: str, execution_price: str) -> str:
    if execution_price == "next_open":
        return f"{symbol}_open"
    return f"{symbol}_close"


def build_signal_frame(df: pd.DataFrame, execution_price: str) -> Tuple[pd.DataFrame, pd.Series]:
    if execution_price == "same_close":
        return df, pd.Series(df.index, index=df.index)
    return df.shift(1), pd.Series(df.index, index=df.index).shift(1)


def valid_signal_row(row: pd.Series) -> bool:
    required = [c for c in DECISION_REQUIRED_COLUMNS if c in row.index]
    return bool(required) and not row[required].isna().any()


def file_sha256(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def git_commit_hash(path: Path) -> Optional[str]:
    try:
        root = path.resolve().parents[1]
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()
    except Exception:
        return None


def git_dirty(path: Path) -> Optional[bool]:
    try:
        root = path.resolve().parents[1]
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            check=True,
            capture_output=True,
            text=True,
        )
        return bool(proc.stdout.strip())
    except Exception:
        return None


def data_snapshot_sha256(df: pd.DataFrame) -> str:
    cols = [
        c for c in [
            "spy_open", "spy_close", "qqq_open", "qqq_close", "vix", "cape",
            "spy_sma50", "spy_sma200", "qqq_sma200", "spy_rsi14",
            "spy_ret_21d", "spy_drawdown_252d", "qqq_rel_63d", "qqq_rel_126d",
        ] if c in df.columns
    ]
    text = df[cols].to_csv(index=True, float_format="%.10g")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_backtest(
    df: pd.DataFrame,
    start: str,
    end: str,
    initial_capital: float = 100_000.0,
    weekly_budget: float = 2_000.0,
    transaction_cost: float = 0.0015,
    execution_price: str = "next_open",
    contribution_weekday: int = 3,
    risk_free_rate: float = 0.0,
    cash_apy: float = 0.0,
    slippage_bps: float = 0.0,
    vol_target_lookback: int = 0,
    vol_target_floor: float = 0.5,
    vol_target_ceiling: float = 1.5,
    vol_target_target: float = 0.12,
) -> Dict:
    """Run the SPY+QQQ valuation/risk-aware DCA backtest.

    Opt-in overlays (all default OFF so canonical runs are unchanged):

    cash_apy
        Annualized yield on the cash sleeve, accrued daily
        (cash *= 1 + cash_apy / 252 on every step). 0.045 ≈ the
        trailing FRED DTB3 1y average.
    slippage_bps
        Extra per-fill deduction on top of `transaction_cost`. Applied
        to both buy and sell fills. 5 bps is a reasonable default for
        liquid US ETFs.
    vol_target_lookback
        If > 0, enable a vol-targeting smooth layer: a rolling SPY
        realized vol of this many days drives a scale in
        [vol_target_floor, vol_target_ceiling] that multiplies the
        rule's discrete multiplier. 63 = quarterly, 21 = monthly.
    vol_target_floor / vol_target_ceiling
        Lower / upper bound on the vol-target scale.
    vol_target_target
        Annualized realized-vol target. 0.12 is a common balanced
        target.

    These four overlays were validated in the
    references/validation/ P0+P1 reports and are now first-class
    engine parameters. The canonical 3y artifact
    (references/backtest_3y_*.json) was generated with all four
    defaulting to OFF, so existing artifacts and the independent
    verifier remain bit-for-bit identical.
    """
    execution_price = normalize_execution_price(execution_price)
    contribution_weekday = parse_weekday(contribution_weekday)
    df = df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))].copy()
    if df.empty:
        raise RuntimeError("No data rows in requested backtest window")

    # Pre-compute the vol-target scale if requested. We compute it
    # once on the full price series and reindex to the trading
    # window so it's PIT-correct (uses only past returns).
    vol_scale_series = None
    if vol_target_lookback and vol_target_lookback > 0:
        try:
            from vol_targeting import (  # noqa: E402
                VolTargetConfig,
                realized_vol,
                vol_targeting_scale,
            )
            cfg = VolTargetConfig(
                target_vol=vol_target_target,
                lookback=vol_target_lookback,
                vol_floor=vol_target_floor,
                vol_ceiling=vol_target_ceiling,
            )
            rv = realized_vol(df["spy_close"].astype(float), cfg.lookback)
            vol_scale_series = vol_targeting_scale(rv, cfg)
        except Exception as e:  # noqa: BLE001
            # If the smooth layer fails, fall back to 1.0 with a
            # warning in the result so the user notices.
            vol_scale_series = None
            print(f"[backtest] vol-targeting layer disabled: {e}")

    signal_df, signal_dates = build_signal_frame(df, execution_price)
    spy_trade_col = trade_price_column("spy", execution_price)
    qqq_trade_col = trade_price_column("qqq", execution_price)

    cash = 0.0
    spy_shares = 0.0
    qqq_shares = 0.0

    bench_cash = 0.0
    bench_spy = 0.0
    bench_qqq = 0.0

    rows = []
    trades = []
    strategy_flows = []
    benchmark_flows = []
    last_contrib_iso = None
    last_trim_month = None
    initialized = False
    # Total cash-yield accrued over the run, tracked separately so it
    # can be reported as `cash_yield_contribution` in metrics.
    cash_yield_total = 0.0
    # Total slippage paid over the run.
    slippage_total = 0.0

    # The vol-targeting scale is recomputed on every buy day inside
    # the loop. It must be defined on non-buy days too so the
    # `effective_multiplier` row field is always present.
    effective_multiplier = None

    for dt, row in df.iterrows():
        signal_row = signal_df.loc[dt]
        signal_date = signal_dates.loc[dt]
        if pd.isna(signal_date) or not valid_signal_row(signal_row):
            continue

        signal_date = pd.Timestamp(signal_date)
        spy_trade_px = float(row[spy_trade_col])
        qqq_trade_px = float(row[qqq_trade_col])
        spy_px = float(row["spy_close"])
        qqq_px = float(row["qqq_close"])

        # --- Cash APY accrual (opt-in) ---
        # Accrue daily yield on the cash sleeve *before* any buy/sell
        # decision so the day-on-day compounding is correct. The
        # contribution is a real return on the reservoir and is
        # reported separately in metrics.
        if cash_apy > 0 and cash > 0:
            daily_yield = cash * (cash_apy / 252.0)
            cash += daily_yield
            cash_yield_total += daily_yield

        if not initialized:
            first_decision = decide(signal_row)
            cash = 0.0
            spy_shares = initial_capital * first_decision.spy_weight * (1 - transaction_cost) / spy_trade_px
            qqq_shares = initial_capital * first_decision.qqq_weight * (1 - transaction_cost) / qqq_trade_px

            bench_cash = 0.0
            bench_spy = initial_capital * 0.5 * (1 - transaction_cost) / spy_trade_px
            bench_qqq = initial_capital * 0.5 * (1 - transaction_cost) / qqq_trade_px

            strategy_flows.append((dt, -initial_capital))
            benchmark_flows.append((dt, -initial_capital))
            trades.append({
                "date": str(dt.date()), "signal_date": str(signal_date.date()),
                "action": "INITIAL_ALLOC", "amount": round(initial_capital, 2),
                "execution_price_mode": execution_price,
                "spy_trade_price": round(spy_trade_px, 4),
                "qqq_trade_price": round(qqq_trade_px, 4),
                "spy_weight": first_decision.spy_weight,
                "qqq_weight": first_decision.qqq_weight,
                "regime": first_decision.regime,
                "panic_tier": first_decision.panic_tier,
                "satellite_signal": first_decision.satellite_signal,
                "reason": first_decision.reason,
            })
            initialized = True

        pre_trade_signal_value = (
            cash
            + spy_shares * float(signal_row["spy_close"])
            + qqq_shares * float(signal_row["qqq_close"])
        )
        pre_trade_cash_pct = cash / pre_trade_signal_value if pre_trade_signal_value else 0.0
        dec = apply_cash_reservoir_policy(decide(signal_row), signal_row, pre_trade_cash_pct)
        flow_today = 0.0
        bench_flow_today = 0.0

        # On non-buy days the effective multiplier is just the rule
        # output (no vol-targeting application happens here). It is
        # computed on the buy days below; this line keeps the
        # per-day equity row consistent.
        if effective_multiplier is None:
            effective_multiplier = dec.multiplier

        # Weekly budget: first trading day on/after Thursday in each ISO week.
        # Use (iso_year, iso_week) so the year-boundary does not collapse two
        # weeks (e.g. ISO 2026-W53 and ISO 2027-W01 both have a `(53,)` prefix
        # when sliced as `dt.isocalendar()[:2]`, and the un-keyed week number
        # alone would re-trigger on the very first session of a new year).
        iso_week = int(dt.isocalendar().week)
        iso_year = int(dt.isocalendar().year)
        contrib_key = (iso_year, iso_week)
        if dt.weekday() >= contribution_weekday and contrib_key != last_contrib_iso:
            last_contrib_iso = contrib_key
            cash += weekly_budget
            bench_cash += weekly_budget
            flow_today += weekly_budget
            bench_flow_today += weekly_budget
            strategy_flows.append((dt, -weekly_budget))
            benchmark_flows.append((dt, -weekly_budget))

            pre_buy_signal_value = (
                cash
                + spy_shares * float(signal_row["spy_close"])
                + qqq_shares * float(signal_row["qqq_close"])
            )
            pre_buy_cash_pct = cash / pre_buy_signal_value if pre_buy_signal_value else 0.0
            dec = apply_cash_reservoir_policy(decide(signal_row), signal_row, pre_buy_cash_pct)

            # --- Vol-targeting smoothing (opt-in) ---
            # If a vol-scale series is configured, multiply the
            # discrete rule multiplier by the smooth scale (clamped
            # to [0, 3] to match the rule's max). When the layer is
            # disabled (vol_scale_series is None) this is a no-op
            # and dec.multiplier is unchanged.
            effective_multiplier = dec.multiplier
            if vol_scale_series is not None and dt in vol_scale_series.index:
                v = vol_scale_series.loc[dt]
                if pd.notna(v):
                    effective_multiplier = float(dec.multiplier) * float(v)
                    # Clamp to the rule's [0, 3] band so the smooth
                    # layer cannot push buys above the rule max.
                    effective_multiplier = max(0.0, min(3.0, effective_multiplier))

            invest_amt = min(cash, weekly_budget * effective_multiplier)
            if invest_amt > 0:
                spy_amt = invest_amt * dec.spy_weight
                qqq_amt = invest_amt * dec.qqq_weight
                # Slippage is an *additional* deduction on top of
                # `transaction_cost`. Both are applied to the same
                # notional; transaction_cost is the broker commission
                # / spread model, slippage is the market-impact /
                # participation-cost model.
                total_cost_pct = transaction_cost + (slippage_bps / 1e4)
                slippage_total += invest_amt * (slippage_bps / 1e4)
                spy_shares += spy_amt * (1 - total_cost_pct) / spy_trade_px
                qqq_shares += qqq_amt * (1 - total_cost_pct) / qqq_trade_px
                cash -= invest_amt
                trades.append({
                    "date": str(dt.date()), "signal_date": str(signal_date.date()),
                    "action": "DCA_BUY", "amount": round(invest_amt, 2),
                    "execution_price_mode": execution_price,
                    "spy_trade_price": round(spy_trade_px, 4),
                    "qqq_trade_price": round(qqq_trade_px, 4),
                    "multiplier": dec.multiplier, "spy_weight": dec.spy_weight,
                    "qqq_weight": dec.qqq_weight, "regime": dec.regime,
                    "panic_tier": dec.panic_tier, "satellite_signal": dec.satellite_signal,
                    "core_spy_weight": dec.core_spy_weight, "core_qqq_weight": dec.core_qqq_weight,
                    "satellite_spy_weight": dec.satellite_spy_weight,
                    "satellite_qqq_weight": dec.satellite_qqq_weight,
                    "cash_reservoir_pct": dec.cash_reservoir_pct,
                    "reason": dec.reason,
                })

            # Benchmark invests every budget 50/50 immediately.
            b_spy_amt = weekly_budget * 0.5
            b_qqq_amt = weekly_budget * 0.5
            bench_spy += b_spy_amt * (1 - transaction_cost) / spy_trade_px
            bench_qqq += b_qqq_amt * (1 - transaction_cost) / qqq_trade_px
            bench_cash -= weekly_budget
            # bench_cash should stay near 0; numerical safety
            if abs(bench_cash) < 1e-9:
                bench_cash = 0.0

        # Monthly trims: once per calendar month, after contribution logic.
        ym = (dt.year, dt.month)
        if ym != last_trim_month and (dec.trim_spy_frac > 0 or dec.trim_qqq_frac > 0):
            sold = 0.0
            trim_total_cost_pct = transaction_cost + (slippage_bps / 1e4)
            if dec.trim_spy_frac > 0 and spy_shares > 0:
                sh = spy_shares * dec.trim_spy_frac
                proceeds = sh * spy_trade_px * (1 - trim_total_cost_pct)
                slippage_total += sh * spy_trade_px * (slippage_bps / 1e4)
                spy_shares -= sh
                cash += proceeds
                sold += proceeds
            if dec.trim_qqq_frac > 0 and qqq_shares > 0:
                sh = qqq_shares * dec.trim_qqq_frac
                proceeds = sh * qqq_trade_px * (1 - trim_total_cost_pct)
                slippage_total += sh * qqq_trade_px * (slippage_bps / 1e4)
                qqq_shares -= sh
                cash += proceeds
                sold += proceeds
            if sold > 0:
                last_trim_month = ym
                trades.append({
                    "date": str(dt.date()), "signal_date": str(signal_date.date()),
                    "action": "MONTHLY_TRIM", "proceeds": round(sold, 2),
                    "execution_price_mode": execution_price,
                    "spy_trade_price": round(spy_trade_px, 4),
                    "qqq_trade_price": round(qqq_trade_px, 4),
                    "trim_spy_frac": dec.trim_spy_frac, "trim_qqq_frac": dec.trim_qqq_frac,
                    "regime": dec.regime, "reason": dec.reason,
                })

        value = cash + spy_shares * spy_px + qqq_shares * qqq_px
        bench_value = bench_cash + bench_spy * spy_px + bench_qqq * qqq_px
        equity_value = spy_shares * spy_px + qqq_shares * qqq_px
        rows.append({
            "date": dt,
            "signal_date": signal_date,
            "execution_price_mode": execution_price,
            "strategy_value": value,
            "benchmark_value": bench_value,
            "strategy_flow": flow_today,
            "benchmark_flow": bench_flow_today,
            "cash": cash,
            "cash_pct": cash / value if value else 0.0,
            "spy_weight_actual": spy_shares * spy_px / value if value else 0.0,
            "qqq_weight_actual": qqq_shares * qqq_px / value if value else 0.0,
            "equity_exposure": equity_value / value if value else 0.0,
            "multiplier": dec.multiplier,
            # `effective_multiplier` is the post-vol-targeting value
            # used for the actual buy. When the vol-targeting overlay
            # is OFF (the default) it equals `dec.multiplier` exactly.
            "effective_multiplier": float(effective_multiplier) if effective_multiplier is not None else dec.multiplier,
            "target_spy_weight": dec.spy_weight,
            "target_qqq_weight": dec.qqq_weight,
            "core_spy_weight": dec.core_spy_weight,
            "core_qqq_weight": dec.core_qqq_weight,
            "satellite_spy_weight": dec.satellite_spy_weight,
            "satellite_qqq_weight": dec.satellite_qqq_weight,
            "panic_tier": dec.panic_tier,
            "satellite_signal": dec.satellite_signal,
            "decision_cash_reservoir_pct": dec.cash_reservoir_pct,
            "regime": dec.regime,
            "reason": dec.reason,
            "cape": float(signal_row["cape"]),
            "vix": float(signal_row["vix"]),
            "rsi14": float(signal_row["spy_rsi14"]),
            "spy_close": spy_px,
            "qqq_close": qqq_px,
            "spy_trade_price": spy_trade_px,
            "qqq_trade_price": qqq_trade_px,
            "trend_up": bool(signal_row["trend_up"]),
            "qqq_trend_up": bool(signal_row.get("qqq_trend_up", True)),
            "spy_drawdown_252d": float(signal_row["spy_drawdown_252d"]),
            "vix_sma20": float(signal_row["vix_sma20"]),
            "spy_ret_21d": float(signal_row["spy_ret_21d"]),
            "qqq_rel_63d": float(signal_row["qqq_rel_63d"]),
            "qqq_rel_126d": float(signal_row.get("qqq_rel_126d", 0.0)),
        })

    if not rows:
        raise RuntimeError("No tradable rows after applying signal timing rules")

    eq = pd.DataFrame(rows).set_index("date")
    eq["strategy_nav"] = unitized_nav(eq["strategy_value"], eq["strategy_flow"])
    eq["benchmark_nav"] = unitized_nav(eq["benchmark_value"], eq["benchmark_flow"])
    final_strategy = float(eq["strategy_value"].iloc[-1])
    final_bench = float(eq["benchmark_value"].iloc[-1])
    total_contributed = initial_capital + float(eq["strategy_flow"].sum())
    strategy_flows.append((eq.index[-1], final_strategy))
    benchmark_flows.append((eq.index[-1], final_bench))
    strat_xirr = xirr(strategy_flows)
    bench_xirr = xirr(benchmark_flows)
    strat_stats = annualized_stats(eq["strategy_value"], eq["strategy_flow"], risk_free_rate=risk_free_rate)
    bench_stats = annualized_stats(eq["benchmark_value"], eq["benchmark_flow"], risk_free_rate=risk_free_rate)

    signal_dist = eq["regime"].value_counts(normalize=True).sort_index().to_dict()
    mult_dist = eq["multiplier"].value_counts(normalize=True).sort_index().to_dict()
    run_df = df.loc[(df.index >= eq.index.min()) & (df.index <= eq.index.max())]
    script_path = Path(__file__).resolve()
    is_lookahead_mode = execution_price == "same_close"

    result = {
        "meta": {
            "strategy_version": STRATEGY_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "git_commit": git_commit_hash(script_path),
            "git_dirty": git_dirty(script_path),
            "script_sha256": file_sha256(script_path),
            "data_snapshot_sha256": data_snapshot_sha256(run_df),
            "data_sources": {
                "SPY_QQQ": (
                    "Adjusted OHLC when an adjusted provider is selected; Nasdaq fallback is price-return "
                    "and excludes dividends"
                ),
                "VIX": "Cboe VIX_History.csv",
                "CAPE": "Yale Shiller ie_data primary or multpl fallback; monthly values are delayed before daily use",
            },
            "requested_start": start,
            "requested_end": end,
            "start": str(eq.index[0].date()),
            "end": str(eq.index[-1].date()),
            "trading_days": int(len(eq)),
            "price_source": df.attrs.get("price_source"),
            "actual_price_source_spy": df.attrs.get("actual_price_source_spy"),
            "actual_price_source_qqq": df.attrs.get("actual_price_source_qqq"),
            "adjusted_for_dividends": bool(df.attrs.get("adjusted_for_dividends")),
            "price_return_only": bool(df.attrs.get("price_return_only")),
            "cape_source": df.attrs.get("cape_source"),
            "cape_vintage_path": df.attrs.get("cape_vintage_path", ""),
            "latest_vintage_file_observation_month": df.attrs.get("latest_vintage_file_observation_month", ""),
            "latest_vintage_file_available_at": df.attrs.get("latest_vintage_file_available_at", ""),
            "latest_used_cape_observation_month": _resolve_latest_used_cape_obs(df),
            "latest_used_cape_available_at": _resolve_latest_used_cape_avail(df),
            "signal_timing": "same_close_signal" if is_lookahead_mode else "previous_close_signal",
            "execution_price": execution_price,
            "execution_model": (
                "same-day close signal and same-day close execution; research comparison only"
                if is_lookahead_mode else
                f"previous completed close signal with execution at {execution_price.replace('_', ' ')}"
            ),
            "lookahead_warning": (
                "same_close uses close-derived indicators and same close execution, so it is not tradable"
                if is_lookahead_mode else None
            ),
            "cape_available_lag_bdays": int(df.attrs.get("cape_lag_bdays", 10)),
            "release_tag": "",
            "release_commit": "",
            "artifact_build_note": "Set release_tag/release_commit at build time to pin artifact to a specific release. git_commit above reflects the script version at generation time.",
        },
        "assumptions": {
            "initial_capital": initial_capital,
            "weekly_budget": weekly_budget,
            "transaction_cost": transaction_cost,
            "slippage_bps": slippage_bps,
            "cash_apy": cash_apy,
            "vol_target_lookback": vol_target_lookback,
            "vol_target_target": vol_target_target if vol_target_lookback > 0 else None,
            "vol_target_floor": vol_target_floor if vol_target_lookback > 0 else None,
            "vol_target_ceiling": vol_target_ceiling if vol_target_lookback > 0 else None,
            "risk_free_rate": risk_free_rate,
            "contribution_weekday": contribution_weekday,
            "contribution_schedule": f"first trading day on/after {['Monday','Tuesday','Wednesday','Thursday','Friday'][contribution_weekday]} in each ISO week",
            "execution_price": execution_price,
            "signal_timing": "same_close_signal" if is_lookahead_mode else "previous_close_signal",
            "benchmark": "50/50 SPY/QQQ buy-and-hold plus weekly 1x budget",
            "strategy": "valuation/risk-aware DCA with scheme-8 panic ladder, cash-reservoir cap, 80/20 core-satellite new-buy weights, monthly trim throttle",
        },
        "strategy": {
            "final_value": round(final_strategy, 2),
            "total_contributed": round(total_contributed, 2),
            "profit": round(final_strategy - total_contributed, 2),
            "return_on_contributed": final_strategy / total_contributed - 1,
            "xirr": strat_xirr,
            # `max_drawdown` is the account-value (DCA-inflow-aware) drawdown
            # of the strategy's portfolio value. The unitized variant is
            # available as `unitized_max_drawdown` and recomputed by the
            # verifier against the equity CSV. Keeping both names explicit
            # avoids the previous bug where both fields were computed from
            # `strategy_nav` (always equal).
            "max_drawdown": max_drawdown(eq["strategy_value"]),
            "unitized_max_drawdown": max_drawdown(eq["strategy_nav"]),
            "volatility": strat_stats["volatility"],
            "sharpe": strat_stats["sharpe"],
            "sortino": strat_stats["sortino"],
            "win_rate": strat_stats["win_rate"],
            "avg_cash_pct": float(eq["cash_pct"].mean()),
            "ending_cash_pct": float(eq["cash_pct"].iloc[-1]),
            "ending_spy_weight": float(eq["spy_weight_actual"].iloc[-1]),
            "ending_qqq_weight": float(eq["qqq_weight_actual"].iloc[-1]),
            "trade_count": len(trades),
            # Overlay contributions (zero when overlay is off).
            "cash_yield_contribution": round(cash_yield_total, 2),
            "slippage_paid": round(slippage_total, 2),
        },
        "benchmark": {
            "final_value": round(final_bench, 2),
            "total_contributed": round(total_contributed, 2),
            "profit": round(final_bench - total_contributed, 2),
            "return_on_contributed": final_bench / total_contributed - 1,
            "xirr": bench_xirr,
            "max_drawdown": max_drawdown(eq["benchmark_value"]),
            "unitized_max_drawdown": max_drawdown(eq["benchmark_nav"]),
            "volatility": bench_stats["volatility"],
            "sharpe": bench_stats["sharpe"],
            "sortino": bench_stats["sortino"],
            "win_rate": bench_stats["win_rate"],
        },
        "relative": {
            "final_value_diff": round(final_strategy - final_bench, 2),
            "xirr_diff": None if strat_xirr is None or bench_xirr is None else strat_xirr - bench_xirr,
            "max_drawdown_diff": max_drawdown(eq["strategy_value"]) - max_drawdown(eq["benchmark_value"]),
            "unitized_max_drawdown_diff": max_drawdown(eq["strategy_nav"]) - max_drawdown(eq["benchmark_nav"]),
        },
        "regime_distribution": {k: round(v, 4) for k, v in signal_dist.items()},
        "multiplier_distribution": {str(k): round(v, 4) for k, v in mult_dist.items()},
        "latest_signal": {
            "execution_date": str(eq.index[-1].date()),
            "signal_date": str(pd.Timestamp(eq["signal_date"].iloc[-1]).date()),
            "date": str(pd.Timestamp(eq["signal_date"].iloc[-1]).date()),
            "regime": str(eq["regime"].iloc[-1]),
            "multiplier": float(eq["multiplier"].iloc[-1]),
            "target_spy_weight": float(eq["target_spy_weight"].iloc[-1]),
            "target_qqq_weight": float(eq["target_qqq_weight"].iloc[-1]),
            "cape": float(eq["cape"].iloc[-1]),
            "vix": float(eq["vix"].iloc[-1]),
            "rsi14": float(eq["rsi14"].iloc[-1]),
            "cash_pct": float(eq["cash_pct"].iloc[-1]),
            "panic_tier": int(eq["panic_tier"].iloc[-1]),
            "satellite_signal": str(eq["satellite_signal"].iloc[-1]),
            "core_spy_weight": float(eq["core_spy_weight"].iloc[-1]),
            "core_qqq_weight": float(eq["core_qqq_weight"].iloc[-1]),
            "satellite_spy_weight": float(eq["satellite_spy_weight"].iloc[-1]),
            "satellite_qqq_weight": float(eq["satellite_qqq_weight"].iloc[-1]),
            "decision_cash_reservoir_pct": float(eq["decision_cash_reservoir_pct"].iloc[-1]),
            "reason": str(eq["reason"].iloc[-1]),
        },
        "recent_trades": trades[-20:],
    }
    return {"result": result, "equity_curve": eq.reset_index(), "trades": trades}


def pct(x: Optional[float]) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x * 100:.2f}%"


def write_outputs(payload: Dict, output_dir: Path, basename: str = "backtest_3y") -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    result = payload["result"]
    eq = payload["equity_curve"]
    trades = payload["trades"]

    (output_dir / f"{basename}_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    eq.to_csv(output_dir / f"{basename}_equity_curve.csv", index=False)
    pd.DataFrame(trades).to_csv(output_dir / f"{basename}_trades.csv", index=False)
    manifest = {
        "strategy_version": result["meta"].get("strategy_version"),
        "generated_at": result["meta"].get("generated_at"),
        "git_commit": result["meta"].get("git_commit"),
        "git_dirty": result["meta"].get("git_dirty"),
        "script_sha256": result["meta"].get("script_sha256"),
        "data_snapshot_sha256": result["meta"].get("data_snapshot_sha256"),
        "price_source": result["meta"].get("price_source"),
        "actual_price_source_spy": result["meta"].get("actual_price_source_spy"),
        "actual_price_source_qqq": result["meta"].get("actual_price_source_qqq"),
        "adjusted_for_dividends": result["meta"].get("adjusted_for_dividends"),
        "price_return_only": result["meta"].get("price_return_only"),
        "cape_source": result["meta"].get("cape_source"),
        "cape_available_lag_bdays": result["meta"].get("cape_available_lag_bdays"),
        "signal_timing": result["meta"].get("signal_timing"),
        "execution_price": result["meta"].get("execution_price"),
        "start": result["meta"].get("start"),
        "end": result["meta"].get("end"),
    }
    (output_dir / f"{basename}_data_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if plt is not None:
        fig, ax = plt.subplots(figsize=(11, 6))
        ax.plot(pd.to_datetime(eq["date"]), eq["strategy_value"], label="Strategy", linewidth=2)
        ax.plot(pd.to_datetime(eq["date"]), eq["benchmark_value"], label="50/50 SPY/QQQ B&H", linewidth=2, alpha=0.8)
        ax.set_title("US ETF Quant System — 3Y Backtest")
        ax.set_ylabel("Portfolio value ($)")
        ax.grid(True, alpha=0.25)
        ax.legend()
        fig.tight_layout()
        fig.savefig(output_dir / f"{basename}_equity_curve.png", dpi=160)
        plt.close(fig)

    s = result["strategy"]
    b = result["benchmark"]
    rel = result["relative"]
    latest = result["latest_signal"]
    report = f"""# US ETF Quant System — 3Y Backtest Report

## Window and assumptions
- Strategy version: {result['meta']['strategy_version']}
- Window: {result['meta']['start']} → {result['meta']['end']} ({result['meta']['trading_days']} trading days)
- Signal/execution: {result['meta']['execution_model']}
- CAPE availability lag: {result['meta']['cape_available_lag_bdays']} business days
- Price source: requested {result['meta'].get('price_source')} / actual SPY {result['meta'].get('actual_price_source_spy')} / actual QQQ {result['meta'].get('actual_price_source_qqq')}
- Adjusted for dividends: {result['meta'].get('adjusted_for_dividends')}; price-return only: {result['meta'].get('price_return_only')}
- CAPE source: {result['meta'].get('cape_source')}
- Contribution schedule: {result['assumptions']['contribution_schedule']}
- Risk-free rate for Sharpe/Sortino: {pct(result['assumptions']['risk_free_rate'])}
- Initial capital: ${result['assumptions']['initial_capital']:,.2f}
- Weekly capital budget: ${result['assumptions']['weekly_budget']:,.2f}
- Transaction cost: {pct(result['assumptions']['transaction_cost'])}
- Benchmark: {result['assumptions']['benchmark']}
- Data caveat: if `price_return_only` is true, dividends are still excluded for both strategy and benchmark.

## Headline results
- Strategy final value: ${s['final_value']:,.2f}
- Benchmark final value: ${b['final_value']:,.2f}
- Strategy profit vs contributed capital: ${s['profit']:,.2f} ({pct(s['return_on_contributed'])})
- Benchmark profit vs contributed capital: ${b['profit']:,.2f} ({pct(b['return_on_contributed'])})
- Relative final value difference: ${rel['final_value_diff']:,.2f}
- Strategy XIRR: {pct(s['xirr'])}
- Benchmark XIRR: {pct(b['xirr'])}
- XIRR difference: {pct(rel['xirr_diff'])}
- Strategy unitized max drawdown: {pct(s['unitized_max_drawdown'])}; account-value drawdown: {pct(s['max_drawdown'])}
- Benchmark unitized max drawdown: {pct(b['unitized_max_drawdown'])}; account-value drawdown: {pct(b['max_drawdown'])}
- Strategy Sharpe / Sortino: {s['sharpe']:.3f} / {s['sortino']:.3f}
- Benchmark Sharpe / Sortino: {b['sharpe']:.3f} / {b['sortino']:.3f}
- Strategy average cash: {pct(s['avg_cash_pct'])}; ending cash: {pct(s['ending_cash_pct'])}

## Latest signal
- Signal date: {latest['signal_date']} → execution date: {latest['execution_date']}
- Regime: {latest['regime']}
- DCA multiplier: {latest['multiplier']:.2f}x
- Target new-buy split: SPY {pct(latest['target_spy_weight'])} / QQQ {pct(latest['target_qqq_weight'])}
- Core/satellite: core SPY {pct(latest['core_spy_weight'])} + QQQ {pct(latest['core_qqq_weight'])}; satellite SPY {pct(latest['satellite_spy_weight'])} + QQQ {pct(latest['satellite_qqq_weight'])} ({latest['satellite_signal']})
- Panic tier: {latest['panic_tier']}; decision cash reservoir: {pct(latest['decision_cash_reservoir_pct'])}
- CAPE: {latest['cape']:.2f}; VIX: {latest['vix']:.2f}; RSI14: {latest['rsi14']:.2f}
- Cash reservoir: {pct(latest['cash_pct'])}
- Reason: {latest['reason']}

## Regime distribution
{json.dumps(result['regime_distribution'], ensure_ascii=False, indent=2)}

## DCA multiplier distribution
{json.dumps(result['multiplier_distribution'], ensure_ascii=False, indent=2)}
"""
    (output_dir / f"{basename}_report.md").write_text(report, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest SPY+QQQ valuation/risk-aware DCA strategy")
    parser.add_argument("--start", default="2023-05-29")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--initial-capital", type=float, default=100_000.0)
    parser.add_argument("--weekly-budget", type=float, default=2_000.0)
    parser.add_argument("--transaction-cost", type=float, default=0.0015)
    parser.add_argument("--slippage-bps", type=float, default=0.0, help="Extra per-fill deduction on top of --transaction-cost, in basis points. 0 = off (canonical).")
    parser.add_argument("--cash-apy", type=float, default=0.0, help="Annualized yield accrued daily on the cash sleeve. 0 = off (canonical). 0.045 ≈ FRED DTB3 trailing 1y average.")
    parser.add_argument(
        "--vol-target-lookback", type=int, default=0,
        help="If >0, enable a vol-targeting smooth layer over the rule multiplier. Window in trading days (63 = quarterly). 0 = off (canonical).",
    )
    parser.add_argument("--vol-target-target", type=float, default=0.12, help="Annualized realized-vol target.")
    parser.add_argument("--vol-target-floor", type=float, default=0.5, help="Lower bound on the vol-target scale.")
    parser.add_argument("--vol-target-ceiling", type=float, default=1.5, help="Upper bound on the vol-target scale.")
    parser.add_argument("--risk-free-rate", type=float, default=0.0, help="Annual risk-free rate used in Sharpe/Sortino excess returns")
    parser.add_argument("--contribution-weekday", default="thursday", help="0-4 or Monday-Friday; first trading day on/after this weekday receives the weekly contribution")
    parser.add_argument("--price-source", choices=sorted(PRICE_SOURCES), default="nasdaq_price_return")
    parser.add_argument("--cape-source", choices=["yale_shiller", "multpl"], default="yale_shiller")
    parser.add_argument("--allow-price-return-fallback", action="store_true", help="Allow adjusted providers to fall back to Nasdaq price-return data if unavailable")
    parser.add_argument("--require-adjusted", action="store_true", help="Fail if actual data source is price-return-only; prevents silent fallback")
    parser.add_argument("--alpha-vantage-api-key", default=None)
    parser.add_argument("--tiingo-api-key", default=None)
    parser.add_argument("--cache-dir", default=None, help="Directory for caching downloaded data (e.g. references/data_cache)")
    parser.add_argument("--cape-vintage-path", default=None, help="Path to CAPE vintage CSV with available_at constraints; overrides --cape-source")
    parser.add_argument(
        "--execution-price",
        choices=sorted(EXECUTION_PRICE_MODES),
        default="next_open",
        help="Trade execution model. Default avoids same-close lookahead.",
    )
    parser.add_argument(
        "--cape-lag-bdays",
        type=int,
        default=10,
        help="Business-day availability lag applied to monthly CAPE observations.",
    )
    parser.add_argument("--output-dir", default="./outputs")
    args = parser.parse_args()

    df = prepare_dataset(
        args.start,
        args.end,
        cape_lag_bdays=args.cape_lag_bdays,
        price_source=args.price_source,
        cape_source=args.cape_source,
        allow_price_return_fallback=args.allow_price_return_fallback,
        alpha_vantage_api_key=args.alpha_vantage_api_key,
        tiingo_api_key=args.tiingo_api_key,
        cache_dir=args.cache_dir,
        require_adjusted=args.require_adjusted,
        cape_vintage_path=args.cape_vintage_path,
    )
    assert_vintage_constraints(df)
    if pd.Timestamp(args.end) > df.index.max():
        # Use latest available market date, common when running before US close.
        args.end = df.index.max().strftime("%Y-%m-%d")
    payload = run_backtest(
        df, args.start, args.end,
        initial_capital=args.initial_capital,
        weekly_budget=args.weekly_budget,
        transaction_cost=args.transaction_cost,
        slippage_bps=args.slippage_bps,
        cash_apy=args.cash_apy,
        vol_target_lookback=args.vol_target_lookback,
        vol_target_target=args.vol_target_target,
        vol_target_floor=args.vol_target_floor,
        vol_target_ceiling=args.vol_target_ceiling,
        execution_price=args.execution_price,
        contribution_weekday=parse_weekday(args.contribution_weekday),
        risk_free_rate=args.risk_free_rate,
    )
    write_outputs(payload, Path(args.output_dir))
    result = payload["result"]
    print(json.dumps({
        "window": f"{result['meta']['start']} -> {result['meta']['end']}",
        "strategy_final": result["strategy"]["final_value"],
        "benchmark_final": result["benchmark"]["final_value"],
        "strategy_xirr": result["strategy"]["xirr"],
        "benchmark_xirr": result["benchmark"]["xirr"],
        "strategy_max_drawdown": result["strategy"]["max_drawdown"],
        "benchmark_max_drawdown": result["benchmark"]["max_drawdown"],
        "execution_model": result["meta"]["execution_model"],
        "price_source": result["meta"]["price_source"],
        "adjusted_for_dividends": result["meta"]["adjusted_for_dividends"],
        "cape_source": result["meta"]["cape_source"],
        "latest_signal": result["latest_signal"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
