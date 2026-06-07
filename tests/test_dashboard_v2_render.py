"""
Tests for build_dashboard.py strategyReview block (Phase 2b).

We exercise the block function directly with synthetic advice JSON.
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


def _fresh_dashboard_import():
    """Import build_dashboard fresh — module-level state matters."""
    if "build_dashboard" in sys.modules:
        del sys.modules["build_dashboard"]
    import build_dashboard
    return build_dashboard


# ---------------------------------------------------------------------------
# _llm_strategy_review_block
# ---------------------------------------------------------------------------


def test_strategy_review_block_returns_unavailable_when_missing():
    bd = _fresh_dashboard_import()
    out = bd._llm_strategy_review_block({})
    assert out["available"] is False
    assert out["strategyName"] == ""
    assert out["toolCalls"] == []


def test_strategy_review_block_returns_unavailable_when_disabled():
    bd = _fresh_dashboard_import()
    out = bd._llm_strategy_review_block({
        "llm_strategy_review": {
            "strategy": "etf_macro_regime",
            "review": {"enabled": False},
            "toolCalls": [],
        }
    })
    assert out["available"] is False
    # 即便 unavailable，name 仍应回传
    assert out["strategyName"] == "etf_macro_regime"


def test_strategy_review_block_full():
    bd = _fresh_dashboard_import()
    out = bd._llm_strategy_review_block({
        "llm_strategy_review": {
            "strategy": "etf_macro_regime",
            "displayName": "ETF 宏观周期审查",
            "review": {
                "enabled": True,
                "model": "claude-3-5-haiku-latest",
                "verdict": "CAPE 41 确认 very_expensive 档",
                "agreement": "agree",
                "risks_blindspots": [
                    "Fed 利率路径仍不明朗",
                    "10Y 收益率若跳升 >20bp 需更激进节流",
                ],
                "reminder": "按 0.75x 节流执行",
                "input_tokens": 2771,
                "output_tokens": 178,
                "generated_at": "2026-06-06T14:00:00+08:00",
            },
            "toolCalls": [
                {
                    "name": "get_market_snapshot",
                    "args": {},
                    "result_preview": "{spy_close: 600.0, cape: 41.0, vix: 15.4}",
                },
                {
                    "name": "get_rule_engine_output",
                    "args": {},
                    "result_preview": "{regime: very_expensive, dca_multiplier: 0.75}",
                },
            ],
        }
    })
    assert out["available"] is True
    assert out["strategyName"] == "etf_macro_regime"
    assert out["strategyDisplayName"] == "ETF 宏观周期审查"
    assert out["verdict"] == "CAPE 41 确认 very_expensive 档"
    assert out["agreement"] == "agree"
    assert out["agreementLabel"] == "🟢 同意"
    assert out["agreementTone"] == "positive"
    assert len(out["risksBlindspots"]) == 2
    assert out["reminder"] == "按 0.75x 节流执行"
    assert out["toolCallCount"] == 2
    assert len(out["toolCalls"]) == 2
    assert out["toolCalls"][0]["name"] == "get_market_snapshot"
    assert out["inputTokens"] == 2771
    assert out["outputTokens"] == 178
    assert out["model"] == "claude-3-5-haiku-latest"


def test_strategy_review_block_truncates_tool_calls():
    """toolCalls 超过 10 时应被裁剪。"""
    bd = _fresh_dashboard_import()
    tool_calls = [{"name": f"tool_{i}", "args": {}, "result_preview": f"r{i}"} for i in range(20)]
    out = bd._llm_strategy_review_block({
        "llm_strategy_review": {
            "strategy": "x",
            "review": {"enabled": True, "agreement": "agree"},
            "toolCalls": tool_calls,
        }
    })
    assert len(out["toolCalls"]) == 10
    assert out["toolCallCount"] == 10


def test_strategy_review_block_clamps_long_preview():
    bd = _fresh_dashboard_import()
    out = bd._llm_strategy_review_block({
        "llm_strategy_review": {
            "strategy": "x",
            "review": {"enabled": True, "agreement": "agree"},
            "toolCalls": [{
                "name": "x", "args": {}, "result_preview": "A" * 1000,
            }],
        }
    })
    assert len(out["toolCalls"][0]["resultPreview"]) == 300


def test_strategy_review_block_handles_garbage():
    """损坏数据应被容错处理，不抛。"""
    bd = _fresh_dashboard_import()
    out = bd._llm_strategy_review_block({
        "llm_strategy_review": "not a dict"
    })
    assert out["available"] is False

    out = bd._llm_strategy_review_block({
        "llm_strategy_review": {
            "review": "not a dict",
            "toolCalls": "not a list",
        }
    })
    assert out["available"] is False
    assert out["toolCalls"] == []


def test_strategy_review_block_maps_agreement_tone():
    """三种 agreement 应映射到正确的 tone。"""
    bd = _fresh_dashboard_import()
    for ag, expected_label, expected_tone in [
        ("agree", "🟢 同意", "positive"),
        ("caution", "🟡 谨慎同意", "warning"),
        ("disagree", "🔴 不同意", "critical"),
    ]:
        out = bd._llm_strategy_review_block({
            "llm_strategy_review": {
                "strategy": "x",
                "review": {"enabled": True, "agreement": ag},
            }
        })
        assert out["agreementLabel"] == expected_label, ag
        assert out["agreementTone"] == expected_tone, ag


def test_latest_advice_path_prefers_with_llm_on_tie():
    """当 plain 和 with_llm 同 timestamp 时，应优先 with_llm（含 LLM 审查）。"""
    bd = _fresh_dashboard_import()
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Mock 两个文件 — 同 generated_at
        common = {
            "meta": {"generated_at": "2026-06-07T10:00:00+08:00"},
            "market": {"latest_market_date": "2026-06-05"},
        }
        plain = tmp_path / "current_market_advice.json"
        with_llm = tmp_path / "current_market_advice_with_llm.json"
        plain.write_text(json.dumps({**common, "decision": {"dca_multiplier": 0.75}}))
        with_llm.write_text(json.dumps({
            **common,
            "decision": {"dca_multiplier": 0.75},
            "llm_review": {"enabled": True, "verdict": "test"},
        }))

        chosen = bd._latest_advice_path([plain, with_llm])
        assert chosen == with_llm, f"应优先 with_llm，实际选了 {chosen.name}"


# ---------------------------------------------------------------------------
# _macro_feeds_block
# ---------------------------------------------------------------------------


def test_macro_feeds_block_returns_unavailable_when_empty():
    """reference feeds 全空时返回 available=False。"""
    bd = _fresh_dashboard_import()
    with patch("llm.reference_feeds.fetch_all_reference_feeds", return_value={"rss": [], "gdelt": [], "calendar": []}):
        out = bd._macro_feeds_block({})
    assert out["available"] is False
    assert out["rss"] == []
    assert out["gdelt"] == []
    assert out["calendar"] == []


def test_macro_feeds_block_uses_default_path():
    """应当使用 ROOT/references/data_cache/macro_feeds 作为缓存。"""
    bd = _fresh_dashboard_import()
    with patch("llm.reference_feeds.fetch_all_reference_feeds", return_value={"rss": [], "gdelt": [], "calendar": []}) as m:
        bd._macro_feeds_block({})
    # 检查 cache_dir 参数指向正确路径
    call_kwargs = m.call_args.kwargs
    cache_dir = str(call_kwargs.get("cache_dir", ""))
    assert "macro_feeds" in cache_dir


def test_macro_feeds_block_trims_and_normalizes():
    bd = _fresh_dashboard_import()
    fake_out = {
        "rss": [
            {"ts": "Wed, 04 Jun 2026", "headline": "X" * 200, "url": "https://x.com",
             "source": "rss:reuters", "category": "fed", "origin": "rss"},
        ],
        "gdelt": [
            {"ts": "20260604T140000Z", "headline": "G", "url": "https://g.com",
             "source": "gdelt", "origin": "gdelt", "tone": -3.5, "country": "US"},
        ],
        "calendar": [
            {"date": "2026-06-12", "time": "14:00", "event": "FOMC",
             "country": "US", "importance": 3, "source": "calendar", "origin": "calendar"},
        ],
    }
    # Patch must cover both module attribute AND local import inside _macro_feeds_block
    with patch("llm.reference_feeds.fetch_all_reference_feeds", return_value=fake_out):
        with patch("llm.reference_feeds.translate_to_chinese", return_value=[None] * 3):
            out = bd._macro_feeds_block({})
    assert out["available"] is True
    assert len(out["rss"]) == 1
    # headline 截断到 120
    assert len(out["rss"][0]["headline"]) == 120
    # gdelt tone 透传
    assert out["gdelt"][0]["tone"] == -3.5
    # calendar event 字段作为 headline
    assert out["calendar"][0]["headline"] == "FOMC"


def test_macro_feeds_block_handles_fetch_failure():
    """fetch 抛异常时返回空块，不让 dashboard 崩。"""
    bd = _fresh_dashboard_import()
    with patch("llm.reference_feeds.fetch_all_reference_feeds", side_effect=RuntimeError("boom")):
        out = bd._macro_feeds_block({})
    assert out["available"] is False
    assert "error" in out
