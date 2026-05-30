#!/usr/bin/env python3
"""
US ETF Quant System Backtest
============================
SPY + QQQ valuation/risk-aware DCA system for S&P 500 and Nasdaq-100 exposure.

Data sources:
- SPY/QQQ daily OHLCV: Nasdaq public quote API (price return; dividends excluded)
- VIX daily OHLC: Cboe public CSV
- Shiller CAPE monthly: multpl.com HTML table, forward-filled to trade dates

The engine models a fixed weekly capital budget flowing into cash. The strategy
can invest 0x-3x of that weekly budget using accumulated cash reserves; the
benchmark invests exactly 1x weekly into static 50/50 SPY/QQQ. This keeps total
external capital comparable while allowing valuation timing.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

try:
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover - chart is optional
    plt = None

NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


def parse_number(value) -> float:
    """Parse strings like '$754.60', '41,562,560', or 'â 32.58'."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return float("nan")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group(0)) if m else float("nan")


def fetch_nasdaq_ohlcv(symbol: str, start: str, end: str, assetclass: str = "etf") -> pd.DataFrame:
    """Fetch daily OHLCV from Nasdaq public API."""
    url = (
        f"https://api.nasdaq.com/api/quote/{symbol}/historical"
        f"?assetclass={assetclass}&fromdate={start}&todate={end}&limit=9999"
    )
    r = requests.get(url, headers=NASDAQ_HEADERS, timeout=40)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("data") or not payload["data"].get("tradesTable"):
        raise RuntimeError(f"Nasdaq API returned no tradesTable for {symbol}: {payload!r}")
    rows = payload["data"]["tradesTable"].get("rows") or []
    if not rows:
        raise RuntimeError(f"Nasdaq API returned zero rows for {symbol}")
    records = []
    for row in rows:
        records.append({
            "date": pd.to_datetime(row["date"], format="%m/%d/%Y"),
            "open": parse_number(row.get("open")),
            "high": parse_number(row.get("high")),
            "low": parse_number(row.get("low")),
            "close": parse_number(row.get("close")),
            "volume": parse_number(row.get("volume")),
        })
    df = pd.DataFrame.from_records(records).dropna(subset=["date", "close"]).sort_values("date")
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)
    return df[["open", "high", "low", "close", "volume"]]


def fetch_cboe_vix(start: str, end: str) -> pd.DataFrame:
    url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
    r = requests.get(url, headers={"User-Agent": NASDAQ_HEADERS["User-Agent"]}, timeout=40)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip().lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"])
    df = df.rename(columns={"close": "vix"}).set_index("date").sort_index()
    return df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end)), ["vix"]]


def fetch_shiller_cape(start: str, end: str) -> pd.DataFrame:
    url = "https://www.multpl.com/shiller-pe/table/by-month"
    tables = pd.read_html(url)
    raw = tables[0].copy()
    raw.columns = ["date", "cape"]
    raw["date"] = pd.to_datetime(raw["date"], format="mixed")
    raw["cape"] = raw["cape"].map(parse_number)
    raw = raw.dropna(subset=["date", "cape"]).set_index("date").sort_index()
    # Forward-fill monthly CAPE to trading dates later.
    return raw.loc[(raw.index >= pd.Timestamp(start) - pd.DateOffset(months=2)) & (raw.index <= pd.Timestamp(end))]


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def prepare_dataset(start: str, end: str, warmup_days: int = 420) -> pd.DataFrame:
    start_ts = pd.Timestamp(start)
    fetch_start = (start_ts - pd.Timedelta(days=warmup_days)).strftime("%Y-%m-%d")
    spy = fetch_nasdaq_ohlcv("SPY", fetch_start, end).add_prefix("spy_")
    qqq = fetch_nasdaq_ohlcv("QQQ", fetch_start, end).add_prefix("qqq_")
    vix = fetch_cboe_vix(fetch_start, end)
    cape = fetch_shiller_cape(fetch_start, end)

    df = spy.join(qqq, how="inner").join(vix, how="left")
    df["vix"] = df["vix"].ffill()
    df["cape"] = cape["cape"].reindex(df.index, method="ffill")

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
    return df.loc[df.index >= start_ts].dropna(subset=["spy_sma200", "spy_rsi14", "cape", "vix"])


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


def annualized_stats(values: pd.Series, flows: pd.Series) -> Dict[str, float]:
    # Daily return adjusted for external capital flows into the portfolio.
    prev = values.shift(1)
    ret = (values - prev - flows.fillna(0.0)) / prev
    ret = ret.replace([np.inf, -np.inf], np.nan).dropna()
    if ret.empty:
        return {"volatility": 0.0, "sharpe": 0.0, "sortino": 0.0, "win_rate": 0.0}
    vol = float(ret.std() * np.sqrt(252))
    mean_ann = float(ret.mean() * 252)
    downside = ret[ret < 0]
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


def run_backtest(
    df: pd.DataFrame,
    start: str,
    end: str,
    initial_capital: float = 100_000.0,
    weekly_budget: float = 2_000.0,
    transaction_cost: float = 0.0015,
) -> Dict:
    df = df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))].copy()
    if df.empty:
        raise RuntimeError("No data rows in requested backtest window")

    # Start strategy at its target allocation for the first day's regime.
    first = df.iloc[0]
    first_decision = decide(first)
    spy_px0 = float(first["spy_close"])
    qqq_px0 = float(first["qqq_close"])

    cash = 0.0
    spy_shares = initial_capital * first_decision.spy_weight * (1 - transaction_cost) / spy_px0
    qqq_shares = initial_capital * first_decision.qqq_weight * (1 - transaction_cost) / qqq_px0

    bench_cash = 0.0
    bench_spy = initial_capital * 0.5 * (1 - transaction_cost) / spy_px0
    bench_qqq = initial_capital * 0.5 * (1 - transaction_cost) / qqq_px0

    rows = []
    trades = []
    strategy_flows = [(df.index[0], -initial_capital)]
    benchmark_flows = [(df.index[0], -initial_capital)]
    last_contrib_iso = None
    last_trim_month = None

    for dt, row in df.iterrows():
        spy_px = float(row["spy_close"])
        qqq_px = float(row["qqq_close"])
        pre_trade_value = cash + spy_shares * spy_px + qqq_shares * qqq_px
        pre_trade_cash_pct = cash / pre_trade_value if pre_trade_value else 0.0
        dec = apply_cash_reservoir_policy(decide(row), row, pre_trade_cash_pct)
        flow_today = 0.0
        bench_flow_today = 0.0

        # Weekly budget: first trading day on/after Thursday in each ISO week.
        iso = dt.isocalendar()[:2]
        if dt.weekday() >= 3 and iso != last_contrib_iso:
            last_contrib_iso = iso
            cash += weekly_budget
            bench_cash += weekly_budget
            flow_today += weekly_budget
            bench_flow_today += weekly_budget
            strategy_flows.append((dt, -weekly_budget))
            benchmark_flows.append((dt, -weekly_budget))

            pre_buy_value = cash + spy_shares * spy_px + qqq_shares * qqq_px
            pre_buy_cash_pct = cash / pre_buy_value if pre_buy_value else 0.0
            dec = apply_cash_reservoir_policy(decide(row), row, pre_buy_cash_pct)

            invest_amt = min(cash, weekly_budget * dec.multiplier)
            if invest_amt > 0:
                spy_amt = invest_amt * dec.spy_weight
                qqq_amt = invest_amt * dec.qqq_weight
                spy_shares += spy_amt * (1 - transaction_cost) / spy_px
                qqq_shares += qqq_amt * (1 - transaction_cost) / qqq_px
                cash -= invest_amt
                trades.append({
                    "date": str(dt.date()), "action": "DCA_BUY", "amount": round(invest_amt, 2),
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
            bench_spy += b_spy_amt * (1 - transaction_cost) / spy_px
            bench_qqq += b_qqq_amt * (1 - transaction_cost) / qqq_px
            bench_cash -= weekly_budget
            # bench_cash should stay near 0; numerical safety
            if abs(bench_cash) < 1e-9:
                bench_cash = 0.0

        # Monthly trims: once per calendar month, after contribution logic.
        ym = (dt.year, dt.month)
        if ym != last_trim_month and (dec.trim_spy_frac > 0 or dec.trim_qqq_frac > 0):
            sold = 0.0
            if dec.trim_spy_frac > 0 and spy_shares > 0:
                sh = spy_shares * dec.trim_spy_frac
                proceeds = sh * spy_px * (1 - transaction_cost)
                spy_shares -= sh
                cash += proceeds
                sold += proceeds
            if dec.trim_qqq_frac > 0 and qqq_shares > 0:
                sh = qqq_shares * dec.trim_qqq_frac
                proceeds = sh * qqq_px * (1 - transaction_cost)
                qqq_shares -= sh
                cash += proceeds
                sold += proceeds
            if sold > 0:
                last_trim_month = ym
                trades.append({
                    "date": str(dt.date()), "action": "MONTHLY_TRIM", "proceeds": round(sold, 2),
                    "trim_spy_frac": dec.trim_spy_frac, "trim_qqq_frac": dec.trim_qqq_frac,
                    "regime": dec.regime, "reason": dec.reason,
                })

        value = cash + spy_shares * spy_px + qqq_shares * qqq_px
        bench_value = bench_cash + bench_spy * spy_px + bench_qqq * qqq_px
        equity_value = spy_shares * spy_px + qqq_shares * qqq_px
        rows.append({
            "date": dt,
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
            "cape": float(row["cape"]),
            "vix": float(row["vix"]),
            "rsi14": float(row["spy_rsi14"]),
            "spy_close": spy_px,
            "qqq_close": qqq_px,
        })

    eq = pd.DataFrame(rows).set_index("date")
    final_strategy = float(eq["strategy_value"].iloc[-1])
    final_bench = float(eq["benchmark_value"].iloc[-1])
    total_contributed = initial_capital + float(eq["strategy_flow"].sum())
    strategy_flows.append((eq.index[-1], final_strategy))
    benchmark_flows.append((eq.index[-1], final_bench))
    strat_xirr = xirr(strategy_flows)
    bench_xirr = xirr(benchmark_flows)
    strat_stats = annualized_stats(eq["strategy_value"], eq["strategy_flow"])
    bench_stats = annualized_stats(eq["benchmark_value"], eq["benchmark_flow"])

    signal_dist = eq["regime"].value_counts(normalize=True).sort_index().to_dict()
    mult_dist = eq["multiplier"].value_counts(normalize=True).sort_index().to_dict()

    result = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "data_sources": {
                "SPY_QQQ": "Nasdaq public historical quote API; close prices are price-return, dividends excluded",
                "VIX": "Cboe VIX_History.csv",
                "CAPE": "multpl.com Shiller PE table by month",
            },
            "start": str(eq.index[0].date()),
            "end": str(eq.index[-1].date()),
            "trading_days": int(len(eq)),
        },
        "assumptions": {
            "initial_capital": initial_capital,
            "weekly_budget": weekly_budget,
            "transaction_cost": transaction_cost,
            "benchmark": "50/50 SPY/QQQ buy-and-hold plus weekly 1x budget",
            "strategy": "valuation/risk-aware DCA with scheme-8 panic ladder, cash-reservoir cap, 80/20 core-satellite new-buy weights, monthly trim throttle",
        },
        "strategy": {
            "final_value": round(final_strategy, 2),
            "total_contributed": round(total_contributed, 2),
            "profit": round(final_strategy - total_contributed, 2),
            "return_on_contributed": final_strategy / total_contributed - 1,
            "xirr": strat_xirr,
            "max_drawdown": max_drawdown(eq["strategy_value"]),
            "volatility": strat_stats["volatility"],
            "sharpe": strat_stats["sharpe"],
            "sortino": strat_stats["sortino"],
            "win_rate": strat_stats["win_rate"],
            "avg_cash_pct": float(eq["cash_pct"].mean()),
            "ending_cash_pct": float(eq["cash_pct"].iloc[-1]),
            "ending_spy_weight": float(eq["spy_weight_actual"].iloc[-1]),
            "ending_qqq_weight": float(eq["qqq_weight_actual"].iloc[-1]),
            "trade_count": len(trades),
        },
        "benchmark": {
            "final_value": round(final_bench, 2),
            "total_contributed": round(total_contributed, 2),
            "profit": round(final_bench - total_contributed, 2),
            "return_on_contributed": final_bench / total_contributed - 1,
            "xirr": bench_xirr,
            "max_drawdown": max_drawdown(eq["benchmark_value"]),
            "volatility": bench_stats["volatility"],
            "sharpe": bench_stats["sharpe"],
            "sortino": bench_stats["sortino"],
            "win_rate": bench_stats["win_rate"],
        },
        "relative": {
            "final_value_diff": round(final_strategy - final_bench, 2),
            "xirr_diff": None if strat_xirr is None or bench_xirr is None else strat_xirr - bench_xirr,
            "max_drawdown_diff": max_drawdown(eq["strategy_value"]) - max_drawdown(eq["benchmark_value"]),
        },
        "regime_distribution": {k: round(v, 4) for k, v in signal_dist.items()},
        "multiplier_distribution": {str(k): round(v, 4) for k, v in mult_dist.items()},
        "latest_signal": {
            "date": str(eq.index[-1].date()),
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
- Window: {result['meta']['start']} → {result['meta']['end']} ({result['meta']['trading_days']} trading days)
- Initial capital: ${result['assumptions']['initial_capital']:,.2f}
- Weekly capital budget: ${result['assumptions']['weekly_budget']:,.2f}
- Transaction cost: {pct(result['assumptions']['transaction_cost'])}
- Benchmark: {result['assumptions']['benchmark']}
- Data caveat: Nasdaq closes are price-return data; dividends are excluded for both strategy and benchmark.

## Headline results
- Strategy final value: ${s['final_value']:,.2f}
- Benchmark final value: ${b['final_value']:,.2f}
- Strategy profit vs contributed capital: ${s['profit']:,.2f} ({pct(s['return_on_contributed'])})
- Benchmark profit vs contributed capital: ${b['profit']:,.2f} ({pct(b['return_on_contributed'])})
- Relative final value difference: ${rel['final_value_diff']:,.2f}
- Strategy XIRR: {pct(s['xirr'])}
- Benchmark XIRR: {pct(b['xirr'])}
- XIRR difference: {pct(rel['xirr_diff'])}
- Strategy max drawdown: {pct(s['max_drawdown'])}
- Benchmark max drawdown: {pct(b['max_drawdown'])}
- Strategy Sharpe / Sortino: {s['sharpe']:.3f} / {s['sortino']:.3f}
- Benchmark Sharpe / Sortino: {b['sharpe']:.3f} / {b['sortino']:.3f}
- Strategy average cash: {pct(s['avg_cash_pct'])}; ending cash: {pct(s['ending_cash_pct'])}

## Latest signal
- Date: {latest['date']}
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
    parser.add_argument("--output-dir", default="./outputs")
    args = parser.parse_args()

    df = prepare_dataset(args.start, args.end)
    if pd.Timestamp(args.end) > df.index.max():
        # Use latest available market date, common when running before US close.
        args.end = df.index.max().strftime("%Y-%m-%d")
    payload = run_backtest(
        df, args.start, args.end,
        initial_capital=args.initial_capital,
        weekly_budget=args.weekly_budget,
        transaction_cost=args.transaction_cost,
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
        "latest_signal": result["latest_signal"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
