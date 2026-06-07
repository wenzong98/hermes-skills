#!/usr/bin/env python3
"""
===================================
Weekly Advice Output Schema
===================================

Pydantic-style schema (using dataclass + manual validation to avoid the
pydantic dependency) for the structured output of
``current_market_advice.py``. This is the contract consumed by:

  - LLM advisor (Plan A: 副驾驶审查; Plan B: rationale 解释)
  - Markdown report renderer (``write_report`` in current_market_advice.py)
  - Telegram push pipeline
  - Static dashboard JSON loader

设计目标：
  1. 严格字段类型 — 避免字符串/None/数字混用导致下游崩溃
  2. 与 ``current_market_advice.build_payload`` 输出 1:1 对应
  3. 不引入 pydantic 依赖 — 项目其他模块用 dataclass
  4. 提供 ``from_payload_dict`` 容错入口，宽松解析（部分字段缺失不报错）

与 DSA ``AnalysisReportSchema`` 对比：
  - 我们字段更少（标的只有 SPY/QQQ，不需要芯片/舆情/缠论等几十个字段）
  - 我们强调**可计算指标**（multiplier、SPY/QQQ 权重），DSA 强调**可读 narrative**
  - 我们的 schema 同时承载 LLM 副驾驶产物（``llm_review``, ``llm_explanation``），
    保持单一真源。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Sub-blocks
# ---------------------------------------------------------------------------


@dataclass
class MarketSnapshot:
    """Latest market data point. All numeric fields may be None if unavailable."""

    latest_market_date: str
    spy_close: Optional[float] = None
    spy_daily_return_pct: Optional[float] = None
    spy_vs_sma200_pct: Optional[float] = None
    spy_sma50_vs_sma200_pct: Optional[float] = None
    qqq_close: Optional[float] = None
    qqq_daily_return_pct: Optional[float] = None
    qqq_rel_63d_pct: Optional[float] = None
    qqq_rel_126d_pct: Optional[float] = None
    qqq_trend_up: Optional[bool] = None
    spy_rsi14: Optional[float] = None
    spy_ret_21d_pct: Optional[float] = None
    spy_drawdown_252d_pct: Optional[float] = None
    vix: Optional[float] = None
    vix_sma20: Optional[float] = None
    cape: Optional[float] = None
    trend_up: Optional[bool] = None
    trend_strong: Optional[bool] = None
    risk_off: Optional[bool] = None


@dataclass
class Diagnosis:
    summary: str
    regime: str
    rule_reason: str  # 来自规则引擎的硬理由（数字 + 触发条件）


@dataclass
class Decision:
    action_label: str
    dca_multiplier: float
    panic_tier: str
    model_cash_reservoir_pct: float
    new_buy_spy_weight_pct: float
    new_buy_qqq_weight_pct: float
    core_spy_weight_pct: float
    core_qqq_weight_pct: float
    satellite_spy_weight_pct: float
    satellite_qqq_weight_pct: float
    satellite_signal: str
    trim_signal_qqq_pct_now: float
    trim_effective_qqq_pct_now: float
    trim_reason_now: str


@dataclass
class RecentSignal:
    """One row in the recent_signals[] history block."""

    date: str
    action: str
    dca_multiplier: float
    spy_buy_weight_pct: float
    qqq_buy_weight_pct: float
    panic_tier: str
    satellite_signal: str
    cape: float
    rsi14: float
    vix: float
    spy_vs_sma200_pct: float
    qqq_rel_63d_pct: float
    trim_qqq_pct: float


@dataclass
class TrimState:
    month: str
    signal_detected: bool
    raw_trim_qqq_pct: float
    effective_trim_qqq_pct: float
    reason: str
    already_executed_this_month: bool
    recommendation_active: bool
    state_file: Optional[str] = None
    last_trim: Optional[Dict[str, Any]] = None


@dataclass
class PortfolioSummary:
    total_value: float
    spy_value: float
    qqq_value: float
    spy_weight_pct: float
    qqq_weight_pct: float


@dataclass
class RecommendedAction:
    weekly_budget_base: float
    total_buy: float
    model_cash_reservoir_pct: float
    spy_buy: float
    qqq_buy: float
    core_spy_weight_pct: float
    core_qqq_weight_pct: float
    satellite_spy_weight_pct: float
    satellite_qqq_weight_pct: float
    satellite_signal: str
    panic_tier: str
    after_buy_spy_weight_pct: float
    after_buy_qqq_weight_pct: float
    trim_already_executed_this_month: bool
    trim_recommendation_active: bool
    trim_signal_qqq_amount: float
    diagnostic_shift_qqq_to_spy_to_match_new_buy_target: float


@dataclass
class LLMReview:
    """方案 A 输出：LLM 副驾驶审查。

    LLM 看完本周信号 + 近 12 周历史 + 当前事实，输出 3 段中文短评。
    失败或未启用时字段为 None。
    """

    enabled: bool
    model: str
    verdict: Optional[str] = None           # 一句话总结（≤40 字）
    agreement: Optional[str] = None         # "agree" | "caution" | "disagree"
    risks_blindspots: List[str] = field(default_factory=list)
    reminder: Optional[str] = None          # 给人执行者的一句提醒
    error: Optional[str] = None             # 调用失败时的兜底
    generated_at: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


@dataclass
class LLMExplanation:
    """方案 B 输出：LLM 把 reason 字段翻译成人话。"""

    enabled: bool
    model: str
    explanation: Optional[str] = None       # 人话版 reason（3-4 句）
    error: Optional[str] = None
    generated_at: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


# ---------------------------------------------------------------------------
# Top-level container
# ---------------------------------------------------------------------------


@dataclass
class WeeklyAdvice:
    """``current_market_advice.build_payload`` 的结构化输出。"""

    generated_at: str
    market: MarketSnapshot
    diagnosis: Diagnosis
    decision: Decision
    meta: Dict[str, Any]
    recent_signals: List[RecentSignal] = field(default_factory=list)
    trim_state: Optional[TrimState] = None
    portfolio: Optional[PortfolioSummary] = None
    recommended: Optional[RecommendedAction] = None

    # LLM-generated fields (None if disabled or failed)
    llm_review: Optional[LLMReview] = None
    llm_explanation: Optional[LLMExplanation] = None

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    # ------------------------------------------------------------------
    # 解析（容错入口）
    # ------------------------------------------------------------------
    @classmethod
    def from_payload_dict(cls, payload: Dict[str, Any]) -> "WeeklyAdvice":
        """从 ``build_payload`` 原始 dict 反序列化为结构化对象。

        缺失字段尽量兜底为 None，不抛异常。这是 LLM 客户端的入口契约。
        """
        if not isinstance(payload, dict):
            raise TypeError(f"payload must be dict, got {type(payload).__name__}")

        meta = payload.get("meta") or {}
        m = payload.get("market") or {}
        d = payload.get("diagnosis") or {}
        dec = payload.get("decision") or {}

        market = MarketSnapshot(
            latest_market_date=m.get("latest_market_date", ""),
            spy_close=_to_float(m.get("spy_close")),
            spy_daily_return_pct=_to_float(m.get("spy_daily_return_pct")),
            spy_vs_sma200_pct=_to_float(m.get("spy_vs_sma200_pct")),
            spy_sma50_vs_sma200_pct=_to_float(m.get("spy_sma50_vs_sma200_pct")),
            qqq_close=_to_float(m.get("qqq_close")),
            qqq_daily_return_pct=_to_float(m.get("qqq_daily_return_pct")),
            qqq_rel_63d_pct=_to_float(m.get("qqq_rel_63d_pct")),
            qqq_rel_126d_pct=_to_float(m.get("qqq_rel_126d_pct")),
            qqq_trend_up=m.get("qqq_trend_up"),
            spy_rsi14=_to_float(m.get("spy_rsi14")),
            spy_ret_21d_pct=_to_float(m.get("spy_ret_21d_pct")),
            spy_drawdown_252d_pct=_to_float(m.get("spy_drawdown_252d_pct")),
            vix=_to_float(m.get("vix")),
            vix_sma20=_to_float(m.get("vix_sma20")),
            cape=_to_float(m.get("cape")),
            trend_up=m.get("trend_up"),
            trend_strong=m.get("trend_strong"),
            risk_off=m.get("risk_off"),
        )

        diagnosis = Diagnosis(
            summary=str(d.get("summary") or ""),
            regime=str(d.get("regime") or ""),
            rule_reason=str(d.get("rule_reason") or ""),
        )

        decision = Decision(
            action_label=str(dec.get("action_label") or ""),
            dca_multiplier=_to_float(dec.get("dca_multiplier")) or 0.0,
            panic_tier=str(dec.get("panic_tier") or ""),
            model_cash_reservoir_pct=_to_float(dec.get("model_cash_reservoir_pct")) or 0.0,
            new_buy_spy_weight_pct=_to_float(dec.get("new_buy_spy_weight_pct")) or 0.0,
            new_buy_qqq_weight_pct=_to_float(dec.get("new_buy_qqq_weight_pct")) or 0.0,
            core_spy_weight_pct=_to_float(dec.get("core_spy_weight_pct")) or 0.0,
            core_qqq_weight_pct=_to_float(dec.get("core_qqq_weight_pct")) or 0.0,
            satellite_spy_weight_pct=_to_float(dec.get("satellite_spy_weight_pct")) or 0.0,
            satellite_qqq_weight_pct=_to_float(dec.get("satellite_qqq_weight_pct")) or 0.0,
            satellite_signal=str(dec.get("satellite_signal") or ""),
            trim_signal_qqq_pct_now=_to_float(dec.get("trim_signal_qqq_pct_now")) or 0.0,
            trim_effective_qqq_pct_now=_to_float(dec.get("trim_effective_qqq_pct_now")) or 0.0,
            trim_reason_now=str(dec.get("trim_reason_now") or ""),
        )

        recent = []
        for row in payload.get("recent_signals") or []:
            if not isinstance(row, dict):
                continue
            recent.append(
                RecentSignal(
                    date=str(row.get("date") or ""),
                    action=str(row.get("action") or ""),
                    dca_multiplier=_to_float(row.get("dca_multiplier")) or 0.0,
                    spy_buy_weight_pct=_to_float(row.get("spy_buy_weight_pct")) or 0.0,
                    qqq_buy_weight_pct=_to_float(row.get("qqq_buy_weight_pct")) or 0.0,
                    panic_tier=str(row.get("panic_tier") or ""),
                    satellite_signal=str(row.get("satellite_signal") or ""),
                    cape=_to_float(row.get("cape")) or 0.0,
                    rsi14=_to_float(row.get("rsi14")) or 0.0,
                    vix=_to_float(row.get("vix")) or 0.0,
                    spy_vs_sma200_pct=_to_float(row.get("spy_vs_sma200_pct")) or 0.0,
                    qqq_rel_63d_pct=_to_float(row.get("qqq_rel_63d_pct")) or 0.0,
                    trim_qqq_pct=_to_float(row.get("trim_qqq_pct")) or 0.0,
                )
            )

        trim_state = None
        if payload.get("trim_state"):
            ts = payload["trim_state"]
            trim_state = TrimState(
                month=str(ts.get("month") or ""),
                signal_detected=bool(ts.get("signal_detected")),
                raw_trim_qqq_pct=_to_float(ts.get("raw_trim_qqq_pct")) or 0.0,
                effective_trim_qqq_pct=_to_float(ts.get("effective_trim_qqq_pct")) or 0.0,
                reason=str(ts.get("reason") or ""),
                already_executed_this_month=bool(ts.get("already_executed_this_month")),
                recommendation_active=bool(ts.get("recommendation_active")),
                state_file=ts.get("state_file"),
                last_trim=ts.get("last_trim"),
            )

        portfolio = None
        if payload.get("portfolio"):
            p = payload["portfolio"]
            portfolio = PortfolioSummary(
                total_value=_to_float(p.get("total_value")) or 0.0,
                spy_value=_to_float(p.get("spy_value")) or 0.0,
                qqq_value=_to_float(p.get("qqq_value")) or 0.0,
                spy_weight_pct=_to_float(p.get("spy_weight_pct")) or 0.0,
                qqq_weight_pct=_to_float(p.get("qqq_weight_pct")) or 0.0,
            )

        recommended = None
        if payload.get("recommended"):
            r = payload["recommended"]
            recommended = RecommendedAction(
                weekly_budget_base=_to_float(r.get("weekly_budget_base")) or 0.0,
                total_buy=_to_float(r.get("total_buy")) or 0.0,
                model_cash_reservoir_pct=_to_float(r.get("model_cash_reservoir_pct")) or 0.0,
                spy_buy=_to_float(r.get("spy_buy")) or 0.0,
                qqq_buy=_to_float(r.get("qqq_buy")) or 0.0,
                core_spy_weight_pct=_to_float(r.get("core_spy_weight_pct")) or 0.0,
                core_qqq_weight_pct=_to_float(r.get("core_qqq_weight_pct")) or 0.0,
                satellite_spy_weight_pct=_to_float(r.get("satellite_spy_weight_pct")) or 0.0,
                satellite_qqq_weight_pct=_to_float(r.get("satellite_qqq_weight_pct")) or 0.0,
                satellite_signal=str(r.get("satellite_signal") or ""),
                panic_tier=str(r.get("panic_tier") or ""),
                after_buy_spy_weight_pct=_to_float(r.get("after_buy_spy_weight_pct")) or 0.0,
                after_buy_qqq_weight_pct=_to_float(r.get("after_buy_qqq_weight_pct")) or 0.0,
                trim_already_executed_this_month=bool(r.get("trim_already_executed_this_month")),
                trim_recommendation_active=bool(r.get("trim_recommendation_active")),
                trim_signal_qqq_amount=_to_float(r.get("trim_signal_qqq_amount")) or 0.0,
                diagnostic_shift_qqq_to_spy_to_match_new_buy_target=_to_float(
                    r.get("diagnostic_shift_qqq_to_spy_to_match_new_buy_target")
                ) or 0.0,
            )

        return cls(
            generated_at=str(meta.get("generated_at") or ""),
            market=market,
            diagnosis=diagnosis,
            decision=decision,
            meta=meta,
            recent_signals=recent,
            trim_state=trim_state,
            portfolio=portfolio,
            recommended=recommended,
        )


def _to_float(value: Any) -> Optional[float]:
    """安全 float 转换。None/字符串数字/NaN 全部兜底。"""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in {"nan", "none", "null"}:
            return None
        try:
            return float(s)
        except ValueError:
            return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


# ---------------------------------------------------------------------------
# 便利函数：构造 fake payload（供测试和 prompt 模板使用）
# ---------------------------------------------------------------------------


def build_fake_advice() -> WeeklyAdvice:
    """构造一个合规的 fake WeeklyAdvice，供单元测试和 prompt 模板 demo 使用。"""
    return WeeklyAdvice.from_payload_dict(
        {
            "meta": {"generated_at": "2026-06-06T10:00:00+08:00", "strategy_version": "fake"},
            "market": {
                "latest_market_date": "2026-06-05",
                "spy_close": 600.0,
                "spy_daily_return_pct": -0.5,
                "spy_vs_sma200_pct": 5.2,
                "spy_sma50_vs_sma200_pct": 1.8,
                "qqq_close": 520.0,
                "qqq_daily_return_pct": -0.6,
                "qqq_rel_63d_pct": 1.5,
                "qqq_rel_126d_pct": 2.1,
                "qqq_trend_up": True,
                "spy_rsi14": 65.0,
                "spy_ret_21d_pct": 2.5,
                "spy_drawdown_252d_pct": -3.2,
                "vix": 15.4,
                "vix_sma20": 16.0,
                "cape": 41.0,
                "trend_up": True,
                "trend_strong": True,
                "risk_off": False,
            },
            "diagnosis": {
                "summary": "高估值+趋势向上：放缓但不停",
                "regime": "very_expensive",
                "rule_reason": "CAPE=41.0 落入 very_expensive 档；趋势确认最低 0.75x；QQQ 相对 SPY 偏强 → 卫星偏 QQQ。",
            },
            "decision": {
                "action_label": "降低定投但不中断",
                "dca_multiplier": 0.75,
                "panic_tier": "none",
                "model_cash_reservoir_pct": 14.5,
                "new_buy_spy_weight_pct": 40.0,
                "new_buy_qqq_weight_pct": 60.0,
                "core_spy_weight_pct": 40.0,
                "core_qqq_weight_pct": 40.0,
                "satellite_spy_weight_pct": 0.0,
                "satellite_qqq_weight_pct": 20.0,
                "satellite_signal": "satellite_qqq_tilt",
                "trim_signal_qqq_pct_now": 0.0,
                "trim_effective_qqq_pct_now": 0.0,
                "trim_reason_now": "none",
            },
        }
    )
