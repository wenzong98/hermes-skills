#!/usr/bin/env python3
"""
===================================
LLM Advisor — 方案 A (副驾驶审查) + 方案 B (rationale 解释) + v2 (工具化)
===================================

三个入口：

  - ``review_signal(advice)`` → ``LLMReview``
      v1 — 静态副驾驶审查。我们把数据喂给 LLM，让它输出审查结论。

  - ``explain_decision(advice)`` → ``LLMExplanation``
      v1 — 把 ``diagnosis.rule_reason`` 翻译成 3-4 句人话。

  - ``review_with_tools(advice)`` → ``LLMReview``
      v2 — 工具化副驾驶审查。LLM 主动调用 ``get_market_snapshot`` /
      ``get_rule_engine_output`` / ``get_recent_decisions`` /
      ``search_macro_news`` 拉数据，再生成审查结论。**v2 失败时降级到 v1**。

工具调用协议（轻量版，避免 anthropic tool_use 协议复杂度）：
  1. LLM 输出 ``<tool_call>{"name": "...", "args": {...}}</tool_call>``
  2. 我们执行工具，把结果以 ``tool_result({...})`` 形式追加到对话
  3. 最多 5 次工具调用（``ToolBudget(max_calls=5)``）
  4. 最终输出必须符合 ``LLMReview`` schema

降级策略：
  - 单次 LLM 调用失败 → 重试
  - 工具调用失败 → 记录 tool_errors，继续循环
  - 整轮失败 → 返回 ``LLMReview(error=str)``
  - budget 耗尽但无结论 → 强制最终一次调用注入"无工具"提示
"""
from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any, Dict, List, Optional

from llm.client import LLMCallResult, call_llm, get_llm_config, is_llm_enabled
from llm.schema import LLMExplanation, LLMReview, WeeklyAdvice
from llm.strategies import build_strategy_system_prompt, get_strategy
from llm.tools import (
    BudgetExceededError,
    ToolArgError,
    ToolBudget,
    TOOL_REGISTRY,
    execute_tool_call,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

# Plan A — 副驾驶审查
REVIEW_SYSTEM = """你是一名美股 ETF 周定投策略的**副驾驶审查员**。你的职责是**质疑、补充、提醒**，绝不替规则引擎做决策。

【硬规则】
1. 你只能**审查**已有决策，不能建议不同的 multiplier 或权重。
2. 你看到的事实包括：SPY/QQQ 价格、Shiller CAPE、RSI、VIX、SMA200、近期定投历史。
3. 你的输出必须是合法 JSON，不要 markdown 代码块、不要任何解释文字。
4. 任何"建议把 0.75x 改成 1.0x"的话都视为越权 — 你的工作是提醒，不是改单。
5. 保持中文输出，长度严格受限：verdict≤40 字、risks_blindspots 每条≤30 字、reminder≤40 字。

【输出 JSON Schema】
{
  "verdict": "一句话总结（≤40 字）",
  "agreement": "agree" | "caution" | "disagree",
  "risks_blindspots": ["盲点/风险 1", "盲点/风险 2", ...],
  "reminder": "给执行者的一句提醒（≤40 字）"
}

agreement 含义：
- "agree": 同意系统建议，没什么需要警示
- "caution": 同意但有需要关注的风险点
- "disagree": 不建议按系统建议执行（极少见，只有当出现明显异常时）

risks_blindspots 是给执行者的"二次检查清单"，最多 3 条，每条 30 字内。
"""

REVIEW_USER_TEMPLATE = """下面是本周系统给出的定投建议 + 近 8 周历史，请审查。

【本周事实（数据时间：{market_date}）】
- Shiller CAPE = {cape}
- VIX = {vix}（20日均 {vix_sma20}）
- SPY 收盘 = {spy_close}，RSI14 = {spy_rsi14}，距 SMA200 = {spy_vs_sma200_pct}%
- QQQ 收盘 = {qqq_close}，63日相对 SPY = {qqq_rel_63d_pct}%，趋势上 = {qqq_trend_up}
- Trend up = {trend_up}，Strong = {trend_strong}，Risk off = {risk_off}

【系统判定】
- Regime: {regime}
- DCA 倍率: {dca_multiplier}x（动作：{action_label}）
- 新买入权重: SPY {new_buy_spy_weight_pct}% / QQQ {new_buy_qqq_weight_pct}%
- 恐慌层级: {panic_tier}，模型现金池: {model_cash_reservoir_pct}%
- 系统理由: {rule_reason}

【近 8 周信号历史】
{recent_signals_table}

请按 JSON Schema 输出审查结论。
"""


# Plan B — rationale 解释
EXPLAIN_SYSTEM = """你是一名"把量化机器语言翻译成普通人话"的金融翻译官。

【硬规则】
1. 你**不能重新计算**任何数字 — 所有事实数据由用户提供。
2. 你只能基于事实重写表达，不增加任何主观判断或额外建议。
3. 解释 3-4 句中文，120-180 字，使用第二人称"你"。
4. 必须用 JSON 输出，不要 markdown 代码块。

【输出 JSON Schema】
{
  "explanation": "3-4 句人话解释（120-180 字）"
}
"""

EXPLAIN_USER_TEMPLATE = """请把下面的量化建议翻译成普通人能听懂的话。

【事实数据（你必须严格使用）】
- Shiller CAPE = {cape}（>38 视为高估，>42 视为极贵）
- SPY {spy_close}，距 SMA200 = {spy_vs_sma200_pct}%（>0 即在长期趋势之上）
- VIX = {vix}（<20 平静，20-30 警惕，>30 恐慌）
- RSI14 = {spy_rsi14}（>70 超买，<30 超卖）
- QQQ 63日相对 SPY = {qqq_rel_63d_pct}%（正=QQQ 偏强，负=SPY 偏强）

【系统输出】
- 判定 regime = {regime}
- 建议 multiplier = {dca_multiplier}x（{action_label}）
- 新买入 SPY/QQQ = {new_buy_spy_weight_pct}%/{new_buy_qqq_weight_pct}%
- 原始理由（机器语言）：{rule_reason}

请用第二人称"你"输出 3-4 句中文（120-180 字），不引入新事实、不给额外建议。
"""


# Plan A2 — 工具化副驾驶审查（v2）
REVIEW_SYSTEM_V2 = REVIEW_SYSTEM_V2_HEAD = """你是一名美股 ETF 周定投策略的副驾驶审查员（v2 工具化版本）。
你的职责是**质疑、补充、提醒**，绝不替规则引擎做决策。

【硬规则】
1. 你只能**审查**已有决策，不能建议不同的 multiplier 或权重。
2. 你可以主动调用工具查询事实，但**最多 5 次工具调用**（硬上限）。
3. 调用时机：VIX>=20 / SPY 单日跌>2% / CAPE 月变动>3 / 你看到 VIX 跳变但系统没动。
4. 最终输出必须是合法 JSON（verdict / agreement / risks_blindspots / reminder），
   不要 markdown 代码块、不要任何解释文字。
5. 长度严格受限：verdict≤40 字、risks_blindspots 每条≤30 字、reminder≤40 字。

agreement 含义：
- "agree": 同意系统建议，没什么需要警示
- "caution": 同意但有需要关注的风险点
- "disagree": 不建议按系统建议执行（极少见）

【工具调用协议】
- 工具调用格式：<tool_call>{"name": "工具名", "args": {...}}</tool_call>
- 工具结果会以 tool_result({...}) 形式追加到对话
- 你可以基于工具结果继续调用或输出最终 JSON
- 可用工具：get_market_snapshot、get_rule_engine_output、get_recent_decisions、search_macro_news
"""


def _build_strategy_user_prompt(advice: WeeklyAdvice) -> str:
    """构造 v2 工具调用的 user prompt — 把当前事实压缩成最小集。"""
    m = advice.market
    d = advice.decision
    diag = advice.diagnosis

    return (
        "【本周事实（T-1 收盘后）】\n"
        f"- 市场日期: {m.latest_market_date or 'n/a'}\n"
        f"- Shiller CAPE: {_safe_float(m.cape, 1)}\n"
        f"- SPY: {_safe_float(m.spy_close, 2)} (距 SMA200 = {_safe_float(m.spy_vs_sma200_pct, 2)}%)\n"
        f"- QQQ: {_safe_float(m.qqq_close, 2)} (63d 相对 SPY = {_safe_float(m.qqq_rel_63d_pct, 2)}%)\n"
        f"- VIX: {_safe_float(m.vix, 2)} (20d 均 {_safe_float(m.vix_sma20, 2)})\n"
        f"- RSI14: {_safe_float(m.spy_rsi14, 1)}, 21d 收益: {_safe_float(m.spy_ret_21d_pct, 2)}%\n"
        f"- 252d 回撤: {_safe_float(m.spy_drawdown_252d_pct, 2)}%\n"
        f"- 趋势: up={m.trend_up}, strong={m.trend_strong}, risk_off={m.risk_off}\n\n"
        "【系统判定】\n"
        f"- Regime: {diag.regime or 'n/a'}\n"
        f"- Multiplier: {_safe_float(d.dca_multiplier, 2)}x ({d.action_label or 'n/a'})\n"
        f"- 新买入权重: SPY {_safe_float(d.new_buy_spy_weight_pct, 1)}% / "
        f"QQQ {_safe_float(d.new_buy_qqq_weight_pct, 1)}%\n"
        f"- Panic tier: {d.panic_tier or 'n/a'}, Cash reservoir: {_safe_float(d.model_cash_reservoir_pct, 1)}%\n"
        f"- 系统理由: {diag.rule_reason or 'n/a'}\n\n"
        "请按协议调用工具查询（如需要），最终输出 JSON 结论。"
    )


# ---------------------------------------------------------------------------
# Tool-call protocol helpers
# ---------------------------------------------------------------------------


_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.DOTALL)


def parse_tool_call(content: str) -> List[Dict[str, Any]]:
    """从 LLM 输出中提取一个或多个 <tool_call>...</tool_call>。

    容错策略：
    - 不抛异常
    - 坏 JSON 直接丢弃
    - 找不到任何 call 返回 []
    """
    if not content:
        return []
    out: List[Dict[str, Any]] = []
    for m in _TOOL_CALL_RE.finditer(content):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        name = data.get("name")
        args = data.get("args") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            continue
        out.append({"name": name, "args": args})
    return out


def format_tool_result_block(name: str, result: Any) -> str:
    """把工具结果格式化成可塞回 prompt 的字符串。"""
    try:
        text = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(result)[:1500]
    return f"tool_result({name}): ```json\n{text}\n```"



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _format_recent_signals(recent: List) -> str:
    """把近 8 周信号压成表格字符串，给 LLM 视觉化。"""
    if not recent:
        return "（无历史）"
    lines = ["日期 | 动作 | multiplier | CAPE | RSI | VIX | SPY/QQQ"]
    for s in recent[-8:]:
        lines.append(
            f"{s.date} | {s.action} | {s.dca_multiplier}x | "
            f"{s.cape} | {s.rsi14} | {s.vix} | "
            f"{s.spy_buy_weight_pct}/{s.qqq_buy_weight_pct}"
        )
    return "\n".join(lines)


def _safe_float(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _parse_json_lenient(content: str) -> Optional[Dict[str, Any]]:
    """宽松 JSON 解析 — 处理 LLM 偶尔的 markdown 包裹或尾部说明。"""
    if not content:
        return None
    text = content.strip()
    # 去掉 ```json ... ``` 包裹
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    # 抓第一个 { 到最后一个 } 之间
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


_REVIEW_KEYS = {"verdict", "agreement", "risks_blindspots", "reminder"}


def _looks_like_review_json(parsed: Dict[str, Any]) -> bool:
    """判定解析出的 dict 是否是 review schema（不是 tool_call）。

    工具调用 JSON 形如 ``{"name": "x", "args": {...}}``，应当识别为"不是 review"。
    """
    if not isinstance(parsed, dict):
        return False
    # 至少有一个 review 字段
    if not any(k in parsed for k in _REVIEW_KEYS):
        return False
    # 不能是 tool_call
    if "name" in parsed and "args" in parsed and not any(
        k in parsed for k in _REVIEW_KEYS
    ):
        return False
    return True


# ---------------------------------------------------------------------------
# Plan A — 副驾驶审查
# ---------------------------------------------------------------------------


def review_signal(
    advice: WeeklyAdvice,
    cfg: Optional[Dict[str, str]] = None,
) -> LLMReview:
    """对本周信号做副驾驶审查。

    返回的 LLMReview：
      - ``enabled=False`` ⇒ LLM 未启用
      - ``enabled=True, error=str`` ⇒ 调用失败，但 caller 仍可推送（fallback 副标题）
      - ``enabled=True, verdict=...`` ⇒ 成功
    """
    cfg = cfg or get_llm_config()
    if not is_llm_enabled(cfg):
        return LLMReview(enabled=False, model=cfg["model"])

    user_prompt = REVIEW_USER_TEMPLATE.format(
        market_date=advice.market.latest_market_date or "n/a",
        cape=_safe_float(advice.market.cape, 1),
        vix=_safe_float(advice.market.vix, 2),
        vix_sma20=_safe_float(advice.market.vix_sma20, 2),
        spy_close=_safe_float(advice.market.spy_close, 2),
        spy_rsi14=_safe_float(advice.market.spy_rsi14, 1),
        spy_vs_sma200_pct=_safe_float(advice.market.spy_vs_sma200_pct, 2),
        qqq_close=_safe_float(advice.market.qqq_close, 2),
        qqq_rel_63d_pct=_safe_float(advice.market.qqq_rel_63d_pct, 2),
        qqq_trend_up=str(advice.market.qqq_trend_up),
        trend_up=str(advice.market.trend_up),
        trend_strong=str(advice.market.trend_strong),
        risk_off=str(advice.market.risk_off),
        regime=advice.diagnosis.regime or "n/a",
        dca_multiplier=_safe_float(advice.decision.dca_multiplier, 2),
        action_label=advice.decision.action_label or "n/a",
        new_buy_spy_weight_pct=_safe_float(advice.decision.new_buy_spy_weight_pct, 1),
        new_buy_qqq_weight_pct=_safe_float(advice.decision.new_buy_qqq_weight_pct, 1),
        panic_tier=advice.decision.panic_tier or "n/a",
        model_cash_reservoir_pct=_safe_float(advice.decision.model_cash_reservoir_pct, 1),
        rule_reason=advice.diagnosis.rule_reason or "n/a",
        recent_signals_table=_format_recent_signals(advice.recent_signals),
    )

    result = call_llm(REVIEW_SYSTEM, user_prompt, cfg=cfg)
    return _parse_review_result(result, cfg)


def _parse_review_result(result: LLMCallResult, cfg: Dict[str, str]) -> LLMReview:
    if not result.content:
        return LLMReview(
            enabled=True,
            model=cfg["model"],
            error="LLM 返回空内容（网络/超时/异常）",
            generated_at=_now_iso(),
        )

    parsed = _parse_json_lenient(result.content)
    if parsed is None or not _looks_like_review_json(parsed):
        return LLMReview(
            enabled=True,
            model=cfg["model"],
            error="LLM 输出非 review JSON（可能仍在调工具），无法解析",
            generated_at=_now_iso(),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    # agreement 强约束
    raw_agreement = str(parsed.get("agreement") or "").strip().lower()
    if raw_agreement not in {"agree", "caution", "disagree"}:
        raw_agreement = "caution"  # 默认到 caution，最安全的兜底

    risks = parsed.get("risks_blindspots") or []
    if not isinstance(risks, list):
        risks = [str(risks)]
    risks = [str(r) for r in risks if r][:3]

    return LLMReview(
        enabled=True,
        model=cfg["model"],
        verdict=str(parsed.get("verdict") or "")[:200] or None,
        agreement=raw_agreement,
        risks_blindspots=risks,
        reminder=str(parsed.get("reminder") or "")[:200] or None,
        error=None,
        generated_at=_now_iso(),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


# ---------------------------------------------------------------------------
# Plan B — rationale 解释
# ---------------------------------------------------------------------------


def explain_decision(
    advice: WeeklyAdvice,
    cfg: Optional[Dict[str, str]] = None,
) -> LLMExplanation:
    """把系统判定翻译成人话。"""
    cfg = cfg or get_llm_config()
    if not is_llm_enabled(cfg):
        return LLMExplanation(enabled=False, model=cfg["model"])

    user_prompt = EXPLAIN_USER_TEMPLATE.format(
        cape=_safe_float(advice.market.cape, 1),
        spy_close=_safe_float(advice.market.spy_close, 2),
        spy_vs_sma200_pct=_safe_float(advice.market.spy_vs_sma200_pct, 2),
        vix=_safe_float(advice.market.vix, 2),
        spy_rsi14=_safe_float(advice.market.spy_rsi14, 1),
        qqq_rel_63d_pct=_safe_float(advice.market.qqq_rel_63d_pct, 2),
        regime=advice.diagnosis.regime or "n/a",
        dca_multiplier=_safe_float(advice.decision.dca_multiplier, 2),
        action_label=advice.decision.action_label or "n/a",
        new_buy_spy_weight_pct=_safe_float(advice.decision.new_buy_spy_weight_pct, 1),
        new_buy_qqq_weight_pct=_safe_float(advice.decision.new_buy_qqq_weight_pct, 1),
        rule_reason=advice.diagnosis.rule_reason or "n/a",
    )

    result = call_llm(EXPLAIN_SYSTEM, user_prompt, cfg=cfg)
    return _parse_explanation_result(result, cfg)


def _parse_explanation_result(result: LLMCallResult, cfg: Dict[str, str]) -> LLMExplanation:
    if not result.content:
        return LLMExplanation(
            enabled=True,
            model=cfg["model"],
            error="LLM 返回空内容（网络/超时/异常）",
            generated_at=_now_iso(),
        )

    parsed = _parse_json_lenient(result.content)
    if parsed is None:
        return LLMExplanation(
            enabled=True,
            model=cfg["model"],
            error="LLM 输出非 JSON，无法解析",
            generated_at=_now_iso(),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    explanation = str(parsed.get("explanation") or "").strip()
    if not explanation:
        return LLMExplanation(
            enabled=True,
            model=cfg["model"],
            error="LLM 未生成 explanation 字段",
            generated_at=_now_iso(),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    return LLMExplanation(
        enabled=True,
        model=cfg["model"],
        explanation=explanation,
        error=None,
        generated_at=_now_iso(),
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


# ---------------------------------------------------------------------------
# Fallback text generators（LLM 失败时给推送的副标题用）
# ---------------------------------------------------------------------------


def fallback_review_text(advice: WeeklyAdvice) -> str:
    """LLM 审查失败时的兜底副标题。"""
    m = advice.decision
    if m.dca_multiplier >= 1.0:
        return "系统建议维持或加大定投节奏；按计划执行即可。"
    if m.dca_multiplier >= 0.75:
        return "系统建议降低定投但不中断；可按计划执行，关注高估值风险。"
    return "系统建议显著降低或暂停定投；除非出现极端波动，否则按计划执行。"


def fallback_explanation_text(advice: WeeklyAdvice) -> str:
    """LLM 解释失败时的兜底说明。"""
    m = advice.decision
    bits = []
    cape = advice.market.cape
    if cape is not None and cape >= 35:
        bits.append(f"估值偏高（CAPE={cape:.1f}）")
    vix = advice.market.vix
    if vix is not None and vix >= 25:
        bits.append(f"波动率抬升（VIX={vix:.1f}）")
    elif vix is not None and vix < 18:
        bits.append(f"市场平静（VIX={vix:.1f}）")
    if not bits:
        bits.append("市场状态中性")
    bits.append(f"本周定投 {m.dca_multiplier}x")
    bits.append(f"SPY/QQQ 配比 {m.new_buy_spy_weight_pct:.0f}/{m.new_buy_qqq_weight_pct:.0f}")
    return "；".join(bits) + "。"


# ---------------------------------------------------------------------------
# v2 — 工具化副驾驶审查（Plan A2）
# ---------------------------------------------------------------------------


def review_with_tools(
    advice: WeeklyAdvice,
    cfg: Optional[Dict[str, str]] = None,
    *,
    strategy_name: Optional[str] = None,
    tool_budget: int = 5,
    enable_tools: bool = True,
) -> LLMReview:
    """v2 工具化副驾驶审查。

    Parameters
    ----------
    advice : WeeklyAdvice
        本周结构化建议
    cfg : dict, optional
        LLM 配置
    strategy_name : str, optional
        若指定，从 ``strategies/`` 加载对应策略作为 system prompt。
        默认 None = 用 ``REVIEW_SYSTEM_V2_HEAD``（通用工具协议）。
    tool_budget : int
        工具调用预算（默认 5）
    enable_tools : bool
        False 时走 v1 等价路径（不调用工具）

    Returns
    -------
    LLMReview
        与 v1 schema 完全相同 — 调用方代码零改动
    """
    cfg = cfg or get_llm_config()

    if not is_llm_enabled(cfg):
        return LLMReview(enabled=False, model=cfg["model"])

    # 1) system prompt
    if strategy_name:
        spec = get_strategy(strategy_name)
        system_prompt = (
            build_strategy_system_prompt(spec)
            if spec is not None
            else REVIEW_SYSTEM_V2_HEAD
        )
    else:
        system_prompt = REVIEW_SYSTEM_V2_HEAD

    # 2) user prompt
    user_prompt = _build_strategy_user_prompt(advice)

    # 3) 关掉工具 = v1 等价路径
    if not enable_tools:
        result = call_llm(system_prompt, user_prompt, cfg=cfg)
        return _parse_review_result(result, cfg)

    # 4) 工具调用循环
    budget = ToolBudget(max_calls=tool_budget)
    tool_errors: List[str] = []
    tool_log: List[Dict[str, Any]] = []
    tool_ctx = {
        "advice": advice,
        "advice_dict": advice.to_dict(),
        "history": advice.recent_signals,
    }

    # 注入已知工具名到 system prompt 末尾（让 LLM 知道可用工具）
    tools_hint = (
        f"\n\n【本会话可用工具（{len(TOOL_REGISTRY)} 个）】\n"
        + "\n".join(
            f"- {name}: {schema['description']}"
            for name, schema in TOOL_REGISTRY.items()
        )
    )
    full_system = system_prompt + tools_hint

    # 强制终止条件：连续 2 次 LLM 调用都返回 tool_call 但 budget 已耗尽
    consecutive_no_progress = 0
    max_turns = tool_budget + 3  # 5 工具 + 1 终止 + 2 buffer
    final_result: Optional[LLMCallResult] = None
    forced_termination = False

    for turn in range(max_turns):
        if forced_termination:
            break
        result = call_llm(full_system, user_prompt, cfg=cfg)
        if not result.content:
            # LLM 静默失败
            final_result = result
            break

        calls = parse_tool_call(result.content)

        # 没调用工具 = 出最终结论了
        if not calls:
            final_result = result
            break

        # 全部调用都被 budget 拒绝
        any_executed = False
        executed_summaries: List[str] = []
        for call in calls:
            if budget.used >= budget.max_calls:
                executed_summaries.append(
                    f"[{call['name']}] 拒绝：预算已耗尽（{budget.used}/{budget.max_calls}）"
                )
                continue
            try:
                tool_out = execute_tool_call(
                    call["name"], call["args"], budget=budget, ctx=tool_ctx
                )
                tool_log.append(
                    {
                        "name": call["name"],
                        "args": call["args"],
                        "result_preview": _preview(tool_out),
                    }
                )
                user_prompt += "\n\n" + format_tool_result_block(call["name"], tool_out)
                any_executed = True
            except BudgetExceededError as exc:
                executed_summaries.append(f"[{call['name']}] 预算耗尽: {exc}")
            except (ToolArgError, KeyError) as exc:
                tool_errors.append(f"{call['name']}: {exc}")
                executed_summaries.append(f"[{call['name']}] 参数错误: {exc}")
            except Exception as exc:  # noqa: BLE001
                tool_errors.append(f"{call['name']}: {type(exc).__name__}: {exc}")
                executed_summaries.append(f"[{call['name']}] 异常: {exc}")

        if not any_executed:
            consecutive_no_progress += 1
            if consecutive_no_progress >= 2:
                # LLM 一直想调工具但都被拒 — 强制终止（下次循环 break）
                user_prompt += (
                    "\n\n【系统提示】工具调用预算已耗尽或全部失败。"
                    "请基于已收集的事实输出最终 JSON 结论。"
                )
                final_result = call_llm(full_system, user_prompt, cfg=cfg)
                forced_termination = True
                break
        else:
            consecutive_no_progress = 0

        # 注入本轮 summary 让 LLM 知道发生了什么
        if executed_summaries:
            user_prompt += (
                "\n\n【本轮执行 summary】\n" + "\n".join(executed_summaries)
            )

    # 5) 收尾 — 解析最终 LLM 输出
    if final_result is None or not final_result.content:
        return LLMReview(
            enabled=True,
            model=cfg["model"],
            error="LLM 工具循环结束但无最终输出（" + "; ".join(tool_errors[:3]) + "）",
            generated_at=_now_iso(),
        )

    review = _parse_review_result(final_result, cfg)
    # 附加工具调用元数据（在 reminder 字段后追加 1 行）— schema 不变
    if tool_log or tool_errors:
        meta_bits = []
        if tool_log:
            meta_bits.append(f"工具调用 {len(tool_log)} 次")
        if tool_errors:
            meta_bits.append(f"{len(tool_errors)} 个工具错误")
        # 把 metadata 注入 reminder 字段后缀（不破坏 schema）
        # 但 reminder 字段 ≤40 字约束，改为 errors 字段后缀
        if review.error is None and meta_bits:
            review.error = None  # 保持 error=None
        if tool_errors:
            existing = review.error or ""
            review.error = (existing + " | " if existing else "") + "; ".join(tool_errors[:3])
    return review


def review_with_tools_ex(
    advice: WeeklyAdvice,
    cfg: Optional[Dict[str, str]] = None,
    *,
    strategy_name: Optional[str] = None,
    tool_budget: int = 5,
    enable_tools: bool = True,
) -> tuple:
    """v2 工具化副驾驶审查 — 扩展版，返回 ``(LLMReview, tool_log)``。

    ``tool_log`` 是 list of ``{name, args, result_preview}``，可序列化到 JSON。
    与 ``review_with_tools()`` 的区别：旧版只返回 ``LLMReview``，工具调用
    历史被丢弃。新版返回 tuple，让 CLI / dashboard 能展示"LLM 怎么想的"。

    v1 调用方（只关心 LLMReview）继续用 ``review_with_tools()`` — 不破坏。
    """
    # 策略：把 tool_log 注入到 ctx 之外的局部状态，monkey-patch execute_tool_call
    # 收集 (但这会污染全局状态)。更简单的方案：直接复制 review_with_tools 实现
    # 并把 tool_log 收集到外部 list。
    #
    # 选最简方案：复用 review_with_tools 内部逻辑（从 _internal 抽取）。
    # 但当前 review_with_tools 把 tool_log 隐藏了 — 我们临时把执行逻辑 inline
    # 写出 v2 路径，调用 execute_tool_call 时把记录写到自己的 list。
    cfg = cfg or get_llm_config()

    if not is_llm_enabled(cfg):
        return LLMReview(enabled=False, model=cfg["model"]), []

    if strategy_name:
        spec = get_strategy(strategy_name)
        system_prompt = (
            build_strategy_system_prompt(spec)
            if spec is not None
            else REVIEW_SYSTEM_V2
        )
    else:
        system_prompt = REVIEW_SYSTEM_V2

    user_prompt = _build_strategy_user_prompt(advice)

    if not enable_tools:
        result = call_llm(system_prompt, user_prompt, cfg=cfg)
        return _parse_review_result(result, cfg), []

    budget = ToolBudget(max_calls=tool_budget)
    tool_errors: List[str] = []
    tool_log: List[Dict[str, Any]] = []
    tool_ctx = {
        "advice": advice,
        "advice_dict": advice.to_dict(),
        "history": advice.recent_signals,
    }

    tools_hint = (
        f"\n\n【本会话可用工具（{len(TOOL_REGISTRY)} 个）】\n"
        + "\n".join(
            f"- {name}: {schema['description']}"
            for name, schema in TOOL_REGISTRY.items()
        )
    )
    full_system = system_prompt + tools_hint

    consecutive_no_progress = 0
    max_turns = tool_budget + 3
    final_result: Optional[LLMCallResult] = None
    forced_termination = False

    for turn in range(max_turns):
        if forced_termination:
            break
        result = call_llm(full_system, user_prompt, cfg=cfg)
        if not result.content:
            final_result = result
            break
        calls = parse_tool_call(result.content)
        if not calls:
            final_result = result
            break
        any_executed = False
        for call in calls:
            if budget.used >= budget.max_calls:
                continue
            try:
                tool_out = execute_tool_call(
                    call["name"], call["args"], budget=budget, ctx=tool_ctx
                )
                tool_log.append(
                    {
                        "name": call["name"],
                        "args": call["args"],
                        "result_preview": _preview(tool_out),
                    }
                )
                user_prompt += "\n\n" + format_tool_result_block(call["name"], tool_out)
                any_executed = True
            except (BudgetExceededError, ToolArgError, KeyError) as exc:
                tool_errors.append(f"{call['name']}: {exc}")
            except Exception as exc:  # noqa: BLE001
                tool_errors.append(f"{call['name']}: {type(exc).__name__}: {exc}")
        if not any_executed:
            consecutive_no_progress += 1
            if consecutive_no_progress >= 2:
                user_prompt += (
                    "\n\n【系统提示】工具调用预算已耗尽或全部失败。"
                    "请基于已收集的事实输出最终 JSON 结论。"
                )
                final_result = call_llm(full_system, user_prompt, cfg=cfg)
                forced_termination = True
                break

    if final_result is None or not final_result.content:
        return (
            LLMReview(
                enabled=True,
                model=cfg["model"],
                error="LLM 工具循环结束但无最终输出（" + "; ".join(tool_errors[:3]) + "）",
                generated_at=_now_iso(),
            ),
            tool_log,
        )

    review = _parse_review_result(final_result, cfg)
    if tool_errors:
        existing = review.error or ""
        review.error = (existing + " | " if existing else "") + "; ".join(tool_errors[:3])
    return review, tool_log


def _preview(value: Any, max_chars: int = 200) -> str:
    """工具结果的简短预览，给 markdown 渲染用。"""
    try:
        s = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(value)
    if len(s) > max_chars:
        return s[: max_chars - 3] + "..."
    return s


def render_strategy_review_markdown(
    review: Optional[LLMReview],
    advice: WeeklyAdvice,
    strategy_name: str,
    tool_log: List[Dict[str, Any]],
) -> str:
    """渲染 v2 副驾驶审查结果为 Markdown（用于 push / dashboard 旁路）。"""
    spec = get_strategy(strategy_name)
    title = spec.display_name if spec else strategy_name
    lines = [
        f"## 工具化副驾驶审查 — {title}",
        "",
        f"**策略**: {strategy_name} | **生成时间**: {_now_iso()}",
        "",
    ]

    if not tool_log:
        lines.append("**工具调用**: 无（LLM 决定不调工具即出结论）")
    else:
        lines.append(f"**工具调用**: {len(tool_log)} 次")
        for i, entry in enumerate(tool_log, 1):
            args_str = json.dumps(entry.get("args") or {}, ensure_ascii=False)
            lines.append(
                f"{i}. `{entry['name']}({args_str})` → {entry.get('result_preview', '')[:120]}"
            )
    lines.append("")

    if review is None:
        lines.append("**LLM 结论**: 未生成")
    else:
        lines.append("**LLM 结论**:")
        if review.error:
            lines.append(f"- ❌ 错误: {review.error}")
        if review.verdict:
            lines.append(f"- 📋 {review.verdict}")
        if review.agreement:
            tone = {"agree": "🟢 同意", "caution": "🟡 谨慎", "disagree": "🔴 不同意"}.get(
                review.agreement, review.agreement
            )
            lines.append(f"- 立场: {tone}")
        if review.risks_blindspots:
            lines.append("- 风险/盲点:")
            for r in review.risks_blindspots:
                lines.append(f"  - {r}")
        if review.reminder:
            lines.append(f"- 💡 {review.reminder}")
        if review.input_tokens is not None:
            lines.append(
                f"- token: in={review.input_tokens} out={review.output_tokens}"
            )

    return "\n".join(lines)

