#!/usr/bin/env python3
"""Generate request-time US ETF market diagnosis and actionable DCA/rebalance advice.

This script is intentionally a thin advisory layer on top of backtest_us_etf.py:
- fetches the latest available SPY/QQQ/VIX/CAPE data,
- applies the production decision rules,
- optionally reads the user's portfolio_config.json,
- writes Chinese Markdown + JSON outputs.

It is decision support, not automated trading or personal financial advice.
"""
from __future__ import annotations

import argparse
import datetime as dt

import pandas as pd
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def load_backtest_module(skill_dir: Path):
    script = skill_dir / "scripts" / "backtest_us_etf.py"
    spec = importlib.util.spec_from_file_location("us_etf_backtest", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {script}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["us_etf_backtest"] = mod
    spec.loader.exec_module(mod)
    return mod


def assert_latest_cape_pit(df, latest_market_close) -> None:
    """Defensive PIT check: refuse to emit advice if the chosen CAPE was not
    actually available at the latest market close.

    The CAPE vintage file is built such that no row claims available_at after
    downloaded_at (see scripts/update_cape_snapshot.py: _resolve_available_at),
    but as a belt-and-braces guard we re-verify the invariant at request time.
    If the chosen CAPE observation is in the future relative to the signal date,
    raise AssertionError so the run aborts with a clear message instead of
    silently emitting advice based on a value that did not exist.
    """
    latest_avail_str = df.attrs.get("latest_used_cape_available_at", "")
    if not latest_avail_str:
        return
    try:
        latest_avail = pd.Timestamp(latest_avail_str)
    except Exception:
        return
    if pd.isna(latest_avail):
        return
    if latest_avail > pd.Timestamp(latest_market_close):
        raise AssertionError(
            f"CAPE PIT violation: latest_used_cape_available_at ({latest_avail.date()}) > "
            f"latest_market_close ({pd.Timestamp(latest_market_close).date()}). "
            f"The CAPE was not actually available at the signal date — refusing to emit advice."
        )


def pct(x: float, digits: int = 2) -> str:
    return f"{x * 100:.{digits}f}%"


def classify_market(m: Dict[str, Any]) -> str:
    if m["cape"] >= 42 and m["trend_up"] and m["vix"] < 20:
        return "极高估值 + 强趋势 + 低波动：牛市惯性仍在，但追高性价比差"
    if not m["trend_up"] and m["vix"] >= 25:
        return "风险释放/风险关闭：跌破长期趋势且波动抬升"
    if m["trend_up"] and m["trend_strong"] and m["vix"] < 20:
        return "趋势健康、波动平稳"
    return "中性/分歧状态：需要等待趋势、波动或估值给出更清晰信号"


def action_label(multiplier: float, trim_signal: float) -> str:
    if trim_signal >= 0.10:
        return "减仓防守"
    if trim_signal > 0:
        return "小幅锁盈"
    if multiplier >= 1.5:
        return "加大定投/逢低加仓"
    if multiplier > 1.0:
        return "略增定投"
    if multiplier == 1.0:
        return "正常定投"
    if multiplier >= 0.75:
        return "降低定投但不中断"
    if multiplier > 0:
        return "显著降低定投"
    return "暂停定投"


def trim_signal_for(cape: float, rsi: float, trend_up: bool, vix: float) -> tuple[float, str]:
    if cape >= 42 and rsi >= 75:
        return 0.03, "CAPE>=42 and RSI>=75"
    if cape >= 40 and rsi >= 78:
        return 0.03, "CAPE>=40 and RSI>=78"
    if (not trend_up) and vix >= 30:
        return 0.10, "SPY below SMA200 and VIX>=30"
    return 0.0, "none"


def load_trim_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def evaluate_trim_state(
    state: Dict[str, Any],
    state_file: Path,
    market_date: str,
    trim_signal: float,
    trim_reason: str,
) -> Dict[str, Any]:
    month = market_date[:7]
    last_trim = (state.get("last_trim") or {}).get("qqq")
    already = bool(last_trim and last_trim.get("month") == month)
    detected = trim_signal > 0
    active = detected and not already
    return {
        "state_file": str(state_file),
        "month": month,
        "signal_detected": detected,
        "raw_trim_qqq_pct": round(trim_signal * 100, 2),
        "effective_trim_qqq_pct": round((trim_signal if active else 0.0) * 100, 2),
        "reason": trim_reason,
        "already_executed_this_month": already,
        "recommendation_active": active,
        "last_trim": last_trim,
    }


def record_trim_execution(state_file: Path, payload: Dict[str, Any]) -> bool:
    trim_state = payload.get("trim_state") or {}
    if not trim_state.get("signal_detected"):
        return False
    state = load_trim_state(state_file)
    state.setdefault("last_trim", {})["qqq"] = {
        "month": trim_state["month"],
        "date": payload["market"]["latest_market_date"],
        "reason": trim_state["reason"],
        "raw_trim_qqq_pct": trim_state["raw_trim_qqq_pct"],
        "recorded_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def build_payload(args: argparse.Namespace) -> Dict[str, Any]:
    skill_dir = Path(args.skill_dir).expanduser().resolve()
    bt = load_backtest_module(skill_dir)
    end = args.end or dt.date.today().isoformat()
    df = bt.prepare_dataset(
        args.start,
        end,
        cape_lag_bdays=int(args.cape_lag_bdays),
        price_source=args.price_source,
        cape_source=args.cape_source,
        allow_price_return_fallback=bool(args.allow_price_return_fallback),
        alpha_vantage_api_key=args.alpha_vantage_api_key,
        tiingo_api_key=getattr(args, "tiingo_api_key", None),
        cache_dir=getattr(args, "cache_dir", None),
        require_adjusted=getattr(args, "require_adjusted", False),
        cape_vintage_path=getattr(args, "cape_vintage_path", None),
    )
    if df.empty:
        raise RuntimeError("No market data available after warmup")
    assert_latest_cape_pit(df, df.index.max())
    row = df.iloc[-1]

    # Compute SPY/QQQ daily returns (current day close vs previous day close)
    prev_row = df.iloc[-2] if len(df) >= 2 else None
    spy_daily_return_pct = None
    qqq_daily_return_pct = None
    if prev_row is not None:
        spy_daily_return_pct = round((float(row["spy_close"]) / float(prev_row["spy_close"]) - 1) * 100, 2)
        qqq_daily_return_pct = round((float(row["qqq_close"]) / float(prev_row["qqq_close"]) - 1) * 100, 2)

    cfg: Optional[Dict[str, Any]] = None
    if args.portfolio_config:
        cfg_path = Path(args.portfolio_config).expanduser()
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    weekly_for_model = float(args.weekly_budget or (cfg or {}).get("plan", {}).get("amount", 2000.0))
    model_cash_pct: Optional[float] = None
    if getattr(args, "model_cash_reservoir", True):
        model_run = bt.run_backtest(
            df,
            args.start,
            str(row.name.date()),
            initial_capital=float(getattr(args, "initial_capital", 100_000.0)),
            weekly_budget=weekly_for_model,
            transaction_cost=float(getattr(args, "transaction_cost", 0.0015)),
        )
        model_cash_pct = float(model_run["result"]["latest_signal"]["cash_pct"])

    dec = bt.apply_cash_reservoir_policy(bt.decide(row), row, model_cash_pct)

    market = {
        "latest_market_date": str(row.name.date()),
        "spy_close": round(float(row["spy_close"]), 2),
        "qqq_close": round(float(row["qqq_close"]), 2),
        "spy_sma50": round(float(row["spy_sma50"]), 2),
        "spy_sma200": round(float(row["spy_sma200"]), 2),
        "spy_vs_sma200_pct": round((float(row["spy_close"]) / float(row["spy_sma200"]) - 1) * 100, 2),
        "spy_sma50_vs_sma200_pct": round((float(row["spy_sma50"]) / float(row["spy_sma200"]) - 1) * 100, 2),
        "spy_rsi14": round(float(row["spy_rsi14"]), 2),
        "spy_ret_21d_pct": round(float(row["spy_ret_21d"]) * 100, 2),
        "spy_drawdown_252d_pct": round(float(row["spy_drawdown_252d"]) * 100, 2),
        "vix": round(float(row["vix"]), 2),
        "vix_sma20": round(float(row["vix_sma20"]), 2),
        "cape": round(float(row["cape"]), 2),
        "qqq_rel_63d_pct": round(float(row["qqq_rel_63d"]) * 100, 2),
        "qqq_rel_126d_pct": round(float(row.get("qqq_rel_126d", 0.0)) * 100, 2),
        "qqq_trend_up": bool(row.get("qqq_trend_up", True)),
        "spy_daily_return_pct": spy_daily_return_pct,
        "qqq_daily_return_pct": qqq_daily_return_pct,
        "trend_up": bool(row["trend_up"]),
        "trend_strong": bool(row["trend_strong"]),
        "risk_off": bool(row["risk_off"]),
    }

    trim_signal, trim_reason = trim_signal_for(
        market["cape"], market["spy_rsi14"], market["trend_up"], market["vix"]
    )
    trim_state_file = Path(args.trim_state_file).expanduser()
    trim_state = evaluate_trim_state(
        load_trim_state(trim_state_file),
        trim_state_file,
        market["latest_market_date"],
        trim_signal,
        trim_reason,
    )
    effective_trim_signal = trim_signal if trim_state["recommendation_active"] else 0.0

    recent_signals = []
    for hist_date, hist_row in df.tail(max(1, int(args.recent_days))).iterrows():
        hist_dec = bt.decide(hist_row)
        hist_trim, hist_trim_reason = trim_signal_for(
            float(hist_row["cape"]),
            float(hist_row["spy_rsi14"]),
            bool(hist_row["trend_up"]),
            float(hist_row["vix"]),
        )
        recent_signals.append({
            "date": str(hist_date.date()),
            "action": action_label(float(hist_dec.multiplier), hist_trim),
            "dca_multiplier": float(hist_dec.multiplier),
            "spy_buy_weight_pct": round(float(hist_dec.spy_weight) * 100, 2),
            "qqq_buy_weight_pct": round(float(hist_dec.qqq_weight) * 100, 2),
            "panic_tier": int(hist_dec.panic_tier),
            "satellite_signal": hist_dec.satellite_signal,
            "trim_qqq_pct": round(hist_trim * 100, 2),
            "trim_reason": hist_trim_reason,
            "cape": round(float(hist_row["cape"]), 2),
            "vix": round(float(hist_row["vix"]), 2),
            "rsi14": round(float(hist_row["spy_rsi14"]), 2),
            "spy_vs_sma200_pct": round((float(hist_row["spy_close"]) / float(hist_row["spy_sma200"]) - 1) * 100, 2),
            "qqq_rel_63d_pct": round(float(hist_row["qqq_rel_63d"]) * 100, 2),
            "regime": hist_dec.regime,
            "reason": hist_dec.reason,
        })

    portfolio: Optional[Dict[str, Any]] = None
    recommended: Optional[Dict[str, Any]] = None
    if cfg is not None:
        funds = cfg["holdings"]["funds"]
        spy_value = sum(float(x["value"]) for x in funds if "标普500" in x["name"] or "S&P" in x["name"].upper())
        qqq_value = sum(float(x["value"]) for x in funds if "纳斯达克100" in x["name"] or "NASDAQ" in x["name"].upper())
        total = float(cfg["holdings"]["total_value"])
        weekly = weekly_for_model
        buy_amount = weekly * float(dec.multiplier)
        buy_spy = buy_amount * float(dec.spy_weight)
        buy_qqq = buy_amount * float(dec.qqq_weight)
        after_total = total + buy_amount
        after_spy = spy_value + buy_spy
        after_qqq = qqq_value + buy_qqq
        diagnostic_shift = max(0.0, after_qqq - after_total * float(dec.qqq_weight))
        qqq_trim_amount = qqq_value * effective_trim_signal
        portfolio = {
            "total_value": round(total, 2),
            "spy_value": round(spy_value, 2),
            "qqq_value": round(qqq_value, 2),
            "spy_weight_pct": round(spy_value / total * 100, 2) if total else None,
            "qqq_weight_pct": round(qqq_value / total * 100, 2) if total else None,
            "weekly_plan": weekly,
            "model_cash_reservoir_pct": round(model_cash_pct * 100, 2) if model_cash_pct is not None else None,
        }
        recommended = {
            "action": action_label(float(dec.multiplier), effective_trim_signal),
            "weekly_budget_base": weekly,
            "dca_multiplier": float(dec.multiplier),
            "total_buy": round(buy_amount, 2),
            "spy_buy": round(buy_spy, 2),
            "qqq_buy": round(buy_qqq, 2),
            "new_buy_spy_weight_pct": round(float(dec.spy_weight) * 100, 2),
            "new_buy_qqq_weight_pct": round(float(dec.qqq_weight) * 100, 2),
            "core_spy_weight_pct": round(float(dec.core_spy_weight) * 100, 2),
            "core_qqq_weight_pct": round(float(dec.core_qqq_weight) * 100, 2),
            "satellite_spy_weight_pct": round(float(dec.satellite_spy_weight) * 100, 2),
            "satellite_qqq_weight_pct": round(float(dec.satellite_qqq_weight) * 100, 2),
            "satellite_signal": dec.satellite_signal,
            "panic_tier": int(dec.panic_tier),
            "model_cash_reservoir_pct": round(model_cash_pct * 100, 2) if model_cash_pct is not None else None,
            "after_buy_spy_weight_pct": round(after_spy / after_total * 100, 2),
            "after_buy_qqq_weight_pct": round(after_qqq / after_total * 100, 2),
            "trim_signal_qqq_pct": round(effective_trim_signal * 100, 2),
            "trim_signal_qqq_pct_raw": round(trim_signal * 100, 2),
            "trim_signal_qqq_amount": round(qqq_trim_amount, 2),
            "trim_reason": trim_reason,
            "trim_already_executed_this_month": trim_state["already_executed_this_month"],
            "trim_recommendation_active": trim_state["recommendation_active"],
            "diagnostic_shift_qqq_to_spy_to_match_new_buy_target": round(diagnostic_shift, 2),
        }

    price_source_label = str(df.attrs.get("price_source", args.price_source))
    cape_source_label = str(df.attrs.get("cape_source", args.cape_source))
    spy_actual = str(df.attrs.get("actual_price_source_spy", price_source_label))
    qqq_actual = str(df.attrs.get("actual_price_source_qqq", price_source_label))
    adj = df.attrs.get("adjusted_for_dividends", False)

    if price_source_label == "yahoo_chart_adjusted":
        spyy_desc = "Yahoo Finance chart API; adjClose dividend-adjustment factor applied to OHLCV"
    elif price_source_label == "tiingo_adjusted":
        spyy_desc = "Tiingo API; adjOpen/adjHigh/adjLow/adjClose/adjVolume with dividend adjustment"
    elif price_source_label == "alpha_vantage_adjusted":
        spyy_desc = "Alpha Vantage TIME_SERIES_DAILY_ADJUSTED; split and dividend adjusted"
    elif price_source_label == "nasdaq_price_return":
        spyy_desc = "Nasdaq public historical quote API; dividends excluded (price-return only)"
    else:
        spyy_desc = f"{price_source_label}"

    if "vintage" in cape_source_label.lower():
        vintage_name = Path(str(df.attrs.get("cape_vintage_path", ""))).name
        cape_desc = f"CAPE vintage file with available_at constraint: {vintage_name}"
    elif "yale" in cape_source_label.lower() or "shiller" in cape_source_label.lower():
        cape_desc = "Yale Shiller ie_data monthly CAPE with BDay publication lag"
    elif "multpl" in cape_source_label.lower():
        cape_desc = "multpl.com Shiller PE monthly table with BDay publication lag"
    else:
        cape_desc = f"{cape_source_label} CAPE"

    vintage_path_val = df.attrs.get("cape_vintage_path", "")
    used_obs = df.attrs.get("latest_used_cape_observation_month", "")
    used_avail = df.attrs.get("latest_used_cape_available_at", "")

    return {
        "meta": {
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "skill_dir": str(skill_dir),
            "data_sources": {
                "SPY_QQQ": spyy_desc,
                "VIX": "Cboe VIX_History.csv",
                "CAPE": cape_desc,
            },
            "signal_timing": "latest completed close signal for next available execution",
            "cape_available_lag_bdays": int(args.cape_lag_bdays),
            "price_source": price_source_label,
            "actual_price_source_spy": spy_actual,
            "actual_price_source_qqq": qqq_actual,
            "cape_source": cape_source_label,
            "cape_vintage_path": vintage_path_val,
            "latest_used_cape_observation_month": used_obs,
            "latest_used_cape_available_at": used_avail,
            "adjusted_for_dividends": bool(adj),
            "price_return_only": bool(df.attrs.get("price_return_only")),
        },
        "market": market,
        "diagnosis": {
            "summary": classify_market(market),
            "regime": dec.regime,
            "rule_reason": dec.reason,
        },
        "decision": {
            "dca_multiplier": float(dec.multiplier),
            "new_buy_spy_weight_pct": round(float(dec.spy_weight) * 100, 2),
            "new_buy_qqq_weight_pct": round(float(dec.qqq_weight) * 100, 2),
            "core_spy_weight_pct": round(float(dec.core_spy_weight) * 100, 2),
            "core_qqq_weight_pct": round(float(dec.core_qqq_weight) * 100, 2),
            "satellite_spy_weight_pct": round(float(dec.satellite_spy_weight) * 100, 2),
            "satellite_qqq_weight_pct": round(float(dec.satellite_qqq_weight) * 100, 2),
            "satellite_signal": dec.satellite_signal,
            "panic_tier": int(dec.panic_tier),
            "model_cash_reservoir_pct": round(model_cash_pct * 100, 2) if model_cash_pct is not None else None,
            "trim_spy_pct": round(float(dec.trim_spy_frac) * 100, 2),
            "trim_qqq_pct_rule": round(float(dec.trim_qqq_frac) * 100, 2),
            "trim_signal_qqq_pct_now": round(trim_signal * 100, 2),
            "trim_effective_qqq_pct_now": round(effective_trim_signal * 100, 2),
            "trim_reason_now": trim_reason,
            "trim_already_executed_this_month": trim_state["already_executed_this_month"],
            "trim_recommendation_active": trim_state["recommendation_active"],
            "action_label": action_label(float(dec.multiplier), effective_trim_signal),
        },
        "trim_state": trim_state,
        "portfolio": portfolio,
        "recommended": recommended,
        "recent_signals": recent_signals,
        "logic": {
            "increase_dca_when": [
                "方案8恐慌阶梯：回撤>=8%且RSI<=40，或VIX>=28且RSI<=40 -> 1.25x",
                "方案8恐慌阶梯：回撤>=15%或VIX>=35 -> 1.5x；回撤>=22%或VIX>=45 -> 2.0x",
                "现金池过高且SPY在SMA200上方、VIX<25 -> 提高定投底线，避免长期现金拖累",
                "CAPE<30 -> 1.25x或更高的估值驱动积累",
            ],
            "decrease_dca_when": [
                "CAPE>=35且RSI>=70 -> 定投上限0.75x",
                "VIX>=25且未超卖 -> 定投上限0.5x",
                "SPY低于SMA200且没有方案8恐慌确认 -> 定投上限0.5x",
            ],
            "new_buy_allocation": [
                "方案11B：新增资金先拆成80%核心仓（标普40%+纳指40%）和20%卫星仓",
                "卫星仓由QQQ/SPY 63日、126日相对强弱、QQQ是否在SMA200上方、VIX和恐慌层级决定",
                "极端估值且RSI过热时，卫星仓转向标普；QQQ强趋势时，卫星仓转向纳指",
            ],
            "trim_when": [
                "CAPE>=42 and RSI>=75 -> monthly QQQ micro-trim 3%",
                "CAPE>=40 and RSI>=78 -> monthly QQQ profit-lock trim 3%",
                "SPY below SMA200 and VIX>=30 -> QQQ risk-off trim 10%",
            ],
        },
    }


def write_report(payload: Dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "current_market_advice.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    m = payload["market"]
    d = payload["decision"]
    trim_state = payload.get("trim_state") or {}
    recent = payload.get("recent_signals") or []
    p = payload.get("portfolio")
    r = payload.get("recommended")
    trim_status = "本月已记录，不重复建议" if trim_state.get("already_executed_this_month") else (
        "本次有效" if trim_state.get("recommendation_active") else "未触发"
    )
    lines = [
        "# 美股 ETF 请求时刻市场诊断与操作建议",
        "",
        f"生成时间：{payload['meta']['generated_at']}",
        f"最新市场交易日：{m['latest_market_date']}",
        "",
        "## 一句话结论",
        f"{payload['diagnosis']['summary']}。操作标签：**{d['action_label']}**。",
        "",
        "## 核心市场数据",
        f"- SPY：{m['spy_close']}；今日涨幅：{m['spy_daily_return_pct']}%；相对 SMA200：{m['spy_vs_sma200_pct']}%；SMA50 相对 SMA200：{m['spy_sma50_vs_sma200_pct']}%\n"
        f"- QQQ：{m['qqq_close']}；今日涨幅：{m['qqq_daily_return_pct']}%；QQQ/SPY 63日相对强弱：{m['qqq_rel_63d_pct']}%；126日相对强弱：{m['qqq_rel_126d_pct']}%；QQQ趋势：{m['qqq_trend_up']}",
        f"- RSI14：{m['spy_rsi14']}；近21日 SPY：{m['spy_ret_21d_pct']}%；252日回撤：{m['spy_drawdown_252d_pct']}%",
        f"- VIX：{m['vix']}；VIX 20日均值：{m['vix_sma20']}",
        f"- Shiller CAPE：{m['cape']}；Regime：{payload['diagnosis']['regime']}",
        f"- Trend up：{m['trend_up']}；Trend strong：{m['trend_strong']}；Risk off：{m['risk_off']}",
        "",
        "## 系统建议",
        f"- 定投倍率：{d['dca_multiplier']}x；恐慌层级：{d['panic_tier']}；模型现金池：{d['model_cash_reservoir_pct']}%",
        f"- 新买入分配：SPY/标普 {d['new_buy_spy_weight_pct']}%，QQQ/纳指 {d['new_buy_qqq_weight_pct']}%",
        f"- 11B拆分：核心仓 标普/纳指 {d['core_spy_weight_pct']}%/{d['core_qqq_weight_pct']}%；卫星仓 标普/纳指 {d['satellite_spy_weight_pct']}%/{d['satellite_qqq_weight_pct']}%；信号：{d['satellite_signal']}",
        f"- 当前减仓信号：规则触发 QQQ {d['trim_signal_qqq_pct_now']}%；本次有效 QQQ {d['trim_effective_qqq_pct_now']}%；状态：{trim_status}；原因：{d['trim_reason_now']}",
        f"- 规则依据：{payload['diagnosis']['rule_reason']}",
    ]
    if recent:
        lines += [
            "",
            f"## 近 {len(recent)} 个交易日建议变化",
        ]
        for s in recent:
            lines.append(
                f"- {s['date']}：{s['action']}；定投 {s['dca_multiplier']}x；"
                f"买入标普/纳指 {s['spy_buy_weight_pct']}%/{s['qqq_buy_weight_pct']}%；"
                f"恐慌层级 {s['panic_tier']}，卫星 {s['satellite_signal']}；"
                f"CAPE {s['cape']}，RSI {s['rsi14']}，VIX {s['vix']}，SPY距SMA200 {s['spy_vs_sma200_pct']}%，QQQ相对强弱 {s['qqq_rel_63d_pct']}%；"
                f"QQQ减仓信号 {s['trim_qqq_pct']}%"
            )
    if p and r:
        lines += [
            "",
            "## 按当前组合换算",
            f"- 当前总市值：{p['total_value']}",
            f"- 标普/SPY类：{p['spy_value']}（{p['spy_weight_pct']}%）",
            f"- 纳指/QQQ类：{p['qqq_value']}（{p['qqq_weight_pct']}%）",
            f"- 原周定投：{r['weekly_budget_base']}；建议本期买入：{r['total_buy']}；模型现金池：{r['model_cash_reservoir_pct']}%",
            f"- 本期标普/SPY类买入：{r['spy_buy']}；纳指/QQQ类买入：{r['qqq_buy']}",
            f"- 11B拆分：核心 标普/纳指 {r['core_spy_weight_pct']}%/{r['core_qqq_weight_pct']}%；卫星 标普/纳指 {r['satellite_spy_weight_pct']}%/{r['satellite_qqq_weight_pct']}%；信号 {r['satellite_signal']}；恐慌层级 {r['panic_tier']}",
            f"- 买入后预估权重：标普 {r['after_buy_spy_weight_pct']}%，纳指 {r['after_buy_qqq_weight_pct']}%",
            f"- 本月减仓去重：已执行={r['trim_already_executed_this_month']}；本次有效={r['trim_recommendation_active']}；有效QQQ减仓金额={r['trim_signal_qqq_amount']}",
            f"- 若强行对齐本期新买入风险目标，诊断性换仓额约：{r['diagnostic_shift_qqq_to_spy_to_match_new_buy_target']} 从纳指转标普；默认不建议一次性完成，只作为风险暴露参考。",
        ]
    lines += [
        "",
        "## 触发器",
        "- 方案8加仓：回撤≥8%且RSI≤40或VIX≥28且RSI≤40 -> 1.25x；回撤≥15%或VIX≥35 -> 1.5x；回撤≥22%或VIX≥45 -> 2.0x。",
        "- 现金池融合：模型现金池>25%且趋势/波动支持时，提高定投底线，避免长期现金拖累。",
        "- 11B分配：80%核心仓固定标普/纳指40/40，20%卫星仓按QQQ/SPY相对强弱、QQQ趋势、VIX和恐慌层级切换。",
        "- 减仓：CAPE>=42 且 RSI>=75 时月度微减 QQQ 3%；跌破SMA200且VIX>=30时减 QQQ 10%。",
    ]
    (output_dir / "current_market_advice.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    default_skill_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill-dir", default=str(default_skill_dir))
    parser.add_argument("--start", default="2023-05-29")
    parser.add_argument("--end", default=None)
    parser.add_argument("--portfolio-config", default="/Users/bytedance/.hermes/portfolio_config.json")
    parser.add_argument("--weekly-budget", type=float, default=None)
    parser.add_argument("--initial-capital", type=float, default=100_000.0, help="Initial capital for model cash-reservoir simulation")
    parser.add_argument("--transaction-cost", type=float, default=0.0015)
    parser.add_argument("--cape-lag-bdays", type=int, default=10, help="Business-day availability lag applied to monthly CAPE observations")
    parser.add_argument("--price-source", choices=sorted(load_backtest_module(default_skill_dir).PRICE_SOURCES), default="nasdaq_price_return")
    parser.add_argument("--cape-source", choices=["yale_shiller", "multpl"], default="yale_shiller")
    parser.add_argument("--allow-price-return-fallback", action="store_true")
    parser.add_argument("--require-adjusted", action="store_true", help="Fail if actual data source is price-return-only")
    parser.add_argument("--alpha-vantage-api-key", default=None)
    parser.add_argument("--tiingo-api-key", default=None)
    parser.add_argument("--cache-dir", default=None, help="Directory for caching downloaded data")
    parser.add_argument("--cape-vintage-path", default=None, help="Path to CAPE vintage CSV with available_at constraints")
    parser.add_argument("--no-model-cash-reservoir", dest="model_cash_reservoir", action="store_false", help="Disable simulated cash-reservoir adjustment in request-time advice")
    parser.set_defaults(model_cash_reservoir=True)
    parser.add_argument("--trim-state-file", default=str(default_skill_dir / "references" / "cron_run" / ".trim_state.json"), help="Persistent state used to avoid repeated monthly trim advice. Default lives inside the skill so it travels with the repo; the legacy ~/.hermes/us_etf_trim_state.json path still works as a symlink.")
    parser.add_argument("--record-trim-execution", action="store_true", help="Record the current QQQ trim signal as executed in the trim state file")
    parser.add_argument("--recent-days", type=int, default=3, help="Number of recent trading days to include in the advice report")
    parser.add_argument("--output-dir", default=str(default_skill_dir / "references" / "current_run"))
    args = parser.parse_args()
    payload = build_payload(args)
    recorded_trim_execution = False
    if args.record_trim_execution:
        recorded_trim_execution = record_trim_execution(Path(args.trim_state_file).expanduser(), payload)
    write_report(payload, Path(args.output_dir).expanduser())
    print(json.dumps({
        "generated_at": payload["meta"]["generated_at"],
        "latest_market_date": payload["market"]["latest_market_date"],
        "summary": payload["diagnosis"]["summary"],
        "action": payload["decision"]["action_label"],
        "dca_multiplier": payload["decision"]["dca_multiplier"],
        "new_buy_spy_weight_pct": payload["decision"]["new_buy_spy_weight_pct"],
        "new_buy_qqq_weight_pct": payload["decision"]["new_buy_qqq_weight_pct"],
        "panic_tier": payload["decision"]["panic_tier"],
        "satellite_signal": payload["decision"]["satellite_signal"],
        "model_cash_reservoir_pct": payload["decision"]["model_cash_reservoir_pct"],
        "trim_state": payload.get("trim_state"),
        "recorded_trim_execution": recorded_trim_execution,
        "recommended": payload.get("recommended"),
        "output_dir": str(Path(args.output_dir).expanduser()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
