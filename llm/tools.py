"""
===================================
LLM 工具层 — 4 个只读工具
===================================

让 LLM 副驾驶主动查询已有数据，而不是被我们喂静态数据。

设计原则：
  1. **只读**：所有工具都是纯函数，无副作用，不修改任何 dca_multiplier / 权重
  2. **预算**：单次审查最多 5 次工具调用（``ToolBudget`` 硬上限）
  3. **真实 + mock fallback**：``search_macro_news`` 实接 Tavily / SerpAPI；两者都失败时返回 mock 数据
  4. **小输出**：每个工具返回 ≤ 2KB，防止 prompt 膨胀
  5. **失败透明**：工具抛异常时 caller 拿到原始异常，决定是否终止循环

工具注册表 ``TOOL_REGISTRY`` — name → {description, parameters, fn}。
``execute_tool_call()`` 是统一入口：调度 + 预算扣减 + 参数校验。

参考 DSA ``@tool`` 装饰器：他们的工具是给多 agent 用的，调用链长；
我们只给"周报副驾驶"用，工具调用预算很短（5 次），不需要工具依赖图。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BudgetExceededError(RuntimeError):
    """工具调用预算已用完。"""


class ToolArgError(ValueError):
    """工具参数缺失或类型错误。"""


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


@dataclass
class ToolBudget:
    """工具调用预算。``max_calls=5`` 是硬上限，由 LLM_SYSTEM_V2 提示词约束。"""

    max_calls: int = 5
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.used)

    def consume(self) -> None:
        if self.used >= self.max_calls:
            raise BudgetExceededError(
                f"tool budget exhausted ({self.max_calls} calls used)"
            )
        self.used += 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _round_floats(obj: Any, digits: int = 2) -> Any:
    """递归把 float 截断到指定位数，None / str / bool / int 保持原样。"""
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        if obj != obj:  # NaN
            return None
        return round(obj, digits)
    if isinstance(obj, dict):
        return {k: _round_floats(v, digits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(x, digits) for x in obj]
    return obj


def _truncate(value: Any, max_bytes: int = 2000) -> Any:
    """保证序列化后 ≤ max_bytes。超出时按字符串裁剪 + 加 ellipsis。"""
    try:
        s = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return value
    if len(s) <= max_bytes:
        return value
    # 兜底：转字符串裁剪
    return {"_truncated": True, "preview": s[: max_bytes - 50] + "..."}


def _require_advice(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """从 ctx 拿 advice_dict（不存在则用空 dict 兜底）。"""
    advice_dict = ctx.get("advice_dict")
    if advice_dict is None and ctx.get("advice") is not None:
        advice_dict = ctx["advice"].to_dict()
    return advice_dict or {}


def _validate_args(schema: Dict[str, Any], args: Dict[str, Any]) -> None:
    """轻量 JSON-Schema 校验：仅检查必填字段和类型标签。"""
    params = schema.get("parameters") or {}
    required = params.get("required") or []
    properties = params.get("properties") or {}
    for key in required:
        if key not in args:
            raise ToolArgError(f"missing required arg: {key}")
    for key, val in args.items():
        if key not in properties:
            raise ToolArgError(f"unknown arg: {key}")
        expected = properties[key].get("type")
        if expected == "integer" and not isinstance(val, int):
            raise ToolArgError(f"arg {key} must be integer, got {type(val).__name__}")
        elif expected == "string" and not isinstance(val, str):
            raise ToolArgError(f"arg {key} must be string, got {type(val).__name__}")


# ---------------------------------------------------------------------------
# Tool 1: get_market_snapshot
# ---------------------------------------------------------------------------


def get_market_snapshot(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """查询最新 SPY/QQQ/VIX/CAPE/RSI 事实数据（PIT：T-1 收盘后）。"""
    advice_dict = _require_advice(ctx)
    market = advice_dict.get("market") or {}
    return _truncate(
        {
            "as_of_date": market.get("latest_market_date"),
            "spy_close": market.get("spy_close"),
            "spy_vs_sma200_pct": market.get("spy_vs_sma200_pct"),
            "spy_sma50_vs_sma200_pct": market.get("spy_sma50_vs_sma200_pct"),
            "spy_rsi14": market.get("spy_rsi14"),
            "spy_ret_21d_pct": market.get("spy_ret_21d_pct"),
            "spy_drawdown_252d_pct": market.get("spy_drawdown_252d_pct"),
            "qqq_close": market.get("qqq_close"),
            "qqq_rel_63d_pct": market.get("qqq_rel_63d_pct"),
            "qqq_rel_126d_pct": market.get("qqq_rel_126d_pct"),
            "qqq_trend_up": market.get("qqq_trend_up"),
            "vix": market.get("vix"),
            "vix_sma20": market.get("vix_sma20"),
            "cape": market.get("cape"),
            "trend_up": market.get("trend_up"),
            "trend_strong": market.get("trend_strong"),
            "risk_off": market.get("risk_off"),
        }
    )


# ---------------------------------------------------------------------------
# Tool 2: get_rule_engine_output
# ---------------------------------------------------------------------------


def get_rule_engine_output(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """查询规则引擎的当前判定（regime / multiplier / SPY-QQQ 配比）。

    **只读**：返回值不能修改 — 任何含 new_/override_/set_ 前缀的字段
    都不暴露，防止 LLM 误以为可改判定。
    """
    advice_dict = _require_advice(ctx)
    dec = advice_dict.get("decision") or {}
    diag = advice_dict.get("diagnosis") or {}
    return _truncate(
        {
            "regime": diag.get("regime") or "unknown",
            "rule_reason": diag.get("rule_reason"),
            "dca_multiplier": dec.get("dca_multiplier"),
            "action_label": dec.get("action_label"),
            "panic_tier": dec.get("panic_tier"),
            "model_cash_reservoir_pct": dec.get("model_cash_reservoir_pct"),
            "new_buy_spy_weight_pct": dec.get("new_buy_spy_weight_pct"),
            "new_buy_qqq_weight_pct": dec.get("new_buy_qqq_weight_pct"),
            "trim_signal_qqq_pct_now": dec.get("trim_signal_qqq_pct_now"),
            "trim_reason_now": dec.get("trim_reason_now"),
        }
    )


# ---------------------------------------------------------------------------
# Tool 3: get_recent_decisions
# ---------------------------------------------------------------------------


def get_recent_decisions(
    ctx: Dict[str, Any],
    n_weeks: int = 8,
) -> List[Dict[str, Any]]:
    """查询近 N 周的决策历史（multiplier / regime / 关键指标）。

    n_weeks 硬上限 52（约 1 年），下限 1。
    """
    if not isinstance(n_weeks, int):
        n_weeks = 8
    n_weeks = max(1, min(52, n_weeks))

    advice_dict = _require_advice(ctx)
    history = ctx.get("history")
    if history is None:
        # 从 advice.recent_signals 取
        history = advice_dict.get("recent_signals") or []

    out: List[Dict[str, Any]] = []
    for row in list(history)[-n_weeks:]:
        if not isinstance(row, dict):
            continue
        out.append(
            _round_floats(
                {
                    "date": row.get("date"),
                    "regime": row.get("regime"),
                    "dca_multiplier": row.get("dca_multiplier"),
                    "panic_tier": row.get("panic_tier"),
                    "spy_buy_weight_pct": row.get("spy_buy_weight_pct"),
                    "qqq_buy_weight_pct": row.get("qqq_buy_weight_pct"),
                    "vix": row.get("vix"),
                    "cape": row.get("cape"),
                    "rsi14": row.get("rsi14"),
                    "spy_drawdown_252d_pct": row.get("spy_drawdown_252d_pct"),
                    "trim_qqq_pct": row.get("trim_qqq_pct"),
                },
                digits=2,
            )
        )
    return _truncate(out) if isinstance(_truncate(out), list) else out


# ---------------------------------------------------------------------------
# Tool 4: search_macro_news (实接 Tavily / SerpAPI, mock 兜底)
# ---------------------------------------------------------------------------


def search_macro_news(
    query: str,
    ctx: Dict[str, Any],  # noqa: ARG001 - reserved for future per-ctx state
) -> List[Dict[str, Any]]:
    """搜索宏观新闻（实接 Tavily / SerpAPI，mock 兜底）。

    Provider 优先级: 显式参数 > TAVILY_API_KEY > SERPAPI_KEY > mock
    Tavily 失败时自动 fallback 到 SerpAPI；都失败时返回 mock 数据。
    全部失败 → 返回 `[]`。

    返回字段契约（与 v1 mock 兼容）:
        ts, headline, url, source, relevance, category, _query
    """
    from llm.news_search import search_news as _search_news_dispatcher

    if not isinstance(query, str):
        query = ""
    # query 清洗（news_search 内部也会清洗，这里只剥控制字符 + 截断到 200）
    query = re.sub(r"[\x00-\x1f\x7f]", "", query)[:200]
    if not query:
        return []

    return _search_news_dispatcher(query)


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "get_market_snapshot": {
        "description": (
            "查询最新 SPY/QQQ/VIX/CAPE/RSI 事实数据。返回字段是 T-1 收盘后的 PIT 快照。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "fn": get_market_snapshot,
    },
    "get_rule_engine_output": {
        "description": (
            "查询规则引擎的当前判定（regime / dca_multiplier / SPY-QQQ 配比 / panic_tier）。"
            "只读 — 不能修改任何字段。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "fn": get_rule_engine_output,
    },
    "get_recent_decisions": {
        "description": (
            "查询近 N 周的决策历史（multiplier / regime / 关键指标）。"
            "n_weeks 默认 8，硬上限 52。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "n_weeks": {
                    "type": "integer",
                    "description": "查询近 N 周（1-52）",
                },
            },
            "required": ["n_weeks"],
        },
        "fn": lambda ctx, n_weeks=8: get_recent_decisions(ctx, n_weeks=n_weeks),
    },
    "search_macro_news": {
        "description": (
            "搜索宏观新闻（实接 Tavily / SerpAPI；都失败时返回 mock 数据）。"
            "返回最多 5 条；source 字段标识 provider（'tavily' / 'serpapi' / 'mock_offline'）。"
            "仅在 VIX>=20 / SPY 单日跌>2% / CAPE 月变动>3 时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词（≤ 200 字符）",
                },
            },
            "required": ["query"],
        },
        "fn": lambda ctx, query="": search_macro_news(query, ctx=ctx),
    },
}


def list_tools() -> List[str]:
    """返回所有可用工具名。"""
    return list(TOOL_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def execute_tool_call(
    name: str,
    args: Dict[str, Any],
    *,
    budget: ToolBudget,
    ctx: Dict[str, Any],
) -> Any:
    """统一调度入口：找工具 → 校验参数 → 扣预算 → 执行。

    失败语义：
      - ``KeyError`` — 工具名未知
      - ``ToolArgError`` — 参数缺失或类型错（**不扣预算**）
      - ``BudgetExceededError`` — 预算耗尽（**不扣预算**）
      - 其他异常 — 由工具抛出，**仍扣预算**（因为已经记账）
    """
    if name not in TOOL_REGISTRY:
        raise KeyError(f"unknown tool: {name}")

    schema = TOOL_REGISTRY[name]
    args = args or {}

    # 1) 参数校验（不扣预算）
    _validate_args(schema, args)

    # 2) 预算检查（不扣预算）
    if budget.used >= budget.max_calls:
        raise BudgetExceededError(
            f"tool budget exhausted ({budget.max_calls} calls used)"
        )

    # 3) 扣预算
    budget.consume()

    # 4) 执行
    fn = schema["fn"]
    try:
        return fn(ctx, **args)
    except BudgetExceededError:
        raise
    except ToolArgError:
        # 工具内部又抛了（不该发生，但兜底）— 不再扣
        budget.used -= 1
        raise
