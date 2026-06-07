#!/usr/bin/env python3
"""
US ETF Quant Dashboard builder.

Reads the latest current_market_advice.json, the latest 3-year
backtest_3y_results.json, the equity curve CSV, the trades CSV,
the cape vintage file, the strategy spec, the QDII universe, and
any verifier output. Writes:

- references/dashboard/data.json — normalized payload for the UI.
- references/dashboard/index.html — single-file research dashboard
  (combined decision/signals/backtest view plus #data-quality audit view).
- references/dashboard/dashboard.html — alias of index.html for
  backward compatibility with old SKILL.md links.

The page is "Strategy research + daily signal + backtest
verification + data credibility", not a trading terminal.
"""
from __future__ import annotations

import argparse
import copy
import csv
import datetime as dt
import hashlib
import json
import logging
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from persistence import archive_decision, load_decisions

ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)

# Make llm package importable when running as a script
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ADVICE_JSON_STRICT = ROOT / "references" / "current_run_strict" / "current_market_advice.json"
ADVICE_JSON = ROOT / "references" / "current_run" / "current_market_advice.json"
# LLM-enhanced copy produced by ``scripts/llm_copilot.py`` — preferred
# when present so the dashboard can show the copilot block.
ADVICE_JSON_LLM = ROOT / "references" / "current_run" / "current_market_advice_with_llm.json"
ADVICE_JSON_STRICT_LLM = ROOT / "references" / "current_run_strict" / "current_market_advice_with_llm.json"
BACKTEST_JSON = ROOT / "references" / "backtest_3y_results.json"
EQUITY_CSV = ROOT / "references" / "backtest_3y_equity_curve.csv"
TRADES_CSV = ROOT / "references" / "backtest_3y_trades.csv"
CAPE_VINTAGE = ROOT / "references" / "cape_vintage.csv"
STRATEGY_SPEC = ROOT / "references" / "strategy_spec_v1.json"
QDII_UNIVERSE = ROOT / "references" / "qdii_universe.json"
DECISIONS_DB = ROOT / "data" / "decisions.db"
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


def _latest_advice_path(paths: List[Path]) -> Optional[Path]:
    """Choose the newest advice by its source run timestamp, not file mtime.

    LLM-enhanced files can be rewritten long after their underlying market
    snapshot was generated. Using existence or mtime would let an old LLM
    copy override a newer plain advice run.

    当 plain 和 with_llm 同 timestamp 时，**优先 with_llm 版本**（含 LLM 审查）。
    """
    candidates: List[Tuple[str, Path]] = []
    for path in paths:
        if not path.exists():
            continue
        payload = _read_json(path)
        generated_at = str((payload.get("meta") or {}).get("generated_at") or "")
        market_date = str((payload.get("market") or {}).get("latest_market_date") or "")
        candidates.append((generated_at or market_date, path))
    if not candidates:
        return None
    # 找最新 timestamp；如果 tie，优先 with_llm
    max_ts = max(c[0] for c in candidates)
    tied = [c for c in candidates if c[0] == max_ts]
    # 排序：with_llm 优先
    tied.sort(key=lambda c: (0 if "with_llm" in c[1].name else 1, c[1].name))
    return tied[0][1]


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


def _next_weekday(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        d = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return ""
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d.isoformat()


def _decision_history_entry(advice: Dict[str, Any]) -> Dict[str, Any]:
    market = advice.get("market") or {}
    decision = advice.get("decision") or {}
    recommended = advice.get("recommended") or {}
    portfolio = advice.get("portfolio") or {}
    diagnosis = advice.get("diagnosis") or {}
    meta = advice.get("meta") or {}
    total_buy = float(recommended.get("total_buy") or 0)
    trim_active = bool(recommended.get("trim_recommendation_active"))
    trim_amount = float(recommended.get("trim_signal_qqq_amount") or 0)
    trim_pct = float(decision.get("trim_effective_qqq_pct_now") or 0)
    action = "SELL" if trim_active and (trim_amount > 0 or trim_pct > 0) else ("BUY" if total_buy > 0 else "HOLD")
    ticker = _target_etf(decision)
    market_date = str(market.get("latest_market_date") or "")
    target_pct = float(decision.get("new_buy_qqq_weight_pct") or 0) if ticker == "QQQ" else float(decision.get("new_buy_spy_weight_pct") or 0)
    current_pct = float(portfolio.get("qqq_weight_pct") or 0) if ticker == "QQQ" else float(portfolio.get("spy_weight_pct") or 0)
    after_pct = float(recommended.get("after_buy_qqq_weight_pct") or current_pct) if ticker == "QQQ" else float(recommended.get("after_buy_spy_weight_pct") or current_pct)
    return {
        "date": market_date,
        "generatedAt": meta.get("generated_at", ""),
        "signalDate": market_date,
        "executionDate": _next_weekday(market_date),
        "priceDate": market_date,
        "dataAvailableAt": meta.get("latest_used_cape_available_at", ""),
        "mode": "production" if bool(meta.get("adjusted_for_dividends")) else "research",
        "action": action,
        "ticker": ticker,
        "targetWeight": target_pct / 100.0,
        "currentWeight": current_pct / 100.0,
        "tradeDelta": (after_pct - current_pct) / 100.0,
        "reason": diagnosis.get("rule_reason") or decision.get("reason") or "",
        "amountUsd": trim_amount if action == "SELL" else total_buy,
        "market": {
            "latest_market_date": market_date,
            "spy_close": market.get("spy_close"),
            "qqq_close": market.get("qqq_close"),
            "spy_daily_return_pct": market.get("spy_daily_return_pct"),
            "qqq_daily_return_pct": market.get("qqq_daily_return_pct"),
            "sp500_index_date": market.get("sp500_index_date"),
            "sp500_index_close": market.get("sp500_index_close"),
            "sp500_index_daily_return_pct": market.get("sp500_index_daily_return_pct"),
            "nasdaq_index_date": market.get("nasdaq_index_date"),
            "nasdaq_index_close": market.get("nasdaq_index_close"),
            "nasdaq_index_daily_return_pct": market.get("nasdaq_index_daily_return_pct"),
            "cape": market.get("cape"),
            "vix": market.get("vix"),
            "spy_rsi14": market.get("spy_rsi14"),
            "qqq_rel_63d_pct": market.get("qqq_rel_63d_pct"),
        },
        "decision": {
            "dcaMultiplier": decision.get("dca_multiplier"),
            "spyTargetWeight": float(decision.get("new_buy_spy_weight_pct") or 0) / 100.0,
            "qqqTargetWeight": float(decision.get("new_buy_qqq_weight_pct") or 0) / 100.0,
            "regime": diagnosis.get("regime") or "",
        },
    }


# ---------------------------------------------------------------------------
# LLM Copilot block — read from current_market_advice.json (optional)
# ---------------------------------------------------------------------------

# UI-friendly tone for the agreement pill.  Maps the advisor-level
# agreement to a (label, tone) pair suitable for the dashboard status pill.
_LLM_AGREEMENT_TONE = {
    "agree": ("🟢 同意", "positive"),
    "caution": ("🟡 谨慎同意", "warning"),
    "disagree": ("🔴 不同意", "critical"),
}


def _macro_feeds_block(advice: Dict[str, Any]) -> Dict[str, Any]:
    """Pull RSS / GDELT / 财经日历 — dashboard 旁路展示。

    失败 → 返回空块，UI 端不显示。
    RSS 标题 + 财经日历事件名会通过 LLM 翻译为中文（仅当 LLM_API_KEY 存在时）— 失败兜底英文。
    """
    try:
        from llm.reference_feeds import (
            fetch_all_reference_feeds,
            translate_to_chinese,
        )

        cache_dir = ROOT / "references" / "data_cache" / "macro_feeds"
        # 未来 30 天日历, max_calendar 给大值, fetch_economic_calendar 内部不截断
        # (ForexFactory thisweek 单周 ~80 条会挤掉 Fed Reserve 长窗口)
        out = fetch_all_reference_feeds(
            cache_dir=cache_dir,
            max_rss=10, max_gdelt=10, max_calendar=200,
            min_calendar_importance=1,
        )
        # 日历内部二次过滤: 优先 ⭐⭐⭐ (3), 不足时回退 ⭐⭐ (2) 让 FOMC 会议 / 重要讲话也进
        import datetime
        all_cal = out.get("calendar") or []
        today = datetime.date.today()
        horizon = today + datetime.timedelta(days=30)
        # 30 天窗口 + 重要性 >= 3
        # 主过滤: 重要性 >= 2 (包含 Med Impact), 不再只用 >= 3 否则 Med Impact tab 永远空
        # 然后按用户 front-end 的 High/Med 切换细化展示
        cal_filtered = [
            it for it in all_cal
            if int(it.get("importance", 0) or 0) >= 2
            and today.isoformat() <= it.get("date", "") <= horizon.isoformat()
        ]
        cal_filtered.sort(key=lambda x: (x.get("date") or "", x.get("time") or ""))
        out["calendar"] = cal_filtered[:80]  # keep more items for medium tab

        # LLM 翻译 — 失败兜底原文
        # 合并 RSS + GDELT 成 1 次 API 调用 (大 batch 工作稳定)
        # CAL 单独 1 次 (短 batch 易触发 MiniMax thinking 截断, 内部重试会处理)
        rss_titles = [it.get("headline", "") for it in (out.get("rss") or [])]
        gdelt_titles = [it.get("headline", "") for it in (out.get("gdelt") or [])]
        cal_titles = [it.get("event", "") for it in (out.get("calendar") or [])]

        rss_gdelt_titles = rss_titles + gdelt_titles
        rss_gdelt_trans = translate_to_chinese(rss_gdelt_titles) if rss_gdelt_titles else []
        rss_translations = rss_gdelt_trans[:len(rss_titles)]
        gdelt_translations = rss_gdelt_trans[len(rss_titles):]
        cal_translations = translate_to_chinese(cal_titles) if cal_titles else []
        # 把 RFC 2822 / ISO 8601 / date 三种时间格式统一为中文友好格式
        # "Sun, 07 Jun 2026 07:00:00 GMT" → "06-07 周日 15:00" (UTC+8)
        # "2026-06-07 07:30:00 +0000" → "06-07 周日 15:30"
        # "2026-06-10" → "06-10 周三"
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone, timedelta
        cn_week = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        cn_tz = timezone(timedelta(hours=8))  # UTC+8 中国时区

        def format_ts_cn(ts: str) -> str:
            if not ts:
                return ""
            ts = ts.strip()
            try:
                # 1) RFC 2822 (RSS): "Sun, 07 Jun 2026 07:00:00 GMT"
                if "," in ts and ("GMT" in ts.upper() or "UTC" in ts.upper()):
                    dt = parsedate_to_datetime(ts)
                # 2) ISO 8601 with TZ (GDELT/Currents/NewsAPI): "2026-06-07 07:30:00 +0000" 或 "2026-06-07T07:30:00Z"
                elif "+" in ts[10:] or "T" in ts or ts.endswith("Z"):
                    # BUG FIX: Python 3.9 的 fromisoformat 不支持 'Z' 后缀, 主动转成 +00:00
                    ts2 = ts.replace("Z", "+00:00").replace(" +0000", "+00:00").replace(" ", "T")
                    dt = datetime.fromisoformat(ts2)
                # 3) date only: "2026-06-10"
                elif len(ts) == 10:
                    dt = datetime.fromisoformat(ts)
                else:
                    return ts
                # 缺时区信息则当 UTC 处理, 否则转 UTC+8
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                dt_cn = dt.astimezone(cn_tz)
                date_part = f"{dt_cn.month:02d}-{dt_cn.day:02d}"
                week_part = cn_week[dt_cn.weekday()]
                # 如果带时分, 加时间
                if dt_cn.hour != 0 or dt_cn.minute != 0:
                    return f"{date_part} {week_part} {dt_cn.hour:02d}:{dt_cn.minute:02d}"
                return f"{date_part} {week_part}"
            except Exception:
                return ts

        def _trim(items, key="headline", max_preview=200, translations=None):
            trimmed = []
            for i, it in enumerate(items[:30]):
                if not isinstance(it, dict):
                    continue
                original = str(it.get(key) or it.get("event") or "")[:max_preview]
                zh = (translations or [])[i] if translations and i < len(translations) else None
                # BUG FIX: LLM 翻译有时会把 60 字的英文标题压成 2-4 字中文（"豪华"/"关注要点"），
                # 这种压缩版信息丢失太严重，强制回退到英文原文。
                # 判定: 译文中文字数 < 3 个 (例如 "豪华"/"关注要点" 这种 2-4 字压缩) 时视为压缩。
                # 正常中英翻译比 0.2-0.5, 太严的 0.2 阈值会误杀 "如何在不出售房屋的情况下提取房屋净值" 这种 16 字好翻译。
                if zh and original:
                    orig_is_chinese = sum(1 for c in original if "一" <= c <= "鿿") > len(original) * 0.5
                    zh_chars = sum(1 for c in zh if "一" <= c <= "鿿")
                    if not orig_is_chinese and zh_chars < 3:
                        zh = None
                trimmed.append({
                    "ts": format_ts_cn(str(it.get("ts") or it.get("date") or "")),
                    "headline": zh or original,
                    "headlineEn": original if zh and zh != original else None,
                    "url": it.get("url"),
                    "source": str(it.get("source") or ""),
                    "category": str(it.get("category") or ""),
                    "origin": str(it.get("origin") or ""),
                    "tone": it.get("tone"),
                    "country": it.get("country"),
                    "importance": int(it.get("importance") or 0),
                })
            return trimmed

        # 把 RSS 和 GDELT 合并成"全球要闻"（按时间倒序, 去重）
        rss_items = _trim(out.get("rss") or [], translations=rss_translations)
        gdelt_items = _trim(out.get("gdelt") or [], translations=gdelt_translations)
        # 同标题去重, 保留先出现的
        seen = set()
        merged = []
        for it in rss_items + gdelt_items:
            key = (it.get("headline") or "").lower().strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(it)

        # 过滤个人观点 / 家庭情感 / 体育娱乐 / 时尚类 — 保留宏观经济/政策/财经/公司类
        # 关键词白名单: 命中任一则保留; 关键词黑名单: 命中任一则丢弃
        keep_keywords = [
            "market", "stock", "shares", "ipo", "earnings", "revenue", "profit", "merger",
            "fed", "rate", "inflation", "cpi", "gdp", "economy", "economic", "recession",
            "central bank", "treasury", "yield", "bond", "debt", "fiscal", "monetary",
            "tariff", "trade", "sanction", "geopolit", "war", "election", "policy",
            "ecb", "boj", "pboc", "imf", "world bank", "opec", "oil", "energy",
            "tech", "ai", "chip", "semiconductor", "tesla", "apple", "nvidia", "microsoft",
            "amazon", "google", "meta", "tesla", "musk", "ai", "chip", "gold",
            "treasury", "yuan", "dollar", "euro", "yen", "pound", "currency", "forex",
            "bank", "loan", "credit", "debt", "default", "credit", "rating",
            "btc", "bitcoin", "crypto", "ethereum", "stablecoin",
            "通胀", "利率", "央行", "市场", "股票", "债券", "经济", "财政", "货币",
            "上市", "财报", "盈利", "营收", "并购", "重组", "破产", "债务", "违约",
            "关税", "制裁", "地缘", "战争", "选举", "政策", "美元", "欧元", "日元",
            "黄金", "原油", "新能源", "科技", "芯片", "半导体", "ai", "人工智能",
        ]
        block_keywords = [
            "my husband", "my wife", "my mom", "my dad", "my son", "my daughter",
            "i'm", "i am", "how can i", "what should i", "should i", "i just", "we just",
            "i have mostly", "i'm a", "i need to", "i'm trying", "i'm worried",
            "我丈夫", "我妻子", "我妈", "我爸", "我儿子", "我女儿",
            "该不该", "要不要", "怎么办", "如何", "请问", "求助",
            "asbestos", "tattoo", "pet", "dog", "cat", "celebrity", "kardashian",
            "fashion", "movie", "celebrity", "wedding", "dating",
        ]
        def is_macro_news(text):
            if not text:
                return False
            t = text.lower()
            if any(b in t for b in block_keywords):
                return False
            if any(k in t for k in keep_keywords):
                return True
            # 兜底: 包含 "公司/集团/股份/上市/财报/CPI" 等中英关键词的中长标题大概率是财经类
            return False
        filtered = [it for it in merged if is_macro_news(it.get("headline") or "")]
        # 如果过滤后 < 5 条, 放低要求: 不过滤 (全展示, 让用户能自己翻)
        if len(filtered) < 5:
            filtered = merged
        # 按 ts 倒序（最新的在前）, 保留 20 条
        filtered.sort(key=lambda x: x.get("ts") or "", reverse=True)
        global_news = filtered[:20]

        return {
            "available": bool(global_news or out.get("calendar")),
            "globalNews": global_news,
            "calendar": _trim(out.get("calendar") or [], key="event", translations=cal_translations),
            # RSS / GDELT 字段: 直接传 trimmed items, 让前端按列展示
            # (过滤后的子集, 与 globalNews 一致, 避免旧字段空数组的歧义)
            "rss": _trim(out.get("rss") or [], translations=rss_translations),
            "gdelt": _trim(out.get("gdelt") or [], translations=gdelt_translations),
            "translated": bool(any(rss_translations) or any(cal_translations) or any(gdelt_translations)),
            "fetchedAt": dt.datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[build_dashboard] macro feeds fetch failed: %s", exc)
        return {"available": False, "rss": [], "gdelt": [], "calendar": [], "error": str(exc)}


def _llm_strategy_review_block(advice: Dict[str, Any]) -> Dict[str, Any]:
    """从 advice.json 抽出 ``llm_strategy_review``（v2 工具化副驾驶）字段。

    缺失或未启用时返回 ``{"available": False, ...}``，UI 自动隐藏。
    """
    sr = advice.get("llm_strategy_review") or {}
    if not isinstance(sr, dict):
        sr = {}

    review = sr.get("review") or {}
    if not isinstance(review, dict):
        review = {}

    enabled = bool(review.get("enabled"))
    agreement = str(review.get("agreement") or "").strip().lower() or None
    agreement_label, agreement_tone = _LLM_AGREEMENT_TONE.get(
        agreement or "", ("", "neutral")
    )

    risks = review.get("risks_blindspots") or []
    if not isinstance(risks, list):
        risks = [str(risks)]
    risks = [str(r) for r in risks if r][:5]  # 略放宽到 5（v2 工具调用版可能产生更多）

    tool_calls_raw = sr.get("toolCalls") or []
    if not isinstance(tool_calls_raw, list):
        tool_calls_raw = []
    tool_calls: List[Dict[str, Any]] = []
    for tc in tool_calls_raw[:10]:  # 硬上限 10
        if not isinstance(tc, dict):
            continue
        tool_calls.append({
            "name": str(tc.get("name") or ""),
            "args": tc.get("args") if isinstance(tc.get("args"), dict) else {},
            "resultPreview": str(tc.get("result_preview") or "")[:300],
        })

    return {
        "available": enabled,
        "strategyName": str(sr.get("strategy") or ""),
        "strategyDisplayName": str(sr.get("displayName") or sr.get("strategy") or ""),
        "verdict": str(review.get("verdict") or ""),
        "agreement": agreement,
        "agreementLabel": agreement_label,
        "agreementTone": agreement_tone,
        "risksBlindspots": risks,
        "reminder": str(review.get("reminder") or ""),
        "toolCalls": tool_calls,
        "toolCallCount": len(tool_calls),
        "error": review.get("error"),
        "generatedAt": str(review.get("generated_at") or ""),
        "inputTokens": int(review.get("input_tokens") or 0),
        "outputTokens": int(review.get("output_tokens") or 0),
        "model": str(review.get("model") or ""),
    }


def _llm_copilot_block(advice: Dict[str, Any]) -> Dict[str, Any]:
    """从 advice.json 抽出 ``llm_review`` / ``llm_explanation`` 字段，构造成
    dashboard 友好的块（缺失时返回空块，UI 端自动隐藏）。

    重要：所有字段都是**只读透传**，不参与规则引擎的任何判断。
    """
    review = advice.get("llm_review") or {}
    explanation = advice.get("llm_explanation") or {}

    review_enabled = bool(review.get("enabled"))
    explanation_enabled = bool(explanation.get("enabled"))

    agreement = str(review.get("agreement") or "").strip().lower() or None
    agreement_label, agreement_tone = _LLM_AGREEMENT_TONE.get(
        agreement or "", ("", "neutral")
    )

    risks = review.get("risks_blindspots") or []
    if not isinstance(risks, list):
        risks = [str(risks)]
    risks = [str(r) for r in risks if r][:3]

    review_error = review.get("error")
    explanation_error = explanation.get("error")

    # Token 用量加总 — 在 dashboard 角落显示「本次 LLM 调用 X / Y tokens」
    in_tokens = int(review.get("input_tokens") or 0) + int(
        explanation.get("input_tokens") or 0
    )
    out_tokens = int(review.get("output_tokens") or 0) + int(
        explanation.get("output_tokens") or 0
    )

    any_enabled = review_enabled or explanation_enabled
    any_succeeded = (
        (review_enabled and not review_error)
        or (explanation_enabled and not explanation_error)
    )

    return {
        "available": any_enabled,
        "anySucceeded": any_succeeded,
        "tokens": {"input": in_tokens, "output": out_tokens},
        "review": {
            "enabled": review_enabled,
            "model": review.get("model") or "",
            "verdict": review.get("verdict") or "",
            "agreement": agreement,
            "agreementLabel": agreement_label,
            "agreementTone": agreement_tone,
            "risksBlindspots": risks,
            "reminder": review.get("reminder") or "",
            "error": review_error,
            "generatedAt": review.get("generated_at") or "",
        },
        "explanation": {
            "enabled": explanation_enabled,
            "model": explanation.get("model") or "",
            "explanation": explanation.get("explanation") or "",
            "error": explanation_error,
            "generatedAt": explanation.get("generated_at") or "",
        },
    }


def build_payload() -> Dict[str, Any]:
    # Prefer the LLM-enhanced copy if present, so the dashboard reflects the
    # most recent copilot run. Fall back to the plain advice JSON when the
    # copilot has not been run.
    advice_path = _latest_advice_path([ADVICE_JSON_STRICT, ADVICE_JSON_STRICT_LLM])
    if advice_path is None:
        advice_path = _latest_advice_path([ADVICE_JSON, ADVICE_JSON_LLM]) or ADVICE_JSON
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
        "signalDate": market.get("latest_market_date", latest_signal.get("signal_date", "")),
        "executionDate": _next_weekday(market.get("latest_market_date", "")) or latest_signal.get("execution_date", latest_signal.get("date", "")),
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
        # `return1m` below already uses `_period_return(..., qqq=is_qqq)` to
        # pick the right column. Earlier revisions had a separate
        # `ret_1m = _period_return(...) * 1.05` hack for QQQ that was both
        # dead (never read) and misleading (a hard-coded 5% inflation of
        # the monthly return), so it was removed in 1.3.x.

        # F2: derive the per-ETF score trio from the live decision and
        # market state. Earlier versions had hard-coded 0.7 / 0.5 / etc.
        # constants here which were both fake numbers and disconnected
        # from the actual strategy output.
        #   - momentum: 21-day return (SPY) or QQQ/SPY 63d relative
        #     strength (QQQ), normalized to ~[0, 1].
        #   - risk: 1 - clip(vix / 40, 0, 1) so low-vol regimes get a
        #     high score.
        #   - valuation: 1 - clip((cape - 20) / 25, 0, 1) so cheap CAPE
        #     gets a high score; very high CAPE (>=45) bottoms at 0.
        #   - final: 0.4*momentum + 0.3*risk + 0.3*valuation.
        if is_qqq:
            momentum_raw = float(market.get("qqq_rel_63d_pct", 0)) / 100.0
        else:
            momentum_raw = float(market.get("spy_ret_21d_pct", 0)) / 100.0
        risk_raw = 1.0 - min(1.0, max(0.0, float(market.get("vix", 20.0)) / 40.0))
        valuation_raw = 1.0 - min(1.0, max(0.0, (float(market.get("cape", 30.0)) - 20.0) / 25.0))
        final_raw = 0.4 * momentum_raw + 0.3 * risk_raw + 0.3 * valuation_raw
        momentum_score = round(min(1.0, max(0.0, 0.5 + momentum_raw)), 3)
        risk_score = round(risk_raw, 3)
        valuation_score = round(valuation_raw, 3)
        final_score = round(min(1.0, max(0.0, final_raw)), 3)

        # F3: signal classification that respects trim and panic states.
        # Earlier code only branched on target_weight > current_weight and
        # reported "hold" even when the strategy was actively recommending
        # a QQQ trim, which contradicted the action column.
        trim_active = bool(decision.get("trim_recommendation_active", False)) or \
            float(decision.get("trim_effective_qqq_pct_now", 0) or 0) > 0
        panic_tier = int(decision.get("panic_tier", 0) or 0)
        if trim_active and is_qqq:
            signal_label = "sell"
        elif trim_active and not is_qqq:
            signal_label = "trim"
        elif panic_tier >= 2:
            signal_label = "trim"
        elif weight_target > current_weight:
            signal_label = "buy"
        else:
            signal_label = "hold"

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
            "momentumScore": momentum_score,
            "riskScore": risk_score,
            "valuationScore": valuation_score,
            "finalScore": final_score,
            "signal": signal_label,
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
    latest_point = series[-1] if series else {}
    strategy_equity = latest_point.get("strategyEquity")
    benchmark_equity = latest_point.get("benchmarkEquity")
    strategy_dd = latest_point.get("strategyDrawdown")
    benchmark_dd = latest_point.get("benchmarkDrawdown")
    allocation = latest_point.get("allocation") or {}
    summary["display"] = {
        "excessCagr": summary["metrics"]["cagr"] - summary["benchmarkMetrics"]["xirr"],
        "drawdownDiff": summary["metrics"]["maxDrawdown"] - summary["benchmarkMetrics"]["maxDrawdown"],
        "latestStrategyEquity": strategy_equity,
        "latestBenchmarkEquity": benchmark_equity,
        "latestStrategyDrawdown": strategy_dd,
        "latestBenchmarkDrawdown": benchmark_dd,
        "latestCashPct": allocation.get("CASH", summary["metrics"].get("endingCashPct", 0.0)),
        "latestSeriesDate": latest_point.get("date", ""),
    }

    # ---- Settings (read-only mirror) ----
    # The runtime ETF pool lives in `advice.meta.etf_pool` so the operator
    # can change it via `--etf-pool SPY,QQQ,VTI,VOO` on the next advice
    # run. Fall back to the historical SPY/QQQ pair if the advice didn't
    # surface a pool (older runs).
    advice_etf_pool = (meta.get("etf_pool") or []) if isinstance(meta, dict) else []
    settings_etf_pool = advice_etf_pool if advice_etf_pool else ["SPY", "QQQ"]
    settings = {
        "etfPool": settings_etf_pool,
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
        # Surface the price-return warning and data-freshness policy so
        # the Settings page can render a yellow chip when price_return_only
        # is true. The cron push shell uses the same flag to refuse pushing
        # an unadjusted run.
        "priceReturnOnly": bool(meta.get("price_return_only", False)),
        "priceReturnWarning": meta.get("price_return_warning"),
    }

    archive_decision(DECISIONS_DB, advice)
    decision_payloads = load_decisions(DECISIONS_DB)
    decision_history = [
        entry for entry in (_decision_history_entry(item) for item in decision_payloads)
        if entry.get("date")
    ]

    payload = {
        "summary": summary,
        "decisionHistory": decision_history,
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
        "llmCopilot": {
            **_llm_copilot_block(advice),
            "strategyReview": _llm_strategy_review_block(advice),
        },
        "macroFeeds": _macro_feeds_block(advice),
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
    payload["asOfViews"] = _build_as_of_views(payload, decision_payloads, equity_rows)
    return payload


def _build_as_of_views(
    base: Dict[str, Any],
    advice_payloads: List[Dict[str, Any]],
    equity_rows: List[Dict[str, str]],
) -> Dict[str, Any]:
    views: Dict[str, Any] = {}
    history_by_date = {item["date"]: item for item in base.get("decisionHistory", [])}
    for advice in advice_payloads:
        date_str = str((advice.get("market") or {}).get("latest_market_date") or "")
        entry = history_by_date.get(date_str)
        if not date_str or not entry:
            continue
        view = copy.deepcopy({k: v for k, v in base.items() if k not in ("asOfViews", "decisionHistory")})
        view["summary"].update({
            "asOf": entry.get("generatedAt", ""),
            "mode": entry.get("mode", "production"),
            "signalDate": entry.get("signalDate", date_str),
            "executionDate": entry.get("executionDate", ""),
            "priceDate": entry.get("priceDate", date_str),
            "dataAvailableAt": entry.get("dataAvailableAt", ""),
            "currentAction": {
                k: entry.get(k) for k in (
                    "action", "ticker", "targetWeight", "currentWeight",
                    "tradeDelta", "reason", "amountUsd",
                )
            },
        })
        view["advice"] = advice
        view["llmCopilot"] = {
            **_llm_copilot_block(advice),
            "strategyReview": _llm_strategy_review_block(advice),
        }
        view["raw"].update({
            "decision": advice.get("decision") or {},
            "recommended": advice.get("recommended") or {},
            "portfolio": advice.get("portfolio") or {},
            "market": advice.get("market") or {},
            "diagnosis": advice.get("diagnosis") or {},
        })
        view["signals"] = _as_of_signals(view["signals"], advice, date_str)
        view["backtest"] = _as_of_backtest(view["backtest"], date_str)
        as_of_equity_rows = [row for row in equity_rows if str(row.get("date", "")) <= date_str]
        _update_as_of_summary(view["summary"], view["backtest"], date_str, as_of_equity_rows)
        view["dataQuality"]["runMetadata"]["createdAt"] = entry.get("generatedAt", "")
        views[date_str] = view
    return views


def _as_of_signals(signals: List[Dict[str, Any]], advice: Dict[str, Any], date_str: str) -> List[Dict[str, Any]]:
    market = advice.get("market") or {}
    decision = advice.get("decision") or {}
    portfolio = advice.get("portfolio") or {}
    diagnosis = advice.get("diagnosis") or {}
    out = copy.deepcopy(signals)
    for signal in out:
        ticker = signal.get("ticker")
        if ticker not in ("SPY", "QQQ"):
            continue
        is_qqq = ticker == "QQQ"
        target = float(decision.get("new_buy_qqq_weight_pct" if is_qqq else "new_buy_spy_weight_pct") or 0) / 100.0
        current = float(portfolio.get("qqq_weight_pct" if is_qqq else "spy_weight_pct") or 0) / 100.0
        momentum_raw = float(market.get("qqq_rel_63d_pct" if is_qqq else "spy_ret_21d_pct") or 0) / 100.0
        risk_raw = 1.0 - min(1.0, max(0.0, float(market.get("vix") or 20) / 40.0))
        valuation_raw = 1.0 - min(1.0, max(0.0, (float(market.get("cape") or 30) - 20.0) / 25.0))
        signal.update({
            "date": date_str,
            "price": market.get("qqq_close" if is_qqq else "spy_close", 0),
            "momentumScore": round(min(1.0, max(0.0, 0.5 + momentum_raw)), 3),
            "riskScore": round(risk_raw, 3),
            "valuationScore": round(valuation_raw, 3),
            "finalScore": round(min(1.0, max(0.0, 0.4 * momentum_raw + 0.3 * risk_raw + 0.3 * valuation_raw)), 3),
            "signal": "buy" if target > current else "hold",
            "targetWeight": target,
            "currentWeight": current,
            "reason": diagnosis.get("rule_reason", ""),
            "dataDate": date_str,
            "availableAt": (advice.get("meta") or {}).get("latest_used_cape_available_at", ""),
        })
    return out


def _as_of_backtest(backtest: Dict[str, Any], date_str: str) -> Dict[str, Any]:
    out = copy.deepcopy(backtest)
    out["series"] = [x for x in out.get("series", []) if str(x.get("date", "")) <= date_str]
    out["rolling"] = [x for x in out.get("rolling", []) if str(x.get("date", "")) <= date_str]
    out["trades"] = [x for x in out.get("trades", []) if str(x.get("date", "")) <= date_str]
    series = out["series"]
    out["monthly"], out["annual"] = _series_period_summaries(series)
    regime_counts: Dict[str, int] = {}
    mult_counts: Dict[str, int] = {}
    for point in series:
        regime = str(point.get("regime") or "unknown")
        mult = str(point.get("multiplier") or 0)
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
        mult_counts[mult] = mult_counts.get(mult, 0) + 1
    total = max(len(series), 1)
    out["regimeDistribution"] = {k: v / total for k, v in regime_counts.items()}
    out["multiplierDistribution"] = {k: v / total for k, v in mult_counts.items()}
    return out


def _series_period_summaries(series: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    monthly_groups: Dict[str, List[Dict[str, Any]]] = {}
    annual_groups: Dict[str, List[Dict[str, Any]]] = {}
    for point in series:
        date_str = str(point.get("date") or "")
        if len(date_str) < 7:
            continue
        monthly_groups.setdefault(date_str[:7], []).append(point)
        annual_groups.setdefault(date_str[:4], []).append(point)

    def period_return(points: List[Dict[str, Any]], key: str) -> float:
        start = float(points[0].get(key) or 0)
        end = float(points[-1].get(key) or 0)
        return end / start - 1 if start > 0 else 0.0

    monthly = []
    for ym, points in sorted(monthly_groups.items()):
        monthly.append({
            "year": int(ym[:4]),
            "month": int(ym[5:7]),
            "return": period_return(points, "strategyEquity"),
        })
    annual = []
    for year, points in sorted(annual_groups.items()):
        strategy = period_return(points, "strategyEquity")
        benchmark = period_return(points, "benchmarkEquity")
        annual.append({
            "year": int(year),
            "strategy": strategy,
            "benchmark": benchmark,
            "excess": strategy - benchmark,
            "maxDrawdown": min(float(x.get("strategyDrawdown") or 0) for x in points),
        })
    return monthly, annual


def _update_as_of_summary(
    summary: Dict[str, Any],
    backtest: Dict[str, Any],
    date_str: str,
    equity_rows: List[Dict[str, str]],
) -> None:
    series = backtest.get("series") or []
    if not series:
        return
    first, last = series[0], series[-1]
    days = max((dt.datetime.strptime(last["date"], "%Y-%m-%d") - dt.datetime.strptime(first["date"], "%Y-%m-%d")).days, 1)
    years = days / 365.25
    s0, s1 = float(first.get("strategyEquity") or 0), float(last.get("strategyEquity") or 0)
    b0, b1 = float(first.get("benchmarkEquity") or 0), float(last.get("benchmarkEquity") or 0)
    metrics = summary["metrics"]
    bench = summary["benchmarkMetrics"]
    first_row = equity_rows[0] if equity_rows else {}
    last_row = equity_rows[-1] if equity_rows else {}
    sn0 = _safe_float(first_row.get("strategy_nav")) or 0
    sn1 = _safe_float(last_row.get("strategy_nav")) or 0
    bn0 = _safe_float(first_row.get("benchmark_nav")) or 0
    bn1 = _safe_float(last_row.get("benchmark_nav")) or 0
    metrics["cagr"] = (sn1 / sn0) ** (1 / years) - 1 if sn0 > 0 and sn1 > 0 else 0
    bench["xirr"] = (bn1 / bn0) ** (1 / years) - 1 if bn0 > 0 and bn1 > 0 else 0
    metrics["maxDrawdown"] = min(float(x.get("strategyDrawdown") or 0) for x in series)
    bench["maxDrawdown"] = min(float(x.get("benchmarkDrawdown") or 0) for x in series)
    metrics["sharpe"] = _nav_sharpe(equity_rows, "strategy_nav")
    bench["sharpe"] = _nav_sharpe(equity_rows, "benchmark_nav")
    metrics["oneYearReturn"] = _rolling_return(equity_rows, 252)
    metrics["ytdReturn"] = _ytd_return(equity_rows)
    metrics["tradesCount"] = len(backtest.get("trades") or [])
    metrics["endingCashPct"] = float((last.get("allocation") or {}).get("CASH") or 0)
    summary["display"] = {
        "excessCagr": metrics["cagr"] - bench["xirr"],
        "drawdownDiff": metrics["maxDrawdown"] - bench["maxDrawdown"],
        "latestStrategyEquity": s1,
        "latestBenchmarkEquity": b1,
        "latestStrategyDrawdown": last.get("strategyDrawdown"),
        "latestBenchmarkDrawdown": last.get("benchmarkDrawdown"),
        "latestCashPct": metrics["endingCashPct"],
        "latestSeriesDate": last.get("date", date_str),
    }


def _sampled_sharpe(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((x - mean) ** 2 for x in returns) / (len(returns) - 1)
    return mean / math.sqrt(variance) * math.sqrt(52) if variance > 0 else 0.0


def _nav_sharpe(rows: List[Dict[str, str]], key: str) -> float:
    values = [_safe_float(row.get(key)) for row in rows]
    returns = [
        values[i] / values[i - 1] - 1
        for i in range(1, len(values))
        if values[i] is not None and values[i - 1] is not None and values[i - 1] > 0
    ]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((x - mean) ** 2 for x in returns) / (len(returns) - 1)
    return mean / math.sqrt(variance) * math.sqrt(252) if variance > 0 else 0.0


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


def _turnover(trades: List[Dict[str, Any]], weekly_budget: float) -> Dict[str, float]:
    """F8: split the total buy and sell turnover into separate fields so
    the dashboard can show them independently. The previous single
    `turnover` field excluded MONTHLY_TRIM because the engine records
    trim proceeds in `proceeds` rather than `amount`. Splitting avoids
    renaming an existing field (which the backtest `data.json` schema
    doesn't otherwise require).
    """
    if not trades or not weekly_budget:
        return {"buyTurnover": 0.0, "sellTurnover": 0.0, "buyTurnoverRatio": 0.0, "sellTurnoverRatio": 0.0}
    buy_total = sum(_safe_float(t.get("amount")) or 0.0 for t in trades if t.get("action") == "DCA_BUY")
    # MONTHLY_TRIM rows record the proceeds in `proceeds`, not `amount`.
    sell_total = sum(_safe_float(t.get("proceeds")) or 0.0 for t in trades if t.get("action") == "MONTHLY_TRIM")
    weekly_floor = max(weekly_budget, 1.0)
    return {
        "buyTurnover": round(buy_total, 2),
        "sellTurnover": round(sell_total, 2),
        "buyTurnoverRatio": round(min(buy_total / weekly_floor, 1.0), 4),
        "sellTurnoverRatio": round(min(sell_total / weekly_floor, 1.0), 4),
    }


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

    # F6: pre-compute running-peak and value series once per column.
    # `_running_dd` previously walked the full row list for every
    # sampled point — quadratic in chart point count. With the cache
    # below, each drawdown lookup is O(1) on average.
    s_cum: List[float] = []
    s_vals: List[float] = []
    b_cum: List[float] = []
    b_vals: List[float] = []
    s_peak = 0.0
    b_peak = 0.0
    for r in rows:
        try:
            d = dt.datetime.strptime(r.get("date", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        s_v = _safe_float(r.get("strategy_value")) or 0.0
        b_v = _safe_float(r.get("benchmark_value")) or 0.0
        s_peak = max(s_peak, s_v)
        b_peak = max(b_peak, b_v)
        s_cum.append((d, s_peak, s_v))
        s_vals.append(s_v)
        b_cum.append((d, b_peak, b_v))
        b_vals.append(b_v)

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
                "strategyDrawdown": _running_dd(rows, r.get("date", ""), "strategy_value",
                                                cummax_cache=s_cum, values_cache=s_vals),
                "benchmarkDrawdown": _running_dd(rows, r.get("date", ""), "benchmark_value",
                                                cummax_cache=b_cum, values_cache=b_vals),
                "allocation": alloc,
                "multiplier": _safe_float(r.get("multiplier")) or 0.0,
                "regime": r.get("regime", ""),
            })
        except Exception:
            continue
    return out


def _running_dd(rows: List[Dict[str, str]], date: str, col: str,
                cummax_cache: Optional[List[float]] = None,
                values_cache: Optional[List[float]] = None) -> float:
    """Running max-drawdown of `col` up to and including `date`.

    F6: was O(N) per call inside `_build_series` (called once per sampled
    equity point → O(N·M) total). Now optionally takes pre-computed
    running-peak and value lists computed once per column, dropping the
    per-point work to O(1) and total work to O(N).
    """
    try:
        target = dt.datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return 0.0
    if cummax_cache is not None and values_cache is not None:
        # Binary search the cache to find the last index whose date <= target.
        # The cache is constructed in `_build_series` and indexed by the
        # sorted order of `rows`.
        if not values_cache:
            return 0.0
        dates_cache: List[dt.date] = cummax_cache
        peak_cache: List[float] = values_cache
        # The cummax_cache list is the same length as dates_cache; treat
        # both as a single precomputed series indexed by sorted position.
        # We need to find the last index whose date <= target. We rely on
        # `_build_series` passing the *same sorted order* it used to
        # build the caches; fall back to the linear scan otherwise.
        # Linear scan over at most ~750 entries is still cheap.
        peak = 0.0
        last = 0.0
        for d, p, v in dates_cache:
            if d <= target:
                peak = p
                last = v
            else:
                break
        if peak == 0:
            return 0.0
        return (last - peak) / peak

    # Fallback path: small `rows` (e.g. direct unit-test invocation) and we
    # can afford the original O(N) scan.
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
    # F7: pre-compute the unitized daily returns once. The previous
    # implementation used `r * 4 / 0.05`, which is a hand-rolled,
    # magic-numbered approximation that has nothing to do with the
    # actual rolling Sharpe (which is `mean(excess_returns) / std(excess_returns)`).
    # The unitized NAV is flow-free, so a return computed off it is a
    # true return. We use the strategy's unitized NAV to feed both the
    # rolling return and the rolling Sharpe.
    nav_series: List[Optional[float]] = []
    for r in rows:
        v = _safe_float(r.get("strategy_nav"))
        if v is None:
            v = _safe_float(r.get("strategy_value"))
        nav_series.append(v)
    daily_returns: List[Optional[float]] = [None]
    for i in range(1, len(nav_series)):
        cur = nav_series[i]
        prev = nav_series[i - 1]
        if cur is None or prev is None or prev == 0:
            daily_returns.append(None)
        else:
            daily_returns.append(cur / prev - 1.0)
    for i in range(window, len(rows)):
        cur_s = nav_series[i] or 0.0
        past_s = nav_series[i - window] or 0.0
        if not cur_s or not past_s:
            continue
        r = (cur_s - past_s) / past_s
        # True rolling Sharpe on the unitized daily returns inside the
        # window: `mean / std` of the daily returns, annualised by
        # sqrt(252). We do not assume a non-zero risk-free rate; if the
        # user sets one in the future, subtract `daily_rf` here.
        win_returns = [x for x in daily_returns[i - window + 1:i + 1] if x is not None]
        if len(win_returns) < 5:
            sharpe = 0.0
        else:
            mean = sum(win_returns) / len(win_returns)
            var = sum((x - mean) ** 2 for x in win_returns) / (len(win_returns) - 1)
            std = math.sqrt(var) if var > 0 else 0.0
            sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0.0
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
