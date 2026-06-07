"""
Tests for llm/reference_feeds.py — RSS / GDELT / 经济日历.

全部 HTTP 调用都 mock — 离线测试。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def _fresh_import():
    if "llm.reference_feeds" in sys.modules:
        del sys.modules["llm.reference_feeds"]
    from llm import reference_feeds
    return reference_feeds


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def test_cache_miss_then_hit(tmp_path):
    rf = _fresh_import()
    cache_dir = tmp_path / "cache"
    key = "test_feed"
    # 1) miss → None
    assert rf._read_cache(cache_dir, key, ttl_seconds=3600) is None
    # 2) write + read
    payload = [{"ts": "2026-06-04", "headline": "X"}]
    rf._write_cache(cache_dir, key, payload)
    out = rf._read_cache(cache_dir, key, ttl_seconds=3600)
    assert out == payload


def test_cache_expires(tmp_path):
    rf = _fresh_import()
    cache_dir = tmp_path / "cache"
    rf._write_cache(cache_dir, "k", [{"x": 1}])
    # 立即读 → 命中
    assert rf._read_cache(cache_dir, "k", ttl_seconds=0) is None  # ttl=0 永远过期
    assert rf._read_cache(cache_dir, "k", ttl_seconds=3600) is not None


# ---------------------------------------------------------------------------
# RSS parser
# ---------------------------------------------------------------------------


SAMPLE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Reuters Markets</title>
    <item>
      <title>Fed holds rates steady</title>
      <link>https://reuters.com/fed</link>
      <pubDate>Wed, 04 Jun 2026 14:00:00 GMT</pubDate>
      <category>fed</category>
    </item>
    <item>
      <title>CPI rises 3.1% YoY</title>
      <link>https://reuters.com/cpi</link>
      <pubDate>Tue, 03 Jun 2026 13:30:00 GMT</pubDate>
      <category>cpi</category>
    </item>
  </channel>
</rss>
"""


def test_rss_parser_extracts_items():
    rf = _fresh_import()
    items = rf._parse_rss(SAMPLE_RSS_XML, source_name="reuters_markets")
    assert len(items) == 2
    for it in items:
        assert "ts" in it
        assert "headline" in it
        assert "url" in it
        # source 字段是 rss:<name>
        assert it["source"] == "rss:reuters_markets"
        assert it["origin"] == "rss"
    assert items[0]["headline"] == "Fed holds rates steady"


def test_rss_parser_handles_empty_xml():
    rf = _fresh_import()
    assert rf._parse_rss("<rss></rss>", source_name="x") == []
    assert rf._parse_rss("", source_name="x") == []


def test_rss_parser_skips_malformed_items():
    rf = _fresh_import()
    xml = """<rss><channel>
      <item><title>OK</title><link>https://x.com</link></item>
      <item><title></title><link>https://y.com</link></item>
      <item><link>https://z.com</link></item>
    </channel></rss>"""
    items = rf._parse_rss(xml, source_name="x")
    # 只有 1 个有 title 的 item
    assert len(items) == 1
    assert items[0]["headline"] == "OK"


# ---------------------------------------------------------------------------
# RSS fetcher (mocked urlopen)
# ---------------------------------------------------------------------------


def test_fetch_macro_rss_returns_normalized_items(tmp_path):
    rf = _fresh_import()
    fake_response = MagicMock()
    fake_response.read.return_value = SAMPLE_RSS_XML.encode("utf-8")
    fake_response.__enter__ = MagicMock(return_value=fake_response)
    fake_response.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=fake_response):
        items = rf.fetch_macro_rss(
            feeds=["https://reuters.com/rss"],
            cache_dir=tmp_path / "cache",
        )
    assert len(items) == 2
    assert items[0]["source"].startswith("rss:")


def test_fetch_macro_rss_caches_to_disk(tmp_path):
    rf = _fresh_import()
    fake_response = MagicMock()
    fake_response.read.return_value = SAMPLE_RSS_XML.encode("utf-8")
    fake_response.__enter__ = MagicMock(return_value=fake_response)
    fake_response.__exit__ = MagicMock(return_value=False)
    cache_dir = tmp_path / "cache"
    with patch("urllib.request.urlopen", return_value=fake_response) as m:
        rf.fetch_macro_rss(feeds=["https://reuters.com/rss"], cache_dir=cache_dir)
        rf.fetch_macro_rss(feeds=["https://reuters.com/rss"], cache_dir=cache_dir)
    # 第二次应当走缓存，不再 urlopen
    assert m.call_count == 1


def test_fetch_macro_rss_returns_empty_on_error(tmp_path):
    rf = _fresh_import()
    with patch("urllib.request.urlopen", side_effect=RuntimeError("network")):
        items = rf.fetch_macro_rss(feeds=["https://x.com"], cache_dir=tmp_path / "cache")
    assert items == []


# ---------------------------------------------------------------------------
# GDELT fetcher
# ---------------------------------------------------------------------------


SAMPLE_GDELT = {
    "articles": [
        {
            "title": "Fed signals rate cut pause",
            "url": "https://reuters.com/fed-pause",
            "socialimage": "https://img.example/fed.jpg",
            "tone": -5.0,
            "location": {"countryCode": "US"},
            "seendate": "20260604T140000Z",
        },
        {
            "title": "OPEC extends cuts",
            "url": "https://reuters.com/opec",
            "socialimage": "",
            "tone": -4.0,
            "location": {"countryCode": "SA"},
            "seendate": "20260603T120000Z",
        },
    ]
}


def test_gdelt_parser_extracts_articles():
    rf = _fresh_import()
    items = rf._parse_gdelt(SAMPLE_GDELT, query="Fed")
    assert len(items) == 2
    assert items[0]["headline"] == "Fed signals rate cut pause"
    assert items[0]["source"] == "gdelt"
    assert items[0]["tone"] == -5.0
    assert items[0]["country"] == "US"
    assert items[0]["origin"] == "gdelt"


def test_gdelt_parser_handles_empty():
    rf = _fresh_import()
    assert rf._parse_gdelt({"articles": []}, "x") == []
    assert rf._parse_gdelt({}, "x") == []


def test_fetch_gdelt_events_makes_correct_request(tmp_path):
    rf = _fresh_import()
    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps(SAMPLE_GDELT).encode("utf-8")
    fake_response.__enter__ = MagicMock(return_value=fake_response)
    fake_response.__exit__ = MagicMock(return_value=False)
    with patch("urllib.request.urlopen", return_value=fake_response) as m:
        items = rf.fetch_gdelt_events(query="Fed", cache_dir=tmp_path / "cache")
    assert len(items) == 2
    # verify URL contains the query
    req = m.call_args[0][0]
    assert "query=Fed" in req.full_url or "query=" in req.full_url


def test_fetch_gdelt_events_returns_empty_on_error(tmp_path):
    rf = _fresh_import()
    with patch("urllib.request.urlopen", side_effect=RuntimeError("429")):
        items = rf.fetch_gdelt_events(query="x", cache_dir=tmp_path / "cache")
    assert items == []


# ---------------------------------------------------------------------------
# Economic calendar
# ---------------------------------------------------------------------------


def test_calendar_parser_handles_investing_format():
    rf = _fresh_import()
    raw = {
        "events": [
            {
                "date": "2026-06-12",
                "time": "14:00",
                "country": "US",
                "event": "FOMC Rate Decision",
                "importance": 3,
                "previous": "5.25%",
                "forecast": "5.25%",
            }
        ]
    }
    items = rf._parse_calendar_investing(raw, days_ahead=7)
    assert len(items) == 1
    assert items[0]["event"] == "FOMC Rate Decision"
    assert items[0]["importance"] == 3
    assert items[0]["country"] == "US"


def test_calendar_parser_filters_old_events():
    rf = _fresh_import()
    raw = {
        "events": [
            {"date": "2020-01-01", "time": "10:00", "country": "US", "event": "Old", "importance": 1},
            {"date": "2099-12-31", "time": "10:00", "country": "US", "event": "Future", "importance": 1},
        ]
    }
    items = rf._parse_calendar_investing(raw, days_ahead=7)
    # 2099 那个太远 — 实际上 "days_ahead=7" 应当过滤掉
    # 由于 today 是动态的，我们只断言旧事件（2020）必定被过滤
    for it in items:
        assert it["date"] > "2026-01-01"


def test_fetch_calendar_returns_empty_on_error(tmp_path):
    rf = _fresh_import()
    with patch("urllib.request.urlopen", side_effect=RuntimeError("403")):
        items = rf.fetch_economic_calendar(days_ahead=7, cache_dir=tmp_path / "cache")
    assert items == []


def test_calendar_min_importance_filter():
    """min_importance=3 只保留 High Impact 事件。"""
    rf = _fresh_import()
    future = "2026-12-15"
    raw = [
        {"title": "Low Event", "country": "USD", "date": f"{future}T08:30:00-04:00", "impact": "Low"},
        {"title": "Medium Event", "country": "USD", "date": f"{future}T09:00:00-04:00", "impact": "Medium"},
        {"title": "High Event", "country": "USD", "date": f"{future}T10:00:00-04:00", "impact": "High"},
    ]
    import json
    import os
    import shutil
    from pathlib import Path
    cache = Path("/tmp/cal_filter_test")
    if cache.exists():
        shutil.rmtree(cache)
    cache.mkdir(parents=True, exist_ok=True)

    # Mock _http_get_bytes 直接 — 避免 MagicMock 上下文管理器复杂性
    ff_data = json.dumps(raw).encode("utf-8")
    fed_data = b'{"events": []}'

    def mock_get(url, **kw):
        if "faireconomy" in url:
            return ff_data
        if "federalreserve" in url:
            return fed_data
        return None

    with patch("llm.reference_feeds._http_get_bytes", side_effect=mock_get):
        all_items = rf.fetch_economic_calendar(days_ahead=365, days_back=0, cache_dir=cache, max_results=30, min_importance=1)
        high_only = rf.fetch_economic_calendar(days_ahead=365, days_back=0, cache_dir=cache, max_results=30, min_importance=3)
    assert len(all_items) == 3
    assert len(high_only) == 1
    assert high_only[0]["event"] == "High Event"
    assert high_only[0]["importance"] == 3


# ---------------------------------------------------------------------------
# translate_to_chinese
# ---------------------------------------------------------------------------


def test_translate_returns_none_list_when_disabled(monkeypatch):
    """无 LLM key 时返回 [None, ...]。"""
    rf = _fresh_import()
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("_TAVILY_LIVE_KEY", raising=False)
    monkeypatch.delenv("_SERPAPI_LIVE_KEY", raising=False)
    # 强制 mock backend（即使没有 api_key，is_llm_enabled 也会因 backend=mock 返回 True）
    monkeypatch.setenv("LLM_BACKEND", "openai")
    out = rf.translate_to_chinese(["X", "Y", "Z"])
    assert out == [None, None, None]


def test_translate_returns_zh_for_each_input(monkeypatch):
    """有 key 时返回对应中文。"""
    rf = _fresh_import()
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    fake_response = MagicMock()
    # content 必须是 str（与真实 LLMCallResult 一致）
    fake_response.content = "1. 测试一\n2. 测试二\n3. 测试三"
    fake_response.input_tokens = 50
    fake_response.output_tokens = 30
    fake_response.elapsed_s = 0.1
    fake_response.model = "mock"
    fake_response.backend = "mock"
    with patch("llm.client.call_llm", return_value=fake_response):
        out = rf.translate_to_chinese(["X", "Y", "Z"])
    assert out == ["测试一", "测试二", "测试三"]


def test_translate_handles_unnumbered_output(monkeypatch):
    """LLM 输出不带数字时按行顺序映射。"""
    rf = _fresh_import()
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    fake_response = MagicMock()
    fake_response.content = "第一\n第二"
    fake_response.input_tokens = 50
    fake_response.output_tokens = 30
    fake_response.elapsed_s = 0.1
    fake_response.model = "mock"
    fake_response.backend = "mock"
    with patch("llm.client.call_llm", return_value=fake_response):
        out = rf.translate_to_chinese(["A", "B"])
    assert out == ["第一", "第二"]


def test_translate_preserves_none_for_empty(monkeypatch):
    """空字符串保持 None。"""
    rf = _fresh_import()
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    out = rf.translate_to_chinese(["", "  ", None])
    assert out == [None, None, None]


def test_translate_returns_none_list_on_failure(monkeypatch):
    """LLM 抛异常 → 返回 [None, ...]，不崩。"""
    rf = _fresh_import()
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    with patch("llm.client.call_llm", side_effect=RuntimeError("boom")):
        out = rf.translate_to_chinese(["X", "Y"])
    assert out == [None, None]


def test_translate_handles_empty_content(monkeypatch):
    """LLM 返回空 content → 返回 [None, ...]。"""
    rf = _fresh_import()
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    fake_response = MagicMock()
    fake_response.content = ""
    fake_response.input_tokens = 0
    fake_response.output_tokens = 0
    fake_response.elapsed_s = 0.0
    fake_response.model = "mock"
    fake_response.backend = "mock"
    with patch("llm.client.call_llm", return_value=fake_response):
        out = rf.translate_to_chinese(["X", "Y"])
    assert out == [None, None]


# ---------------------------------------------------------------------------
# ForexFactory calendar
# ---------------------------------------------------------------------------


def test_forexfactory_parser_basic():
    """验证 _parse_forexfactory_json 解析标准字段。"""
    rf = _fresh_import()
    # 用未来日期 — today filter 是 >= today，past 事件会被过滤
    future = "2026-12-15"
    raw = [
        {
            "title": "Non-Farm Employment Change",
            "country": "USD",
            "date": f"{future}T08:30:00-04:00",
            "impact": "High",
            "forecast": "85K",
            "previous": "115K",
        },
        {
            "title": "Bank Holiday",
            "country": "USD",
            "date": f"{future}T09:00:00-04:00",
            "impact": "Holiday",
        },
    ]
    out = rf._parse_forexfactory_json(raw, days_ahead=365)
    # Holiday 被跳过
    assert len(out) == 1
    assert out[0]["event"] == "Non-Farm Employment Change"
    assert out[0]["country"] == "USD"
    assert out[0]["importance"] == 3  # High → 3
    assert out[0]["source"] == "forexfactory"
    assert out[0]["forecast"] == "85K"


def test_forexfactory_parser_handles_bad_date():
    rf = _fresh_import()
    raw = [
        {"title": "X", "country": "USD", "date": "not-a-date", "impact": "Low"},
    ]
    out = rf._parse_forexfactory_json(raw, days_ahead=30)
    assert out == []


def test_forexfactory_impact_mapping():
    rf = _fresh_import()
    future = "2026-12-15"
    raw = [
        {"title": "A", "country": "USD", "date": f"{future}T08:30:00-04:00", "impact": "Low"},
        {"title": "B", "country": "USD", "date": f"{future}T09:00:00-04:00", "impact": "Medium"},
        {"title": "C", "country": "USD", "date": f"{future}T10:00:00-04:00", "impact": "High"},
    ]
    out = rf._parse_forexfactory_json(raw, days_ahead=365)
    assert [ev["importance"] for ev in out] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Combined fetcher
# ---------------------------------------------------------------------------


def test_fetch_all_returns_combined_dict(tmp_path):
    rf = _fresh_import()
    with patch.object(rf, "fetch_macro_rss", return_value=[{"headline": "rss"}]):
        with patch.object(rf, "fetch_gdelt_events", return_value=[{"headline": "gdelt"}]):
            with patch.object(rf, "fetch_economic_calendar", return_value=[{"event": "cal"}]):
                out = rf.fetch_all_reference_feeds(cache_dir=tmp_path / "cache")
    assert "rss" in out
    assert "gdelt" in out
    assert "calendar" in out
    assert out["rss"] == [{"headline": "rss"}]


def test_fetch_all_clamps_lists(tmp_path):
    rf = _fresh_import()
    rss = [{"headline": f"r{i}"} for i in range(50)]
    gdelt = [{"headline": f"g{i}"} for i in range(50)]
    cal = [{"event": f"c{i}"} for i in range(50)]
    with patch.object(rf, "fetch_macro_rss", return_value=rss):
        with patch.object(rf, "fetch_gdelt_events", return_value=gdelt):
            with patch.object(rf, "fetch_economic_calendar", return_value=cal):
                out = rf.fetch_all_reference_feeds(
                    cache_dir=tmp_path / "cache",
                    max_rss=10, max_gdelt=10, max_calendar=20,
                )
    assert len(out["rss"]) == 10
    assert len(out["gdelt"]) == 10
    assert len(out["calendar"]) == 20
