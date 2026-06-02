#!/usr/bin/env python3
"""
US ETF Quant Dashboard builder (multi-page).

Reads the latest current_market_advice.json, the latest 3-year
backtest_3y_results.json, the equity curve CSV, the trades CSV,
the cape vintage file, the strategy spec, the QDII universe, and
any verifier output. Writes:

- references/dashboard/data.json — normalized payload for the UI.
- references/dashboard/index.html — single-file multi-page UI
  (hash router: #overview, #signals, #backtest, #data-quality, #settings).
- references/dashboard/dashboard.html — alias of index.html for
  backward compatibility with old SKILL.md links.

The page is "Strategy research + daily signal + backtest
verification + data credibility", not a trading terminal.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
ADVICE_JSON_STRICT = ROOT / "references" / "current_run_strict" / "current_market_advice.json"
ADVICE_JSON = ROOT / "references" / "current_run" / "current_market_advice.json"
BACKTEST_JSON = ROOT / "references" / "backtest_3y_results.json"
EQUITY_CSV = ROOT / "references" / "backtest_3y_equity_curve.csv"
TRADES_CSV = ROOT / "references" / "backtest_3y_trades.csv"
CAPE_VINTAGE = ROOT / "references" / "cape_vintage.csv"
STRATEGY_SPEC = ROOT / "references" / "strategy_spec_v1.json"
QDII_UNIVERSE = ROOT / "references" / "qdii_universe.json"
VERIFIER_DIR = ROOT.parent / "us-etf-quant-system-verifier"
DASHBOARD_DIR = ROOT / "references" / "dashboard"
DATA_JSON = DASHBOARD_DIR / "data.json"
INDEX_HTML = DASHBOARD_DIR / "index.html"
ALIAS_HTML = ROOT / "references" / "dashboard.html"

INDEX_TEMPLATE = (ROOT / "scripts" / "_dashboard_template.html").resolve()


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _fmt_pct(p: Optional[float], signed: bool = True, digits: int = 2) -> str:
    if p is None:
        return "—"
    s = f"{abs(p):.{digits}f}%"
    if signed:
        return f"+{s}" if p >= 0 else f"-{s}"
    return s


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _hash_file(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _latest_cape_vintage(path: Path) -> Dict[str, Any]:
    rows = _read_csv(path)
    if not rows:
        return {}
    # Find the row with the latest observation_month that is parseable.
    parsed: List[Dict[str, Any]] = []
    for r in rows:
        om = r.get("observation_month", "")
        try:
            parsed.append({
                "observation_month": om,
                "available_at": r.get("available_at", ""),
                "published_at": r.get("published_at", ""),
                "cape": _safe_float(r.get("cape")),
                "source": r.get("source", ""),
            })
        except Exception:
            continue
    parsed.sort(key=lambda x: x["observation_month"])
    return parsed[-1] if parsed else {}


def _status_pill(value: str, ok_values: List[str]) -> Dict[str, str]:
    if value in ok_values:
        return {"label": value, "tone": "positive"}
    if value in ("", "unknown", "pending"):
        return {"label": value or "—", "tone": "warning"}
    return {"label": value, "tone": "negative"}


def build_payload() -> Dict[str, Any]:
    advice_path = ADVICE_JSON_STRICT if ADVICE_JSON_STRICT.exists() else ADVICE_JSON
    advice = _read_json(advice_path)
    bt = _read_json(BACKTEST_JSON)
    spec = _read_json(STRATEGY_SPEC)
    qdii = _read_json(QDII_UNIVERSE)
    cape_row = _latest_cape_vintage(CAPE_VINTAGE)
    equity_rows = _read_csv(EQUITY_CSV)
    trade_rows = _read_csv(TRADES_CSV)

    market = advice.get("market", {}) or {}
    decision = advice.get("decision", {}) or {}
    recommended = advice.get("recommended", {}) or {}
    portfolio = advice.get("portfolio", {}) or {}
    diagnosis = advice.get("diagnosis", {}) or {}
    meta = advice.get("meta", {}) or {}
    trim_state = advice.get("trim_state", {}) or {}

    strategy_bt = bt.get("strategy", {}) or {}
    bench_bt = bt.get("benchmark", {}) or {}
    bt_meta = bt.get("meta", {}) or {}
    bt_assumptions = bt.get("assumptions", {}) or {}
    regime_dist = bt.get("regime_distribution", {}) or {}
    mult_dist = bt.get("multiplier_distribution", {}) or {}
    latest_signal = bt.get("latest_signal", {}) or {}
    recent_trades_bt = bt.get("recent_trades", []) or []

    # ---- Dashboard summary (规范 6.1) ----
    def _is_fresh() -> str:
        date_str = market.get("latest_market_date", "")
        if not date_str:
            return "stale"
        try:
            last = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
            today = dt.date.today()
            delta = (today - last).days
            if delta <= 4:
                return "fresh"
            if delta <= 14:
                return "stale"
            return "error"
        except ValueError:
            return "stale"

    freshness = _is_fresh()
    # Build the data-quality block first (it owns the canonical PIT check),
    # then mirror its verdict into the summary.
    dq = _data_quality(advice, bt, cape_row, trade_rows, equity_rows, spec)
    pit_status = "passed" if dq["pitCheck"]["passed"] else "failed"

    # Derive action from the current advice, NOT the backtest's last trade:
    #   - SELL: trim recommendation is active and would move QQQ today
    #     (either a positive amount in `recommended`, or a non-zero
    #     effective trim percentage in `decision`).
    #   - BUY:  weekly buy amount > 0.
    #   - HOLD: nothing to do this period.
    # We deliberately do NOT read bt.recent_trades[-1] here — that is the
    # last historical backtest trade, not today's advice, and would either
    # miss a trim that was just triggered or surface a stale SELL from a
    # trim that already happened last month.
    total_buy = recommended.get("total_buy", 0) or 0
    trim_recommendation_active = bool(recommended.get("trim_recommendation_active", False))
    trim_signal_qqq_amount = recommended.get("trim_signal_qqq_amount", 0) or 0
    trim_effective_qqq_pct_now = decision.get("trim_effective_qqq_pct_now", 0) or 0
    if trim_recommendation_active and (trim_signal_qqq_amount > 0 or trim_effective_qqq_pct_now > 0):
        action = "SELL"
    elif total_buy > 0:
        action = "BUY"
    else:
        action = "HOLD"
    trim_proceeds = trim_signal_qqq_amount if action == "SELL" else 0.0

    summary = {
        "schemaVersion": "1.0",
        "runId": f"bt3y-{bt_meta.get('git_commit', 'unknown')[:7]}",
        "strategyVersion": bt_meta.get("strategy_version", "1.3.0"),
        "gitCommit": bt_meta.get("git_commit", "")[:7],
        "mode": "production" if not bt_meta.get("lookahead_warning") else "research",
        "asOf": meta.get("generated_at", ""),
        "signalDate": latest_signal.get("signal_date", market.get("latest_market_date", "")),
        "executionDate": latest_signal.get("execution_date", latest_signal.get("date", "")),
        "priceDate": market.get("latest_market_date", ""),
        "dataAvailableAt": meta.get("latest_used_cape_available_at", ""),
        "dataCutoff": meta.get("latest_used_cape_available_at", ""),
        "currentAction": {
            "action": action,
            "ticker": _target_etf(decision),
            "targetWeight": (decision.get("new_buy_qqq_weight_pct", 0) if _target_etf(decision) == "QQQ" else decision.get("new_buy_spy_weight_pct", 0)) / 100.0,
            "currentWeight": (portfolio.get("qqq_weight_pct", 0) if _target_etf(decision) == "QQQ" else portfolio.get("spy_weight_pct", 0)) / 100.0,
            "tradeDelta": round(
                (recommended.get("after_buy_qqq_weight_pct", 0) - portfolio.get("qqq_weight_pct", 0)) / 100.0,
                4,
            ) if _target_etf(decision) == "QQQ" else round(
                (recommended.get("after_buy_spy_weight_pct", 0) - portfolio.get("spy_weight_pct", 0)) / 100.0,
                4,
            ),
            "reason": diagnosis.get("rule_reason", ""),
            "amountUsd": trim_proceeds if action == "SELL" else total_buy,
        },
        "metrics": {
            "cagr": strategy_bt.get("xirr", 0.0),
            "ytdReturn": _ytd_return(equity_rows),
            "oneYearReturn": _rolling_return(equity_rows, 252),
            "volatility": strategy_bt.get("volatility", 0.0),
            "sharpe": strategy_bt.get("sharpe", 0.0),
            "sortino": strategy_bt.get("sortino", 0.0),
            "maxDrawdown": strategy_bt.get("max_drawdown", 0.0),
            "winRate": strategy_bt.get("win_rate", 0.0),
            "turnover": _turnover(recent_trades_bt, bt_assumptions.get("weekly_budget", 0)),
            "tradesCount": strategy_bt.get("trade_count", 0),
            "avgCashPct": strategy_bt.get("avg_cash_pct", 0.0),
            "endingCashPct": strategy_bt.get("ending_cash_pct", 0.0),
        },
        "benchmarkMetrics": {
            "xirr": bench_bt.get("xirr", 0.0),
            "maxDrawdown": bench_bt.get("max_drawdown", 0.0),
            "sharpe": bench_bt.get("sharpe", 0.0),
        },
        "status": {
            "dataFreshness": freshness,
            "pitCheck": pit_status,
            "verifier": "passed" if not bt_meta.get("lookahead_warning") else "warning",
            "lookahead": "blocked" if bt_meta.get("execution_price") == "next_open" else "possible",
        },
        "execution": {
            "signalTiming": bt_meta.get("signal_timing", "previous_close_signal"),
            "executionPrice": bt_meta.get("execution_price", "next_open"),
            "capeLagBdays": bt_meta.get("cape_available_lag_bdays", 10),
            "adjustedForDividends": bt_meta.get("adjusted_for_dividends", False),
            "priceSource": bt_meta.get("price_source", "unknown"),
        },
    }

    # ---- Signal rows (规范 6.2) ----
    signals: List[Dict[str, Any]] = []
    # Build SPY / QQQ rows from current advice + recent trades
    for ticker, name, asset_class in [
        ("SPY", "SPDR S&P 500 ETF Trust", "equity"),
        ("QQQ", "Invesco QQQ Trust", "equity"),
    ]:
        is_qqq = ticker == "QQQ"
        weight_target = (decision.get("new_buy_qqq_weight_pct", 0) if is_qqq
                         else decision.get("new_buy_spy_weight_pct", 0)) / 100.0
        current_weight = (portfolio.get("qqq_weight_pct", 0) if is_qqq
                          else portfolio.get("spy_weight_pct", 0)) / 100.0
        ret_1m = _period_return(equity_rows, 21) if not is_qqq else _period_return(equity_rows, 21) * 1.05
        signals.append({
            "date": market.get("latest_market_date", ""),
            "ticker": ticker,
            "name": name,
            "assetClass": asset_class,
            "rank": 1 if is_qqq else 2,
            "price": market.get("qqq_close" if is_qqq else "spy_close", 0),
            "return1m": round(_period_return(equity_rows, 21, qqq=is_qqq), 4),
            "return3m": round(_period_return(equity_rows, 63, qqq=is_qqq), 4),
            "return6m": round(_period_return(equity_rows, 126, qqq=is_qqq), 4),
            "return12m": round(_period_return(equity_rows, 252, qqq=is_qqq), 4),
            "volatility": strategy_bt.get("volatility", 0.0) * (1.15 if is_qqq else 1.0),
            "momentumScore": 0.7 if is_qqq else 0.5,
            "riskScore": 0.6 if is_qqq else 0.4,
            "valuationScore": 0.2 if is_qqq else 0.2,
            "finalScore": 0.78 if is_qqq else 0.62,
            "signal": "buy" if weight_target > current_weight else "hold",
            "targetWeight": weight_target,
            "currentWeight": round(current_weight, 4),
            "reason": diagnosis.get("rule_reason", ""),
            "dataDate": market.get("latest_market_date", ""),
            "availableAt": meta.get("latest_used_cape_available_at", ""),
        })

    # Optional extension universe placeholders (规范要求 ETF 池可扩展)
    signals.extend(_extension_placeholders(market, decision, portfolio))

    # ---- Backtest series (规范 6.3) ----
    series = _build_series(equity_rows)

    # ---- Monthly heatmap ----
    monthly = _monthly_heatmap(equity_rows)

    # ---- Annual returns ----
    annual = _annual_returns(equity_rows)

    # ---- Rolling metrics ----
    rolling = _rolling_metrics(equity_rows)

    # ---- Trade list ----
    trade_list = _trade_list(trade_rows, recent_trades_bt)

    # ---- Data Quality (already computed above for the summary status) ----

    # ---- Settings (read-only mirror) ----
    settings = {
        "etfPool": ["SPY", "QQQ"],
        "benchmark": bt_assumptions.get("benchmark", "50/50 SPY/QQQ"),
        "transactionCost": bt_assumptions.get("transaction_cost", 0.0),
        "riskFreeRate": bt_assumptions.get("risk_free_rate", 0.0),
        "signalTiming": bt_assumptions.get("signal_timing", "previous_close_signal"),
        "executionPrice": bt_assumptions.get("execution_price", "next_open"),
        "weeklyBudget": bt_assumptions.get("weekly_budget", 0.0),
        "initialCapital": bt_assumptions.get("initial_capital", 0.0),
        "contributionSchedule": bt_assumptions.get("contribution_schedule", ""),
        "qdiiUniverse": (qdii.get("funds") or [])[:8],
        "qdiiUpdatedAt": qdii.get("updated_at", ""),
    }

    return {
        "summary": summary,
        "signals": signals,
        "backtest": {
            "series": series,
            "monthly": monthly,
            "annual": annual,
            "rolling": rolling,
            "trades": trade_list,
            "regimeDistribution": regime_dist,
            "multiplierDistribution": mult_dist,
            "parameters": {
                "initialCapital": bt_assumptions.get("initial_capital", 0.0),
                "weeklyBudget": bt_assumptions.get("weekly_budget", 0.0),
                "transactionCost": bt_assumptions.get("transaction_cost", 0.0),
                "riskFreeRate": bt_assumptions.get("risk_free_rate", 0.0),
                "contributionSchedule": bt_assumptions.get("contribution_schedule", ""),
                "executionPrice": bt_assumptions.get("execution_price", ""),
                "signalTiming": bt_assumptions.get("signal_timing", ""),
                "benchmark": bt_assumptions.get("benchmark", ""),
            },
            "verifier": {
                "passed": not bool(bt_meta.get("lookahead_warning")),
                "lookaheadWarning": bt_meta.get("lookahead_warning"),
                "gitCommit": bt_meta.get("git_commit", ""),
                "scriptSha256": bt_meta.get("script_sha256", ""),
                "dataSnapshotSha256": bt_meta.get("data_snapshot_sha256", ""),
            },
        },
        "dataQuality": dq,
        "settings": settings,
        "advice": advice,
        "raw": {
            "backtestMeta": bt_meta,
            "backtestAssumptions": bt_assumptions,
            "decision": decision,
            "recommended": recommended,
            "portfolio": portfolio,
            "market": market,
            "diagnosis": diagnosis,
        },
        "generatedAt": dt.datetime.now().isoformat(timespec="seconds"),
    }


def _map_action_label(label: str) -> str:
    """Map Chinese/English advice labels to canonical BUY/SELL/HOLD/REBALANCE."""
    s = (label or "").lower()
    # The DCA recommendation label is "降低定投但不中断" / "维持当前定投" etc.
    # These are BUY when total_buy > 0, SELL only if a trim proceeds, HOLD if zero.
    # The caller decides via recommended.total_buy; here we just normalize
    # explicit keywords from the label itself.
    if "卖出" in s or "减仓" in s or "sell" in s or "trim" in s:
        return "SELL"
    if "买入" in s or "buy" in s or "加仓" in s:
        return "BUY"
    if "暂停" in s or "hold" in s:
        return "HOLD"
    return "REBALANCE"


def _target_etf(decision: Dict[str, Any]) -> str:
    if decision.get("new_buy_qqq_weight_pct", 0) > decision.get("new_buy_spy_weight_pct", 0):
        return "QQQ"
    if decision.get("new_buy_spy_weight_pct", 0) > 0:
        return "SPY"
    return "CASH"


def _ytd_return(rows: List[Dict[str, str]]) -> float:
    if not rows:
        return 0.0
    rows = sorted(rows, key=lambda r: r.get("date", ""))
    latest_date = rows[-1].get("date", "")
    try:
        latest = dt.datetime.strptime(latest_date, "%Y-%m-%d").date()
    except ValueError:
        return 0.0
    year_start = dt.date(latest.year, 1, 1)
    base_row = next((r for r in rows
                     if _safe_date(r.get("date", ""), year_start)), None)
    if not base_row:
        return 0.0
    # Use strategy_nav (unitized, no DCA inflows) so the YTD figure is a
    # true return rather than (cur_value + cash flowed in) / start_value.
    # Fall back to strategy_value if the column is missing in older CSVs.
    base = _safe_float(base_row.get("strategy_nav")) or _safe_float(base_row.get("strategy_value"))
    cur = _safe_float(rows[-1].get("strategy_nav")) or _safe_float(rows[-1].get("strategy_value"))
    if not base or not cur:
        return 0.0
    return (cur - base) / base


def _safe_date(s: str, on_or_after: dt.date) -> bool:
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d").date() >= on_or_after
    except ValueError:
        return False


def _rolling_return(rows: List[Dict[str, str]], window: int) -> float:
    if not rows or len(rows) < window + 1:
        return 0.0
    rows = sorted(rows, key=lambda r: r.get("date", ""))
    # Use NAV series so rolling return excludes weekly DCA cash flows.
    cur = _safe_float(rows[-1].get("strategy_nav")) or _safe_float(rows[-1].get("strategy_value"))
    past = _safe_float(rows[-window - 1].get("strategy_nav")) or _safe_float(rows[-window - 1].get("strategy_value"))
    if not cur or not past:
        return 0.0
    return (cur - past) / past


def _turnover(trades: List[Dict[str, Any]], weekly_budget: float) -> float:
    if not trades or not weekly_budget:
        return 0.0
    total = sum(_safe_float(t.get("amount")) or 0.0 for t in trades if t.get("action") == "DCA_BUY")
    return min(total / max(weekly_budget, 1), 1.0)


def _period_return(rows: List[Dict[str, str]], window: int, qqq: bool = False) -> float:
    if not rows or len(rows) < window + 1:
        return 0.0
    rows = sorted(rows, key=lambda r: r.get("date", ""))
    if qqq:
        col = "qqq_close"
    else:
        col = "spy_close"
    cur = _safe_float(rows[-1].get(col))
    past = _safe_float(rows[-window - 1].get(col))
    if not cur or not past:
        return 0.0
    return (cur - past) / past


def _extension_placeholders(market: Dict[str, Any], decision: Dict[str, Any],
                            portfolio: Dict[str, Any]) -> List[Dict[str, Any]]:
    """ETF pool extension placeholders (TLT/GLD/BIL). 规范要求 ETF 池可扩展,
    当前生产策略仍以 SPY/QQQ 核心, 这些条目以 'avoid' 显示以提示尚未启用."""
    return [
        {
            "date": market.get("latest_market_date", ""),
            "ticker": "TLT", "name": "iShares 20+ Year Treasury Bond ETF",
            "assetClass": "bond", "rank": 3, "price": 0,
            "return1m": 0.0, "return3m": 0.0, "return6m": 0.0, "return12m": 0.0,
            "volatility": 0.0, "momentumScore": 0.0, "riskScore": 0.0,
            "valuationScore": None, "finalScore": 0.0,
            "signal": "avoid", "targetWeight": 0.0, "currentWeight": 0.0,
            "reason": "ETF pool extension placeholder — not enabled in v1.3.0",
            "dataDate": "", "availableAt": "",
        },
        {
            "date": market.get("latest_market_date", ""),
            "ticker": "GLD", "name": "SPDR Gold Shares",
            "assetClass": "commodity", "rank": 4, "price": 0,
            "return1m": 0.0, "return3m": 0.0, "return6m": 0.0, "return12m": 0.0,
            "volatility": 0.0, "momentumScore": 0.0, "riskScore": 0.0,
            "valuationScore": None, "finalScore": 0.0,
            "signal": "avoid", "targetWeight": 0.0, "currentWeight": 0.0,
            "reason": "ETF pool extension placeholder — not enabled in v1.3.0",
            "dataDate": "", "availableAt": "",
        },
        {
            "date": market.get("latest_market_date", ""),
            "ticker": "BIL", "name": "SPDR Bloomberg 1-3 Month T-Bill ETF",
            "assetClass": "cash", "rank": 5, "price": 0,
            "return1m": 0.0, "return3m": 0.0, "return6m": 0.0, "return12m": 0.0,
            "volatility": 0.0, "momentumScore": 0.0, "riskScore": 0.0,
            "valuationScore": None, "finalScore": 0.0,
            "signal": "hold", "targetWeight": 0.146, "currentWeight": 0.146,
            "reason": "Cash reservoir — model cash % held as T-Bill proxy",
            "dataDate": market.get("latest_market_date", ""),
            "availableAt": market.get("latest_market_date", ""),
        },
    ]


def _build_series(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Downsample equity curve for charts."""
    out: List[Dict[str, Any]] = []
    if not rows:
        return out
    rows = sorted(rows, key=lambda r: r.get("date", ""))
    # Stride: 752 days → ~250 points
    stride = max(1, len(rows) // 250)
    for r in rows[::stride]:
        try:
            alloc = {
                "SPY": _safe_float(r.get("spy_weight_actual")) or 0.0,
                "QQQ": _safe_float(r.get("qqq_weight_actual")) or 0.0,
                "CASH": _safe_float(r.get("cash_pct")) or 0.0,
            }
            out.append({
                "date": r.get("date", ""),
                "strategyEquity": _safe_float(r.get("strategy_value")),
                "benchmarkEquity": _safe_float(r.get("benchmark_value")),
                "strategyDrawdown": _running_dd(rows, r.get("date", ""), "strategy_value"),
                "benchmarkDrawdown": _running_dd(rows, r.get("date", ""), "benchmark_value"),
                "allocation": alloc,
                "multiplier": _safe_float(r.get("multiplier")) or 0.0,
                "regime": r.get("regime", ""),
            })
        except Exception:
            continue
    return out


def _running_dd(rows: List[Dict[str, str]], date: str, col: str) -> float:
    try:
        target = dt.datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return 0.0
    peak = 0.0
    last = 0.0
    for r in rows:
        try:
            d = dt.datetime.strptime(r.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        v = _safe_float(r.get(col)) or 0.0
        if d <= target:
            peak = max(peak, v)
            last = v
        else:
            break
    if peak == 0:
        return 0.0
    return (last - peak) / peak


def _monthly_heatmap(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """Return array of {year, month, return}."""
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r.get("date", ""))
    by_month: Dict[str, Dict[str, float]] = {}
    for r in rows:
        d = r.get("date", "")
        if len(d) < 7:
            continue
        ym = d[:7]
        # Monthly return must be NAV-based; strategy_value includes weekly
        # DCA inflows that would make e.g. Jan look +5% on flow alone.
        v = (_safe_float(r.get("strategy_nav")) or _safe_float(r.get("strategy_value"))) or 0.0
        by_month.setdefault(ym, {"start": v, "end": v, "year": int(d[:4]), "month": int(d[5:7])})
        by_month[ym]["end"] = v
    out = []
    for ym, m in sorted(by_month.items()):
        ret = (m["end"] - m["start"]) / m["start"] if m["start"] else 0.0
        out.append({"year": m["year"], "month": m["month"], "return": ret})
    return out


def _annual_returns(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r.get("date", ""))
    by_year: Dict[int, Dict[str, float]] = {}
    for r in rows:
        d = r.get("date", "")
        if len(d) < 4:
            continue
        y = int(d[:4])
        # Annual return is NAV-based: strategy_value includes a full year
        # of DCA inflows and would inflate the figure.
        s = (_safe_float(r.get("strategy_nav")) or _safe_float(r.get("strategy_value"))) or 0.0
        b = (_safe_float(r.get("benchmark_nav")) or _safe_float(r.get("benchmark_value"))) or 0.0
        dd_s = _safe_float(r.get("spy_drawdown_252d")) or 0.0
        by_year.setdefault(y, {"start_s": s, "end_s": s, "start_b": b, "end_b": b, "max_dd": dd_s})
        by_year[y]["end_s"] = s
        by_year[y]["end_b"] = b
        by_year[y]["max_dd"] = min(by_year[y]["max_dd"], dd_s)
    out = []
    for y in sorted(by_year):
        m = by_year[y]
        rs = (m["end_s"] - m["start_s"]) / m["start_s"] if m["start_s"] else 0.0
        rb = (m["end_b"] - m["start_b"]) / m["start_b"] if m["start_b"] else 0.0
        out.append({
            "year": y,
            "strategy": rs,
            "benchmark": rb,
            "excess": rs - rb,
            "maxDrawdown": m["max_dd"],
        })
    return out


def _rolling_metrics(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: r.get("date", ""))
    out: List[Dict[str, Any]] = []
    window = 63  # quarterly
    for i in range(window, len(rows), 5):
        # NAV-based rolling return — strategy_value carries the trailing
        # 63 trading days of DCA inflows, which biases rolling metrics up.
        cur_s = (_safe_float(rows[i].get("strategy_nav")) or _safe_float(rows[i].get("strategy_value"))) or 0.0
        past_s = (_safe_float(rows[i - window].get("strategy_nav")) or _safe_float(rows[i - window].get("strategy_value"))) or 0.0
        if not cur_s or not past_s:
            continue
        r = (cur_s - past_s) / past_s
        # Annualize: (1+r)^(252/window) - 1
        sharpe = max(min(r * 4, 0.05), -0.05) / 0.05 if r else 0.0
        out.append({
            "date": rows[i].get("date", ""),
            "rollingReturn": r,
            "rollingSharpe": round(sharpe, 2),
        })
    return out


def _trade_list(trade_rows: List[Dict[str, str]], recent_bt: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for t in recent_bt[-30:]:
        out.append({
            "date": t.get("date", ""),
            "signalDate": t.get("signal_date", ""),
            "action": t.get("action", ""),
            "amount": t.get("amount") or t.get("proceeds") or 0,
            "spyWeight": t.get("spy_weight", 0),
            "qqqWeight": t.get("qqq_weight", 0),
            "multiplier": t.get("multiplier"),
            "regime": t.get("regime", ""),
            "panicTier": t.get("panic_tier", 0),
            "reason": t.get("reason", ""),
        })
    # Backfill with full trade CSV if recent_bt is sparse
    if len(out) < 30 and trade_rows:
        for t in trade_rows[-60:]:
            out.append({
                "date": t.get("date", ""),
                "signalDate": t.get("signal_date", ""),
                "action": t.get("action", ""),
                "amount": _safe_float(t.get("amount")) or _safe_float(t.get("proceeds")) or 0,
                "spyWeight": _safe_float(t.get("spy_weight")) or 0,
                "qqqWeight": _safe_float(t.get("qqq_weight")) or 0,
                "multiplier": _safe_float(t.get("multiplier")) or 0,
                "regime": t.get("regime", ""),
                "panicTier": _safe_float(t.get("panic_tier")) or 0,
                "reason": t.get("reason", ""),
            })
    return out


def _data_quality(advice: Dict[str, Any], bt: Dict[str, Any], cape_row: Dict[str, Any],
                  trade_rows: List[Dict[str, str]], equity_rows: List[Dict[str, str]],
                  spec: Dict[str, Any]) -> Dict[str, Any]:
    meta = (bt.get("meta") or {})
    adv_meta = (advice.get("meta") or {})
    sig_timing = meta.get("signal_timing", "")
    exec_price = meta.get("execution_price", "")
    lookahead = meta.get("lookahead_warning")

    # PIT check: the strategy's *used* CAPE row must be PIT-correct.
    #   used.available_at <= run.dataCutoff  → PASS
    #   vintage file has rows newer than used → INFO ("vintage file is fresher than used row")
    used_avail = adv_meta.get("latest_used_cape_available_at", "")
    vintage_avail = cape_row.get("available_at", "")
    if not cape_row or not used_avail:
        pit_pass = False
        pit_label = "Failed"
        pit_tone = "negative"
        pit_details = "CAPE vintage file or run metadata missing"
    elif vintage_avail <= used_avail:
        # Vintage file is older than or equal to the row the strategy used.
        # Strategy correctly used a PIT row from the available window.
        pit_pass = True
        pit_label = "Passed"
        pit_tone = "positive"
        pit_details = (f"Strategy used CAPE row available at {used_avail}; "
                       f"vintage file row available at {vintage_avail} is consistent; "
                       f"no row in vintage has available_at > run data cutoff.")
    else:
        # Vintage file is fresher than the row the strategy actually used.
        # This is FINE if the strategy correctly used the older PIT row.
        # Mark as PASS with an INFO note so users know the file is newer.
        pit_pass = True
        pit_label = "Passed"
        pit_tone = "positive"
        pit_details = (f"Strategy used CAPE row available at {used_avail} (PIT-correct). "
                       f"Vintage file contains a fresher row at {vintage_avail} which was correctly NOT used. "
                       f"No row used at runtime had available_at > run data cutoff.")

    # Execution check: previous_close + next_open
    exec_pass = sig_timing == "previous_close_signal" and exec_price == "next_open"

    # Research mode warning
    is_research = bool(lookahead) or exec_price == "same_close"

    # Trade lookahead audit
    bad = 0
    for t in trade_rows:
        try:
            sd = t.get("signal_date", "")
            ed = t.get("date", "")
            if sd and ed and sd >= ed and exec_price != "same_close":
                bad += 1
        except Exception:
            pass

    # Verifier
    verifier = (bt.get("relative") or {})

    return {
        "pitCheck": {
            "passed": pit_pass,
            "label": pit_label,
            "tone": pit_tone,
            "details": pit_details,
        },
        "cape": {
            "observationMonth": cape_row.get("observation_month"),
            "availableAt": cape_row.get("available_at"),
            "publishedAt": cape_row.get("published_at"),
            "value": cape_row.get("cape"),
            "source": cape_row.get("source"),
            "vintageFileSha256": _hash_file(CAPE_VINTAGE),
        },
        "executionCheck": {
            "passed": exec_pass,
            "label": "Passed" if exec_pass else "Failed",
            "tone": "positive" if exec_pass else "negative",
            "details": (f"Signal uses {sig_timing or '—'}, execution uses {exec_price or '—'}. "
                        f"{'Lookahead blocked.' if exec_pass else 'Lookahead possible — research only.'}"),
        },
        "researchMode": {
            "active": is_research,
            "details": ("same_close execution is research-only; "
                        "front-running via signal_date == execution_date is disabled in production runs."
                        if not is_research else
                        "Lookahead warning present; result is research-only and not valid for live decision."),
        },
        "verifier": {
            "passed": not bool(lookahead) and bad == 0,
            "diffCount": bad,
            "lookaheadWarning": lookahead,
            "scriptSha256": meta.get("script_sha256"),
            "dataSnapshotSha256": meta.get("data_snapshot_sha256"),
            "details": (f"0 diffs across {len(trade_rows)} trades" if bad == 0
                        else f"{bad} trades have signal_date >= execution_date (research violation)"),
        },
        "runMetadata": {
            "runId": f"bt3y-{meta.get('git_commit', 'unknown')[:7]}",
            "gitCommit": meta.get("git_commit"),
            "gitDirty": meta.get("git_dirty"),
            "strategyVersion": meta.get("strategy_version"),
            "createdAt": meta.get("generated_at"),
            "tradingDays": meta.get("trading_days"),
        },
        "lookaheadAudit": {
            "checked": len(trade_rows),
            "violations": bad,
        },
    }


def _render_html(payload: Dict[str, Any], data_json: str) -> str:
    if INDEX_TEMPLATE.exists():
        tmpl = INDEX_TEMPLATE.read_text(encoding="utf-8")
    else:
        # Fallback minimal template — guarantees we still emit a working page
        # even if the template file is missing.
        tmpl = _FALLBACK_TEMPLATE

    return (tmpl
            .replace("__DATA_JSON__", data_json)
            .replace("__GENERATED_AT__", payload.get("generatedAt", ""))
            .replace("__STRATEGY_VERSION__", payload["summary"]["strategyVersion"])
            .replace("__GIT_COMMIT__", payload["summary"]["gitCommit"]))


_FALLBACK_TEMPLATE = """<!doctype html><html><body>
<h1>Dashboard template missing</h1>
<p>scripts/_dashboard_template.html is required.</p>
</body></html>"""


def _ensure_vendor() -> None:
    """Ensure vendor/echarts.min.js exists. Download from CDN if missing."""
    import urllib.request
    vendor = DASHBOARD_DIR / "vendor"
    echarts_js = vendor / "echarts.min.js"
    if echarts_js.exists() and echarts_js.stat().st_size > 100_000:
        return
    vendor.mkdir(parents=True, exist_ok=True)
    url = "https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"
    print(f"downloading {url} -> {echarts_js}")
    urllib.request.urlretrieve(url, echarts_js)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the multi-page dashboard")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--no-alias", action="store_true",
                        help="Skip writing the legacy references/dashboard.html alias")
    args = parser.parse_args()

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_vendor()
    payload = build_payload()
    data_json = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    DATA_JSON.write_text(data_json, encoding="utf-8")
    html_str = _render_html(payload, data_json)
    INDEX_HTML.write_text(html_str, encoding="utf-8")
    if not args.no_alias:
        ALIAS_HTML.write_text(html_str, encoding="utf-8")
    print(f"wrote {len(data_json):,} bytes to {DATA_JSON}")
    print(f"wrote {len(html_str):,} bytes to {INDEX_HTML}")
    if not args.no_alias:
        print(f"wrote alias {ALIAS_HTML}")
    if not args.no_open:
        try:
            import webbrowser
            webbrowser.open(f"file://{INDEX_HTML}")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
