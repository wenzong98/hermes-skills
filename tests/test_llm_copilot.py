"""
Tests for LLM copilot module: schema, client (mock), advisor.

CI friendly: no real LLM calls — ``LLM_BACKEND=mock`` is forced.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Force mock backend BEFORE any llm import so config is read correctly
os.environ["LLM_BACKEND"] = "mock"
os.environ.setdefault("LLM_API_KEY", "test-key")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from llm.advisor import (  # noqa: E402
    explain_decision,
    fallback_explanation_text,
    fallback_review_text,
    review_signal,
)
from llm.client import call_llm, get_llm_config, is_llm_enabled  # noqa: E402
from llm.schema import (  # noqa: E402
    WeeklyAdvice,
    _to_float,
    build_fake_advice,
)


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


def test_to_float_handles_none_and_strings():
    assert _to_float(None) is None
    assert _to_float("") is None
    assert _to_float("nan") is None
    assert _to_float("12.5") == 12.5
    assert _to_float(12.5) == 12.5
    assert _to_float("not a number") is None
    # NaN
    nan = float("nan")
    assert _to_float(nan) is None


def test_to_float_handles_nan_value():
    assert _to_float(float("nan")) is None


def test_build_fake_advice_is_well_formed():
    advice = build_fake_advice()
    assert advice.market.cape == 41.0
    assert advice.decision.dca_multiplier == 0.75
    assert advice.diagnosis.regime == "very_expensive"
    assert advice.market.spy_close == 600.0
    assert advice.llm_review is None
    assert advice.llm_explanation is None


def test_weekly_advice_from_payload_dict_is_lenient():
    """空 / 缺字段 / 错误类型 — 都不能抛。"""
    advice = WeeklyAdvice.from_payload_dict({})
    assert advice.market.latest_market_date == ""
    assert advice.decision.dca_multiplier == 0.0

    advice = WeeklyAdvice.from_payload_dict({"market": {"latest_market_date": "2026-06-05"}})
    assert advice.market.latest_market_date == "2026-06-05"
    assert advice.decision.dca_multiplier == 0.0

    # 错误类型字段
    advice = WeeklyAdvice.from_payload_dict({
        "meta": {"generated_at": 12345},  # 不是字符串
        "market": {"cape": "not a number"},
    })
    assert advice.generated_at == "12345"  # str() 转换
    assert advice.market.cape is None


def test_weekly_advice_from_payload_dict_rejects_non_dict():
    with pytest.raises(TypeError):
        WeeklyAdvice.from_payload_dict("not a dict")  # type: ignore[arg-type]


def test_weekly_advice_to_dict_roundtrip():
    advice = build_fake_advice()
    d = advice.to_dict()
    assert d["market"]["cape"] == 41.0
    assert d["decision"]["dca_multiplier"] == 0.75


# ---------------------------------------------------------------------------
# Client tests
# ---------------------------------------------------------------------------


def test_is_llm_enabled_with_mock_backend():
    cfg = get_llm_config()
    assert cfg["backend"] == "mock"
    assert is_llm_enabled(cfg) is True


def test_is_llm_enabled_without_key_non_mock(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    cfg = get_llm_config()
    assert is_llm_enabled(cfg) is False


def test_call_llm_mock_returns_json():
    cfg = get_llm_config()
    result = call_llm("you are a review", "please review", cfg=cfg)
    assert result.content
    parsed = json.loads(result.content)
    assert "verdict" in parsed or "explanation" in parsed
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.backend == "mock"


def test_call_llm_disabled_returns_empty():
    """未启用时（无 key + 非 mock backend）应当返回空 content 而不抛异常。"""
    cfg = {
        "api_key": "",
        "base_url": "",
        "model": "claude-3-5-haiku-latest",
        "backend": "anthropic",
        "timeout_s": "5",
        "max_retries": "0",
        "usage_log": "/tmp/llm_disabled_usage.jsonl",
    }
    result = call_llm("sys", "user", cfg=cfg)
    assert result.content == ""
    assert result.input_tokens == 0


# ---------------------------------------------------------------------------
# Advisor tests (Plan A + Plan B)
# ---------------------------------------------------------------------------


def test_review_signal_returns_agree_for_fake_advice():
    advice = build_fake_advice()
    review = review_signal(advice)
    assert review.enabled
    assert review.error is None
    assert review.verdict
    assert review.agreement in {"agree", "caution", "disagree"}
    assert review.input_tokens > 0


def test_explain_decision_returns_text_for_fake_advice():
    advice = build_fake_advice()
    expl = explain_decision(advice)
    assert expl.enabled
    assert expl.error is None
    assert expl.explanation
    assert 50 <= len(expl.explanation) <= 1000  # 在合理长度区间
    assert expl.input_tokens > 0


def test_fallback_review_text_for_various_multipliers():
    advice = build_fake_advice()

    advice.decision.dca_multiplier = 1.0
    assert "维持" in fallback_review_text(advice) or "加大" in fallback_review_text(advice)

    advice.decision.dca_multiplier = 0.75
    text = fallback_review_text(advice)
    assert "0.75" in text or "降低" in text

    advice.decision.dca_multiplier = 0.0
    text = fallback_review_text(advice)
    assert "显著" in text or "暂停" in text


def test_fallback_explanation_includes_facts():
    advice = build_fake_advice()
    text = fallback_explanation_text(advice)
    # CAPE 41 应当出现在说明里
    assert "41" in text or "估值" in text
    # multiplier 应当出现
    assert "0.75" in text
    # SPY/QQQ 配比
    assert "40" in text and "60" in text


# ---------------------------------------------------------------------------
# End-to-end CLI test (uses mock backend, no network)
# ---------------------------------------------------------------------------


def test_llm_copilot_cli_with_existing_advice():
    """端到端：CLI 读 advice-json → 写 LLM 增强文件。"""
    import subprocess

    advice_json = ROOT / "references" / "current_run" / "current_market_advice.json"
    if not advice_json.exists():
        pytest.skip("no current_run advice json available")

    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "llm_copilot.py"),
                "--advice-json", str(advice_json),
                "--output-dir", tmp,
            ],
            env={**os.environ, "LLM_BACKEND": "mock"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        out_dir = Path(tmp)
        assert (out_dir / "llm_review.md").exists()
        assert (out_dir / "llm_explanation.md").exists()
        assert (out_dir / "current_market_advice_with_llm.json").exists()

        merged = json.loads((out_dir / "current_market_advice_with_llm.json").read_text())
        assert "llm_review" in merged
        assert "llm_explanation" in merged
        assert merged["llm_review"]["enabled"] is True
        assert merged["llm_explanation"]["enabled"] is True


def test_llm_copilot_cli_with_plan_filter():
    """只跑方案 A：review。explain 应当不出现。"""
    import subprocess

    advice_json = ROOT / "references" / "current_run" / "current_market_advice.json"
    if not advice_json.exists():
        pytest.skip("no current_run advice json available")

    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "llm_copilot.py"),
                "--advice-json", str(advice_json),
                "--output-dir", tmp,
                "--plans", "review",
            ],
            env={**os.environ, "LLM_BACKEND": "mock"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        out_dir = Path(tmp)
        assert (out_dir / "llm_review.md").exists()
        # explain 没跑 → 没有 explanation 字段
        merged = json.loads((out_dir / "current_market_advice_with_llm.json").read_text())
        assert "llm_review" in merged
        assert "llm_explanation" not in merged
