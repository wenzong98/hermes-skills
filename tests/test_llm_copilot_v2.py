"""
Tests for llm/advisor.py v2 — review_with_tools (Plan A2).

v2 让 LLM 主动调用工具查询数据，再生成审查结论。
失败时降级到 v1 ``review_signal()``。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Force mock backend
os.environ["LLM_BACKEND"] = "mock"
os.environ.setdefault("LLM_API_KEY", "test-key")

from llm.advisor import (  # noqa: E402
    REVIEW_SYSTEM_V2,
    parse_tool_call,
    format_tool_result_block,
    review_with_tools,
    render_strategy_review_markdown,
)
from llm.client import LLMCallResult, get_llm_config  # noqa: E402
from llm.schema import WeeklyAdvice, build_fake_advice  # noqa: E402
from llm.strategies import get_strategy  # noqa: E402
from llm.tools import ToolBudget  # noqa: E402


# ---------------------------------------------------------------------------
# Tool-call protocol
# ---------------------------------------------------------------------------


def test_parse_tool_call_extracts_single_call():
    text = (
        "我来查一下市场数据。\n"
        "<tool_call>"
        '{"name": "get_market_snapshot", "args": {}}'
        "</tool_call>"
    )
    calls = parse_tool_call(text)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_market_snapshot"
    assert calls[0]["args"] == {}


def test_parse_tool_call_extracts_multiple_calls():
    text = (
        "<tool_call>"
        '{"name": "get_market_snapshot", "args": {}}'
        "</tool_call>"
        "<tool_call>"
        '{"name": "get_rule_engine_output", "args": {}}'
        "</tool_call>"
    )
    calls = parse_tool_call(text)
    assert len(calls) == 2


def test_parse_tool_call_handles_no_call():
    text = "I'm not going to call any tools, just final answer: {...}"
    assert parse_tool_call(text) == []


def test_parse_tool_call_handles_malformed_json():
    text = '<tool_call>{"name": "broken"</tool_call>'
    # 不抛，丢弃坏数据
    calls = parse_tool_call(text)
    assert calls == []


def test_parse_tool_call_handles_args_with_nested_json():
    text = (
        "<tool_call>"
        '{"name": "search_macro_news", "args": {"query": "Fed CPI"}}'
        "</tool_call>"
    )
    calls = parse_tool_call(text)
    assert calls[0]["args"] == {"query": "Fed CPI"}


def test_format_tool_result_block_is_valid_json():
    block = format_tool_result_block(
        "get_market_snapshot",
        {"spy_close": 600.0, "vix": 15.4},
    )
    assert "get_market_snapshot" in block
    # 提取第一个 JSON 字典（```json ... ``` 包裹）
    import re
    m = re.search(r"```json\s*(\{.*?\})\s*```", block, re.DOTALL)
    assert m is not None, f"no json block found in: {block}"
    parsed = json.loads(m.group(1))
    assert parsed["spy_close"] == 600.0
    assert parsed["vix"] == 15.4


# ---------------------------------------------------------------------------
# Mock backend for tool loop
# ---------------------------------------------------------------------------


def _tool_calling_mock_backend(call_idx: int) -> LLMCallResult:
    """模拟 LLM 流程：第 1/2 次返回 tool_call，第 3 次返回最终 JSON。"""
    if call_idx == 1:
        content = '<tool_call>{"name": "get_market_snapshot", "args": {}}</tool_call>'
    elif call_idx == 2:
        content = '<tool_call>{"name": "get_rule_engine_output", "args": {}}</tool_call>'
    else:
        content = json.dumps({
            "verdict": "工具调用完成，系统判定无盲点",
            "agreement": "agree",
            "risks_blindspots": ["CAPE 41 偏高，但趋势确认节流已到位"],
            "reminder": "按系统建议执行",
        }, ensure_ascii=False)

    return LLMCallResult(
        content=content,
        input_tokens=100,
        output_tokens=len(content),
        elapsed_s=0.001,
        model="mock",
        backend="mock",
    )


# ---------------------------------------------------------------------------
# review_with_tools main flow
# ---------------------------------------------------------------------------


def test_review_with_tools_completes_after_3_turns():
    advice = build_fake_advice()
    cfg = get_llm_config()

    call_count = {"n": 0}

    def fake_call_llm(system, user, cfg=None):
        call_count["n"] += 1
        return _tool_calling_mock_backend(call_count["n"])

    with patch("llm.advisor.call_llm", side_effect=fake_call_llm):
        review = review_with_tools(advice, cfg=cfg, tool_budget=5, enable_tools=True)

    assert review.enabled
    assert review.error is None
    assert review.verdict == "工具调用完成，系统判定无盲点"
    assert review.agreement == "agree"
    assert "CAPE 41 偏高" in review.risks_blindspots[0]
    # 工具调用了 2 次 + 最终生成 1 次 = 3 次 LLM 调用
    assert call_count["n"] == 3


def test_review_with_tools_records_tool_call_log():
    """v2 必须把工具调用历史记入 LLMReview metadata（如果未来有该字段的话，
    目前在生成时输出到 llm_strategy_review.md 的 "工具调用" 段）。"""
    advice = build_fake_advice()
    cfg = get_llm_config()

    # 直接测渲染函数
    md = render_strategy_review_markdown(
        review=None,  # 不需要 review，tool_log 已包含信息
        advice=advice,
        strategy_name="etf_macro_regime",
        tool_log=[
            {"name": "get_market_snapshot", "args": {}, "result_preview": "{spy_close: 600.0}"},
            {"name": "get_rule_engine_output", "args": {}, "result_preview": "{regime: very_expensive}"},
        ],
    )
    assert "etf_macro_regime" in md
    assert "get_market_snapshot" in md
    assert "get_rule_engine_output" in md


def test_review_with_tools_falls_back_to_v1_on_disabled():
    """enable_tools=False 时应当调用 review_signal() 的等价逻辑。"""
    advice = build_fake_advice()
    cfg = get_llm_config()

    # 用一个不返回 tool_call 的 LLM（第一次就输出 final）
    call_count = {"n": 0}

    def fake_call_llm(system, user, cfg=None):
        call_count["n"] += 1
        return LLMCallResult(
            content=json.dumps({
                "verdict": "v2 关闭，走 v1 静态审查",
                "agreement": "agree",
                "risks_blindspots": [],
                "reminder": "无",
            }, ensure_ascii=False),
            input_tokens=10,
            output_tokens=20,
            elapsed_s=0.001,
            model="mock",
            backend="mock",
        )

    with patch("llm.advisor.call_llm", side_effect=fake_call_llm):
        review = review_with_tools(advice, cfg=cfg, enable_tools=False)

    assert review.enabled
    assert review.verdict == "v2 关闭，走 v1 静态审查"
    assert call_count["n"] == 1  # 1 次直接出结论


def test_review_with_tools_handles_tool_error_gracefully():
    """某个工具抛异常时，loop 不应崩溃。"""
    advice = build_fake_advice()
    cfg = get_llm_config()

    # 替换 get_market_snapshot 抛异常
    from llm import tools as t
    original_fn = t.TOOL_REGISTRY["get_market_snapshot"]["fn"]
    t.TOOL_REGISTRY["get_market_snapshot"]["fn"] = lambda ctx: (_ for _ in ()).throw(
        RuntimeError("tool down")
    )

    call_count = {"n": 0}

    def fake_call_llm(system, user, cfg=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMCallResult(
                content='<tool_call>{"name": "get_market_snapshot", "args": {}}</tool_call>',
                input_tokens=10, output_tokens=20, elapsed_s=0.001,
                model="mock", backend="mock",
            )
        return LLMCallResult(
            content=json.dumps({
                "verdict": "工具异常但仍出结论",
                "agreement": "caution",
                "risks_blindspots": ["get_market_snapshot 异常"],
                "reminder": "保留人工判断",
            }, ensure_ascii=False),
            input_tokens=10, output_tokens=20, elapsed_s=0.001,
            model="mock", backend="mock",
        )

    try:
        with patch("llm.advisor.call_llm", side_effect=fake_call_llm):
            review = review_with_tools(advice, cfg=cfg, tool_budget=5, enable_tools=True)

        assert review.enabled
        assert review.verdict == "工具异常但仍出结论"
        assert review.agreement == "caution"
    finally:
        t.TOOL_REGISTRY["get_market_snapshot"]["fn"] = original_fn


def test_review_with_tools_respects_budget():
    """工具调用超 5 次时，loop 必须终止。"""
    advice = build_fake_advice()
    cfg = get_llm_config()

    call_count = {"n": 0}

    def always_tool_call(system, user, cfg=None):
        call_count["n"] += 1
        return LLMCallResult(
            content='<tool_call>{"name": "get_market_snapshot", "args": {}}</tool_call>',
            input_tokens=10, output_tokens=20, elapsed_s=0.001,
            model="mock", backend="mock",
        )

    with patch("llm.advisor.call_llm", side_effect=always_tool_call):
        review = review_with_tools(advice, cfg=cfg, tool_budget=5, enable_tools=True)

    # 应当终止于 budget 耗尽
    # 5 次工具 + 1 次终止
    # 实际可能 6-10 次（取决于实现），但必须有 verdict 输出
    assert review.enabled
    assert review.verdict is not None or review.error is not None
    # 关键：call_count 不应超过 12（5 工具 + 1 终止 + buffer）
    assert call_count["n"] <= 12, f"loop runaway: {call_count['n']} calls"


def test_review_with_tools_returns_error_on_total_failure():
    """LLM 完全无响应时，error 字段必须有。"""
    advice = build_fake_advice()
    cfg = get_llm_config()

    def always_empty(system, user, cfg=None):
        return LLMCallResult(
            content="", input_tokens=0, output_tokens=0, elapsed_s=0.001,
            model="mock", backend="mock",
        )

    with patch("llm.advisor.call_llm", side_effect=always_empty):
        review = review_with_tools(advice, cfg=cfg, tool_budget=5, enable_tools=True)

    assert review.enabled
    assert review.error  # 必有 error


# ---------------------------------------------------------------------------
# System prompt (REVIEW_SYSTEM_V2)
# ---------------------------------------------------------------------------


def test_review_system_v2_mentions_tools():
    assert "get_market_snapshot" in REVIEW_SYSTEM_V2
    assert "search_macro_news" in REVIEW_SYSTEM_V2
    assert "硬规则" in REVIEW_SYSTEM_V2
    assert "JSON" in REVIEW_SYSTEM_V2


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def test_render_strategy_review_markdown_uses_strategy_prompt():
    s = get_strategy("etf_macro_regime")
    assert s is not None
    advice = build_fake_advice()
    md = render_strategy_review_markdown(
        review=None,
        advice=advice,
        strategy_name="etf_macro_regime",
        tool_log=[],
    )
    # 包含 display_name
    assert s.display_name in md
    # 没有 tool log 时仍能渲染
    assert "工具调用" in md or "无工具" in md


# ---------------------------------------------------------------------------
# review_with_tools_ex (returns tuple)
# ---------------------------------------------------------------------------


def test_review_with_tools_ex_returns_tool_log():
    """扩展版必须把 tool_log 一起返回。"""
    from llm.advisor import review_with_tools_ex
    from llm.client import LLMCallResult

    advice = build_fake_advice()
    cfg = get_llm_config()
    call_count = {"n": 0}

    def fake_call_llm(system, user, cfg=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMCallResult(
                content='<tool_call>{"name": "get_market_snapshot", "args": {}}</tool_call>',
                input_tokens=10, output_tokens=20, elapsed_s=0.001,
                model="mock", backend="mock",
            )
        return LLMCallResult(
            content=json.dumps({
                "verdict": "ok", "agreement": "agree", "risks_blindspots": [], "reminder": "ok"
            }, ensure_ascii=False),
            input_tokens=10, output_tokens=20, elapsed_s=0.001,
            model="mock", backend="mock",
        )

    with patch("llm.advisor.call_llm", side_effect=fake_call_llm):
        review, tool_log = review_with_tools_ex(
            advice, cfg=cfg, tool_budget=5, enable_tools=True
        )

    assert review.enabled
    assert review.verdict == "ok"
    assert len(tool_log) == 1
    assert tool_log[0]["name"] == "get_market_snapshot"
    assert "result_preview" in tool_log[0]
    assert tool_log[0]["args"] == {}


def test_review_with_tools_ex_disabled_returns_empty_log():
    """未启用时返回 disabled review + 空 tool_log。"""
    from llm.advisor import review_with_tools_ex
    advice = build_fake_advice()
    cfg = {
        "api_key": "",
        "base_url": "",
        "model": "claude-3-5-haiku-latest",
        "backend": "anthropic",
        "timeout_s": "5",
        "max_retries": "0",
        "usage_log": "/tmp/llm_disabled.jsonl",
    }
    review, tool_log = review_with_tools_ex(advice, cfg=cfg)
    assert not review.enabled
    assert tool_log == []


def test_review_with_tools_ex_enable_tools_false_skips_tools():
    """enable_tools=False 时直接出 final，不调工具。"""
    from llm.advisor import review_with_tools_ex
    from llm.client import LLMCallResult

    advice = build_fake_advice()
    cfg = get_llm_config()

    def fake_call_llm(system, user, cfg=None):
        return LLMCallResult(
            content=json.dumps({
                "verdict": "v1 fallback", "agreement": "agree",
                "risks_blindspots": [], "reminder": "ok"
            }, ensure_ascii=False),
            input_tokens=10, output_tokens=20, elapsed_s=0.001,
            model="mock", backend="mock",
        )

    with patch("llm.advisor.call_llm", side_effect=fake_call_llm):
        review, tool_log = review_with_tools_ex(
            advice, cfg=cfg, enable_tools=False
        )

    assert review.verdict == "v1 fallback"
    assert tool_log == []
