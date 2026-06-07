"""
Tests for llm/strategies.py — ETF 策略 YAML loader.

策略文件用自包含的简版 YAML 解析（不引入 PyYAML）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm.strategies import (  # noqa: E402
    StrategySpec,
    list_strategies,
    load_strategies,
    get_strategy,
    build_strategy_system_prompt,
)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_load_strategies_finds_two_yaml_files():
    strategies = load_strategies()
    names = set(strategies.keys())
    assert "etf_macro_regime" in names
    assert "etf_panic_ladder" in names


def test_get_strategy_returns_spec():
    s = get_strategy("etf_macro_regime")
    assert s is not None
    assert s.name == "etf_macro_regime"
    assert s.display_name
    assert s.description
    assert s.instructions


def test_get_strategy_unknown_returns_none():
    assert get_strategy("not_a_real_strategy") is None


def test_list_strategies_returns_names():
    names = list_strategies()
    assert "etf_macro_regime" in names
    assert "etf_panic_ladder" in names


# ---------------------------------------------------------------------------
# Field integrity
# ---------------------------------------------------------------------------


def test_etf_macro_regime_required_fields():
    s = get_strategy("etf_macro_regime")
    assert s is not None
    assert isinstance(s.core_rules, list)
    assert len(s.core_rules) >= 1
    assert "get_market_snapshot" in s.required_tools
    assert "get_rule_engine_output" in s.required_tools
    # instructions 必须有步骤和硬规则
    assert "【步骤】" in s.instructions
    assert "硬规则" in s.instructions


def test_etf_panic_ladder_required_fields():
    s = get_strategy("etf_panic_ladder")
    assert s is not None
    assert 8 in s.core_rules  # 引用方案 8
    assert "get_market_snapshot" in s.required_tools
    assert "get_recent_decisions" in s.required_tools
    # 必须明确阶梯阈值
    assert "1.25" in s.instructions
    assert "1.5" in s.instructions or "1.50" in s.instructions
    assert "2.0" in s.instructions or "2.00" in s.instructions


def test_strategies_have_no_overlap_with_dangerous_tools():
    """策略里不能引用未注册的工具。"""
    from llm.tools import TOOL_REGISTRY
    valid = set(TOOL_REGISTRY.keys())
    for s in load_strategies().values():
        for tool in s.required_tools:
            assert tool in valid, f"strategy {s.name} uses unregistered tool {tool}"


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------


def test_build_strategy_system_prompt_includes_tools_section():
    s = get_strategy("etf_macro_regime")
    assert s is not None
    prompt = build_strategy_system_prompt(s)
    assert "etf_macro_regime" in prompt
    assert "get_market_snapshot" in prompt
    assert "get_rule_engine_output" in prompt
    assert "硬规则" in prompt or "硬" in prompt


def test_build_strategy_system_prompt_ends_with_json_schema():
    s = get_strategy("etf_panic_ladder")
    assert s is not None
    prompt = build_strategy_system_prompt(s)
    assert "verdict" in prompt
    assert "agreement" in prompt
    assert "risks_blindspots" in prompt


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


def test_load_strategies_silently_skips_broken_yaml(tmp_path, monkeypatch, capsys):
    """损坏的 YAML 不能让加载函数崩溃 — 加载器应只 log warning。"""
    from llm import strategies as strat_mod

    # 临时替换 STRATEGY_DIR
    broken = tmp_path / "broken.yaml"
    broken.write_text("not: valid: yaml: at all\n::")
    monkeypatch.setattr(strat_mod, "STRATEGY_DIR", tmp_path)

    out = strat_mod.load_strategies()
    # 加载器应返回 dict（可能空），不抛
    assert isinstance(out, dict)
