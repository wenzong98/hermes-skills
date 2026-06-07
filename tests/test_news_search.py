"""
Tests for llm/news_search.py — Tavily + SerpAPI + mock backends.

We mock urllib.request.urlopen to avoid real network calls.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Strip Tavily/SerpAPI keys so unit tests don't accidentally read them from env.
# Live integration tests re-populate from _TAVILY_LIVE_KEY / _SERPAPI_LIVE_KEY
# (set in the parent shell) so they only run when user explicitly opts in.
os.environ.pop("TAVILY_API_KEY", None)
os.environ.pop("SERPAPI_KEY", None)

# Save live keys for integration tests
_LIVE_TAVILY = os.environ.pop("_TAVILY_LIVE_KEY", None)
_LIVE_SERPAPI = os.environ.pop("_SERPAPI_LIVE_KEY", None)
if _LIVE_TAVILY:
    os.environ["TAVILY_API_KEY"] = _LIVE_TAVILY
if _LIVE_SERPAPI:
    os.environ["SERPAPI_KEY"] = _LIVE_SERPAPI


from llm.news_search import (  # noqa: E402
    search_news,
    _tavily,
    _serpapi,
    _mock,
    _resolve_provider,
    _normalize_tavily,
    _normalize_serpapi,
    NEWS_SCHEMA_KEYS,
    get_news_config,
)


# ---------------------------------------------------------------------------
# Config & provider resolution
# ---------------------------------------------------------------------------


def test_get_news_config_defaults(monkeypatch):
    """默认配置：所有 key 应当为空。"""
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    cfg = get_news_config()
    assert cfg["tavily_key"] == ""
    assert cfg["serpapi_key"] == ""
    assert int(cfg["timeout_s"]) >= 1
    assert int(cfg["max_results"]) >= 1


def test_get_news_config_reads_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setenv("SERPAPI_KEY", "serp-test")
    monkeypatch.setenv("NEWS_TIMEOUT_S", "7")
    monkeypatch.setenv("NEWS_MAX_RESULTS", "4")
    cfg = get_news_config()
    assert cfg["tavily_key"] == "tvly-test"
    assert cfg["serpapi_key"] == "serp-test"
    assert cfg["timeout_s"] == "7"
    assert cfg["max_results"] == "4"


def test_resolve_provider_prefers_explicit():
    cfg = {"tavily_key": "x", "serpapi_key": "y"}
    assert _resolve_provider(provider="tavily", cfg=cfg) == "tavily"
    assert _resolve_provider(provider="serpapi", cfg=cfg) == "serpapi"


def test_resolve_provider_falls_back_to_keys():
    cfg = {"tavily_key": "x", "serpapi_key": ""}
    assert _resolve_provider(provider=None, cfg=cfg) == "tavily"
    cfg = {"tavily_key": "", "serpapi_key": "y"}
    assert _resolve_provider(provider=None, cfg=cfg) == "serpapi"


def test_resolve_provider_falls_back_to_mock():
    cfg = {"tavily_key": "", "serpapi_key": ""}
    assert _resolve_provider(provider=None, cfg=cfg) == "mock"


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------


def test_normalize_tavily_handles_full_response():
    raw = {
        "results": [
            {
                "title": "Fed holds rates steady",
                "url": "https://reuters.com/fed-rates-2026",
                "content": "The Federal Reserve held...",
                "published_date": "2026-06-04T14:00:00Z",
            },
            {
                "title": "CPI rises 3.1% YoY",
                "url": "https://wsj.com/cpi",
                "content": "Consumer prices climbed...",
                "published_date": "2026-06-03T13:30:00Z",
            },
        ]
    }
    out = _normalize_tavily(raw, query="Fed")
    assert len(out) == 2
    for item in out:
        for k in NEWS_SCHEMA_KEYS:
            assert k in item, f"missing key {k} in {item}"
    assert out[0]["headline"] == "Fed holds rates steady"
    assert out[0]["source"] == "tavily"
    assert out[0]["url"] == "https://reuters.com/fed-rates-2026"
    assert out[0]["relevance"] > 0
    assert out[0]["_query"] == "Fed"
    # 标题里包含 "Fed" 关键词 → 类别归为 fed
    assert out[0]["category"] in {"fed", "macro", "cpi", "rates"}


def test_normalize_tavily_handles_missing_optional_fields():
    raw = {"results": [{"title": "X", "url": "https://y.com"}]}
    out = _normalize_tavily(raw, query="x")
    assert len(out) == 1
    assert out[0]["headline"] == "X"
    assert out[0]["ts"] is None


def test_normalize_tavily_empty():
    out = _normalize_tavily({"results": []}, query="x")
    assert out == []


def test_normalize_tavily_handles_no_results_key():
    out = _normalize_tavily({}, query="x")
    assert out == []


def test_normalize_serpapi_handles_full_response():
    raw = {
        "news_results": [
            {
                "title": "ECB signals easing",
                "link": "https://ft.com/ecb-easing",
                "snippet": "European Central Bank...",
                "date": "3 hours ago",
            }
        ]
    }
    out = _normalize_serpapi(raw, query="ECB")
    assert len(out) == 1
    for k in NEWS_SCHEMA_KEYS:
        assert k in out[0]
    assert out[0]["headline"] == "ECB signals easing"
    assert out[0]["source"] == "serpapi"
    assert out[0]["url"] == "https://ft.com/ecb-easing"
    assert "ECB" in out[0]["_query"]


def test_normalize_serpapi_handles_empty():
    assert _normalize_serpapi({"news_results": []}, "x") == []
    assert _normalize_serpapi({}, "x") == []


# ---------------------------------------------------------------------------
# search_news dispatcher
# ---------------------------------------------------------------------------


def test_search_news_uses_explicit_provider():
    """显式 provider 优先 — 即使 tavily_key 为空也应能跑（缺 key 时 fail-soft）"""
    with patch("llm.news_search._tavily", return_value=[{"headline": "fake", "source": "tavily"}]) as m:
        out = search_news("Fed", provider="tavily", cfg={"tavily_key": "ignored", "serpapi_key": "", "timeout_s": "5", "max_results": "3"})
    assert len(out) == 1
    assert m.called


def test_search_news_falls_back_to_serpapi_on_tavily_failure():
    """tavily 调用失败时自动 fallback 到 serpapi。"""
    cfg = {"tavily_key": "x", "serpapi_key": "y", "timeout_s": "5", "max_results": "3"}
    with patch("llm.news_search._tavily", side_effect=RuntimeError("boom")):
        with patch("llm.news_search._serpapi", return_value=[{"headline": "s", "source": "serpapi"}]) as m:
            out = search_news("Fed", provider="tavily", cfg=cfg)
    assert len(out) == 1
    assert out[0]["source"] == "serpapi"
    assert m.called


def test_search_news_falls_back_to_mock_on_all_failure():
    cfg = {"tavily_key": "x", "serpapi_key": "y", "timeout_s": "5", "max_results": "3"}
    with patch("llm.news_search._tavily", side_effect=RuntimeError("a")):
        with patch("llm.news_search._serpapi", side_effect=RuntimeError("b")):
            with patch("llm.news_search._mock", return_value=[{"headline": "m", "source": "mock_offline"}]) as m:
                out = search_news("Fed", provider="tavily", cfg=cfg)
    assert len(out) == 1
    assert out[0]["source"] == "mock_offline"
    assert m.called


def test_search_news_uses_mock_when_no_keys():
    cfg = {"tavily_key": "", "serpapi_key": "", "timeout_s": "5", "max_results": "3"}
    with patch("llm.news_search._mock", return_value=[{"headline": "m", "source": "mock_offline"}]) as m:
        out = search_news("anything", cfg=cfg)
    assert out[0]["source"] == "mock_offline"
    assert m.called


def test_search_news_clamps_max_results():
    """max_results 超过 10 应被夹到 10。"""
    cfg = {"tavily_key": "", "serpapi_key": "", "timeout_s": "5", "max_results": "999"}
    captured = {}
    def fake_mock(query, max_results):
        captured["max_results"] = max_results
        return []
    with patch("llm.news_search._mock", side_effect=fake_mock):
        search_news("x", cfg=cfg)
    # 内部 max_results 参数应当 ≤ 10
    assert captured["max_results"] <= 10


# ---------------------------------------------------------------------------
# _tavily HTTP call (mocked urlopen)
# ---------------------------------------------------------------------------


def test_tavily_makes_post_request():
    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps({
        "results": [{"title": "X", "url": "https://x.com", "published_date": "2026-06-04"}]
    }).encode("utf-8")
    fake_response.__enter__ = MagicMock(return_value=fake_response)
    fake_response.__exit__ = MagicMock(return_value=False)
    # 必须在使用 urlopen 的模块上 patch
    with patch("urllib.request.urlopen", return_value=fake_response) as m:
        out = _tavily("query", "tvly-key", max_results=3, timeout_s=5)
    assert len(out) == 1
    # verify it was called with a Request object
    call_args = m.call_args
    req = call_args[0][0]
    assert req.get_method() == "POST"
    body = json.loads(req.data.decode("utf-8"))
    assert body["api_key"] == "tvly-key"
    assert body["query"] == "query"
    assert body["max_results"] == 3
    # Tavily 有效值: ultra-fast/fast/basic/advanced；"news" 是无效值（实测 422）
    assert body["search_depth"] in {"ultra-fast", "fast", "basic", "advanced"}


def test_tavily_returns_empty_on_http_error():
    """HTTP 4xx/5xx → 返回空列表，不抛。"""
    with patch("urllib.request.urlopen", side_effect=RuntimeError("HTTP 429")):
        out = _tavily("q", "k", max_results=5, timeout_s=5)
    assert out == []


def test_tavily_returns_empty_on_bad_json():
    fake_response = MagicMock()
    fake_response.read.return_value = b"not json"
    fake_response.__enter__ = MagicMock(return_value=fake_response)
    fake_response.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=fake_response):
        out = _tavily("q", "k", max_results=5, timeout_s=5)
    assert out == []


# ---------------------------------------------------------------------------
# _serpapi HTTP call (mocked urlopen)
# ---------------------------------------------------------------------------


def test_serpapi_makes_get_request():
    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps({
        "news_results": [{"title": "X", "link": "https://x.com", "date": "today"}]
    }).encode("utf-8")
    fake_response.__enter__ = MagicMock(return_value=fake_response)
    fake_response.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=fake_response) as m:
        out = _serpapi("query", "serp-key", max_results=3, timeout_s=5)
    assert len(out) == 1
    req = m.call_args[0][0]
    assert req.get_method() == "GET"
    assert "api_key=serp-key" in req.full_url
    assert "q=query" in req.full_url or "q=" in req.full_url


def test_serpapi_returns_empty_on_http_error():
    with patch("urllib.request.urlopen", side_effect=RuntimeError("HTTP 500")):
        out = _serpapi("q", "k", max_results=5, timeout_s=5)
    assert out == []


# ---------------------------------------------------------------------------
# _mock keeps existing behavior
# ---------------------------------------------------------------------------


def test_mock_returns_at_most_5_items():
    out = _mock("anything", max_results=10)
    assert 0 < len(out) <= 5
    for item in out:
        assert item["source"] == "mock_offline"


def test_mock_query_sanitization():
    out = _mock("A" * 500 + "\x00{}", max_results=5)
    assert all(item.get("source") == "mock_offline" for item in out)
    # 不应崩


# ---------------------------------------------------------------------------
# tools.py integration
# ---------------------------------------------------------------------------


def test_search_macro_news_uses_news_search_module():
    """tools.search_macro_news 应该委托给 news_search.search_news。"""
    from llm.tools import search_macro_news
    with patch("llm.news_search.search_news", return_value=[{"headline": "x", "source": "mock_offline"}]) as m:
        out = search_macro_news("Fed", ctx={})
    assert m.called
    assert out[0]["source"] == "mock_offline"


# ---------------------------------------------------------------------------
# Integration tests (skipped if real API keys not set)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.getenv("TAVILY_API_KEY"),
    reason="TAVILY_API_KEY not set — skipping live integration test",
)
def test_tavily_live_integration():
    """真实 Tavily key 应当返回 5 条真新闻，source='tavily'。"""
    from llm.news_search import search_news
    out = search_news("Federal Reserve rate decision 2026")
    assert len(out) > 0
    assert out[0]["source"] == "tavily"
    assert out[0]["url"] is not None
    # headline 必须非空
    assert out[0]["headline"]
    # published_date 可能是 None（Tavily basic depth 不返回日期），不强求
    # 但 url 必须有
    assert out[0]["url"].startswith("http")


@pytest.mark.skipif(
    not os.getenv("SERPAPI_KEY"),
    reason="SERPAPI_KEY not set — skipping live integration test",
)
def test_serpapi_live_integration():
    """真实 SerpAPI key 应当返回真新闻，source='serpapi'。"""
    from llm.news_search import search_news
    # 显式强制用 serpapi
    out = search_news("Federal Reserve", provider="serpapi")
    assert len(out) > 0
    assert out[0]["source"] == "serpapi"
    assert out[0]["url"] is not None
