"""
Tests for scripts/llm_copilot.py v2 — strategy flag + tool budget.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

os.environ["LLM_BACKEND"] = "mock"
os.environ.setdefault("LLM_API_KEY", "test-key")


def _run_cli(extra_args):
    advice_json = ROOT / "references" / "current_run" / "current_market_advice.json"
    if not advice_json.exists():
        pytest.skip("no current_run advice json available")
    # 不用 TemporaryDirectory context — 否则 tmp 在函数返回后被清理
    tmp = tempfile.mkdtemp(prefix="llm_copilot_v2_")
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "llm_copilot.py"),
        "--advice-json", str(advice_json),
        "--output-dir", tmp,
    ] + extra_args
    result = subprocess.run(
        cmd,
        env={**os.environ, "LLM_BACKEND": "mock"},
        capture_output=True, text=True, timeout=30,
    )
    return result, Path(tmp)


def test_cli_strategy_macro_regime(tmp_path_check=False):
    result, out_dir = _run_cli(["--strategy", "etf_macro_regime", "--tool-budget", "3"])
    assert result.returncode == 0, f"stderr: {result.stderr}"
    # v1 review/explanation 仍写
    assert (out_dir / "llm_review.md").exists() or (out_dir / "llm_explanation.md").exists()
    # v2 strategy 文件
    assert (out_dir / "llm_strategy_review.md").exists()


def test_cli_strategy_panic_ladder():
    result, out_dir = _run_cli(["--strategy", "etf_panic_ladder"])
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert (out_dir / "llm_strategy_review.md").exists()


def test_cli_strategy_none_default():
    """不指定 strategy 时不写 llm_strategy_review.md（v1 行为）。"""
    result, out_dir = _run_cli([])
    assert result.returncode == 0
    assert not (out_dir / "llm_strategy_review.md").exists()


def test_cli_strategy_unknown_warns():
    """未知的 strategy 名不应当让 CLI 崩溃。"""
    result, out_dir = _run_cli(["--strategy", "fake_strategy"])
    # exit 0，但 v2 文件不存在
    assert result.returncode == 0
    assert not (out_dir / "llm_strategy_review.md").exists()
    # stderr 应有 warning
    assert "fake_strategy" in result.stderr or "unknown" in result.stderr.lower()


def test_cli_tool_budget_zero_means_no_tools():
    result, out_dir = _run_cli(["--strategy", "etf_macro_regime", "--tool-budget", "0"])
    assert result.returncode == 0
    # budget=0 应当走 v1 fallback
    assert (out_dir / "llm_strategy_review.md").exists()


def test_cli_strategy_review_contains_display_name():
    """llm_strategy_review JSON 应当包含 displayName + toolCalls 字段。"""
    import json
    result, out_dir = _run_cli(["--strategy", "etf_macro_regime", "--tool-budget", "3"])
    assert result.returncode == 0
    target = out_dir / "current_market_advice_with_llm.json"
    assert target.exists()
    data = json.loads(target.read_text(encoding="utf-8"))
    sr = data.get("llm_strategy_review", {})
    assert sr.get("strategy") == "etf_macro_regime"
    assert sr.get("displayName")  # 非空
    # toolCalls 必须是 list（可能为空，因为 mock backend 不发 tool_call）
    assert "toolCalls" in sr
    assert isinstance(sr["toolCalls"], list)
    # review 必须是 dict with LLMReview fields
    assert "verdict" in sr.get("review", {})
    assert "agreement" in sr.get("review", {})
