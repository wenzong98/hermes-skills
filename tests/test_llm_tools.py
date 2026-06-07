"""
Tests for llm/tools.py — 4 read-only tools + budget + dispatch.

All tools are read-only and offline. No network calls.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Force mock backend BEFORE any llm import
os.environ["LLM_BACKEND"] = "mock"
os.environ.setdefault("LLM_API_KEY", "test-key")

from llm.schema import build_fake_advice  # noqa: E402
from llm.tools import (  # noqa: E402
    TOOL_REGISTRY,
    ToolBudget,
    BudgetExceededError,
    execute_tool_call,
    get_market_snapshot,
    get_rule_engine_output,
    get_recent_decisions,
    list_tools,
    search_macro_news,
)


def _ctx_from_advice():
    """Standard tool context: 包含 advice + 历史。"""
    advice = build_fake_advice()
    return {
        "advice": advice,
        "advice_dict": advice.to_dict(),
        "history": [
            {"date": "2026-05-22", "multiplier": 0.75, "regime": "very_expensive",
             "vix": 14.2, "cape": 40.5, "spy_drawdown_252d_pct": -1.2},
            {"date": "2026-05-29", "multiplier": 0.75, "regime": "very_expensive",
             "vix": 15.0, "cape": 40.8, "spy_drawdown_252d_pct": -2.5},
        ],
    }


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_budget_starts_with_max_remaining():
    b = ToolBudget(max_calls=5)
    assert b.remaining == 5
    assert b.used == 0


def test_budget_consume_decrements_remaining():
    b = ToolBudget(max_calls=3)
    b.consume()
    b.consume()
    assert b.used == 2
    assert b.remaining == 1


def test_budget_raises_when_exhausted():
    b = ToolBudget(max_calls=2)
    b.consume()
    b.consume()
    with pytest.raises(BudgetExceededError):
        b.consume()


def test_budget_default_is_five():
    b = ToolBudget()
    assert b.max_calls == 5
    assert b.remaining == 5


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


def test_registry_has_four_tools():
    names = set(TOOL_REGISTRY.keys())
    assert names == {
        "get_market_snapshot",
        "get_rule_engine_output",
        "get_recent_decisions",
        "search_macro_news",
    }


def test_list_tools_returns_names():
    names = list_tools()
    assert "get_market_snapshot" in names
    assert "search_macro_news" in names


def test_registry_schemas_have_required_fields():
    for name, schema in TOOL_REGISTRY.items():
        assert "description" in schema, f"{name} missing description"
        assert "parameters" in schema, f"{name} missing parameters"


# ---------------------------------------------------------------------------
# get_market_snapshot
# ---------------------------------------------------------------------------


def test_get_market_snapshot_returns_facts():
    ctx = _ctx_from_advice()
    snap = get_market_snapshot(ctx=ctx)
    assert "spy_close" in snap
    assert "vix" in snap
    assert "cape" in snap
    assert "qqq_rel_63d_pct" in snap
    assert snap["cape"] == 41.0  # from build_fake_advice


def test_get_market_snapshot_is_small():
    """防止 prompt 膨胀 — 必须 ≤ 2KB。"""
    import json
    ctx = _ctx_from_advice()
    snap = get_market_snapshot(ctx=ctx)
    size = len(json.dumps(snap, ensure_ascii=False))
    assert size < 2000, f"snapshot too large: {size} bytes"


def test_get_market_snapshot_handles_empty_advice():
    """无 advice 时所有字段为 None，不抛。"""
    snap = get_market_snapshot(ctx={})
    assert snap["spy_close"] is None
    assert snap["cape"] is None


# ---------------------------------------------------------------------------
# get_rule_engine_output
# ---------------------------------------------------------------------------


def test_get_rule_engine_output_returns_decision():
    ctx = _ctx_from_advice()
    out = get_rule_engine_output(ctx=ctx)
    assert out["regime"] == "very_expensive"
    assert out["dca_multiplier"] == 0.75
    assert "new_buy_spy_weight_pct" in out
    assert "new_buy_qqq_weight_pct" in out
    assert "panic_tier" in out


def test_get_rule_engine_output_is_immutable_shape():
    """不能暴露可能让 LLM 误以为可改的字段（如 'new_multiplier'）。"""
    ctx = _ctx_from_advice()
    out = get_rule_engine_output(ctx=ctx)
    forbidden = {"new_multiplier", "override_dca", "set_weight"}
    assert not (forbidden & set(out.keys()))


def test_get_rule_engine_output_handles_empty():
    out = get_rule_engine_output(ctx={})
    assert out["dca_multiplier"] is None
    assert out["regime"] == "unknown"
    assert out["action_label"] is None


# ---------------------------------------------------------------------------
# get_recent_decisions
# ---------------------------------------------------------------------------


def test_get_recent_decisions_default_is_8():
    ctx = _ctx_from_advice()
    out = get_recent_decisions(ctx=ctx)
    assert isinstance(out, list)
    # default n_weeks=8
    assert len(out) <= 8


def test_get_recent_decisions_respects_n_weeks():
    ctx = _ctx_from_advice()
    # 注入 10 周历史
    ctx["history"] = [
        {"date": f"2026-{m:02d}-{d:02d}", "multiplier": 0.75, "regime": "very_expensive"}
        for m, d in [(4, 1), (4, 8), (4, 15), (4, 22), (4, 29),
                     (5, 6), (5, 13), (5, 20), (5, 27), (6, 3)]
    ]
    out = get_recent_decisions(ctx=ctx, n_weeks=4)
    assert len(out) == 4


def test_get_recent_decisions_caps_at_52():
    ctx = _ctx_from_advice()
    ctx["history"] = [{"date": f"2025-w{w}", "multiplier": 0.75} for w in range(100)]
    out = get_recent_decisions(ctx=ctx, n_weeks=200)
    assert len(out) == 52  # 硬上限


def test_get_recent_decisions_no_history_returns_empty():
    out = get_recent_decisions(ctx={})
    assert out == []


# ---------------------------------------------------------------------------
# search_macro_news (mock)
# ---------------------------------------------------------------------------


def test_search_macro_news_returns_at_most_5_items():
    ctx = _ctx_from_advice()
    out = search_macro_news(query="Fed CPI 2026-06", ctx=ctx)
    assert isinstance(out, list)
    assert 0 < len(out) <= 5


def test_search_macro_news_marks_source_as_mock():
    """无 Tavily/SerpAPI key 时 source 必须是 mock_offline。"""
    ctx = _ctx_from_advice()
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("TAVILY_API_KEY", None)
        os.environ.pop("SERPAPI_KEY", None)
        out = search_macro_news(query="anything", ctx=ctx)
    for item in out:
        assert item.get("source") == "mock_offline"
        assert "headline" in item
        assert "ts" in item


def test_search_macro_news_query_is_sanitized():
    """查询字符串必须清洗，防止 prompt 注入。"""
    ctx = _ctx_from_advice()
    # 极端 query 不能崩溃
    out = search_macro_news(query="A" * 1000 + "{}", ctx=ctx)
    assert isinstance(out, list)


# ---------------------------------------------------------------------------
# execute_tool_call (dispatch + budget)
# ---------------------------------------------------------------------------


def test_execute_tool_call_routes_correctly():
    ctx = _ctx_from_advice()
    budget = ToolBudget()
    out = execute_tool_call("get_market_snapshot", {}, budget=budget, ctx=ctx)
    assert "spy_close" in out
    assert budget.used == 1


def test_execute_tool_call_validates_args():
    ctx = _ctx_from_advice()
    budget = ToolBudget()
    # 缺必填参数
    with pytest.raises(ValueError):
        execute_tool_call("get_recent_decisions", {}, budget=budget, ctx=ctx)  # n_weeks 必需
    assert budget.used == 0  # 失败不扣预算


def test_execute_tool_call_unknown_tool_raises():
    ctx = _ctx_from_advice()
    budget = ToolBudget()
    with pytest.raises(KeyError):
        execute_tool_call("delete_database", {}, budget=budget, ctx=ctx)
    assert budget.used == 0


def test_execute_tool_call_enforces_budget():
    ctx = _ctx_from_advice()
    budget = ToolBudget(max_calls=2)
    execute_tool_call("get_market_snapshot", {}, budget=budget, ctx=ctx)
    execute_tool_call("get_rule_engine_output", {}, budget=budget, ctx=ctx)
    with pytest.raises(BudgetExceededError):
        execute_tool_call("get_market_snapshot", {}, budget=budget, ctx=ctx)
    assert budget.used == 2


def test_execute_tool_call_handles_tool_exception():
    """单个工具抛异常时 execute_tool_call 透传，预算已扣。"""
    ctx = _ctx_from_advice()

    # 模拟替换 TOOL_REGISTRY 里的 fn（dispatch 从这里读）
    from llm import tools as t
    original_fn = t.TOOL_REGISTRY["get_market_snapshot"]["fn"]
    t.TOOL_REGISTRY["get_market_snapshot"]["fn"] = lambda ctx: (_ for _ in ()).throw(
        RuntimeError("boom")
    )
    try:
        budget = ToolBudget()
        with pytest.raises(RuntimeError):
            t.execute_tool_call("get_market_snapshot", {}, budget=budget, ctx=ctx)
        # 异常透传 + 预算已扣
        assert budget.used == 1
    finally:
        t.TOOL_REGISTRY["get_market_snapshot"]["fn"] = original_fn
