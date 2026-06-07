"""
===================================
News Search — Tavily + SerpAPI + mock
===================================

Provider 优先级: 显式参数 > TAVILY_API_KEY > SERPAPI_KEY > mock

环境变量：
  - ``TAVILY_API_KEY``    Tavily API key
  - ``SERPAPI_KEY``       SerpAPI API key
  - ``NEWS_TIMEOUT_S``    超时秒数，默认 5
  - ``NEWS_MAX_RESULTS``  单次最大返回数（默认 5，硬上限 10）

设计原则：
  1. 永不抛异常 — 失败返回 ``[]`` + log warning
  2. 快速失败 — 5 秒超时（新闻搜索必须即时）
  3. 自动 fallback — Tavily 失败 → SerpAPI → mock
  4. 不持久化 — 搜索结果只入调用方 context
  5. 标准 schema — 与 ``search_macro_news`` 现有契约兼容

Schema 契约（每条新闻）:
    ts:          str | None   # ISO 8601
    headline:    str          # 标题
    url:         str | None   # 链接
    source:      str          # "tavily" | "serpapi" | "mock_offline"
    relevance:   float        # 0.0-1.0
    category:    str          # fed | cpi | rates | macro | ...
    _query:      str          # 调试用，前 50 字符
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Export urlopen so tests can patch it via ``patch("llm.news_search.urlopen")``
urlopen = urllib.request.urlopen


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

NEWS_SCHEMA_KEYS = {"ts", "headline", "url", "source", "relevance", "category", "_query"}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def get_news_config() -> Dict[str, str]:
    """读取新闻搜索配置。"""
    return {
        "tavily_key": os.getenv("TAVILY_API_KEY", "").strip(),
        "serpapi_key": os.getenv("SERPAPI_KEY", "").strip(),
        "timeout_s": os.getenv("NEWS_TIMEOUT_S", "5").strip(),
        "max_results": os.getenv("NEWS_MAX_RESULTS", "5").strip(),
    }


def _resolve_provider(provider: Optional[str], cfg: Dict[str, str]) -> str:
    """显式 > tavily_key > serpapi_key > mock。"""
    if provider in {"tavily", "serpapi", "mock"}:
        return provider
    if cfg.get("tavily_key"):
        return "tavily"
    if cfg.get("serpapi_key"):
        return "serpapi"
    return "mock"


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------


_KEYWORD_CATEGORIES = {
    "fed": ["fed", "fomc", "powell", "rate decision", "rate hold", "rate cut", "rate hike"],
    "cpi": ["cpi", "inflation", "core inflation", "pce"],
    "rates": ["treasury", "yield", "10y", "2y", "bond", "curve"],
    "macro": ["ism", "pmi", "gdp", "payroll", "nfp", "unemployment", "jobs"],
    "geopolitics": ["war", "tension", "china", "russia", "iran", "middle east", "tariff"],
    "global": ["ecb", "boj", "europe", "asia", "japan", "uk", "eurozone"],
    "oil": ["oil", "crude", "opec", "barrel", "wti", "brent"],
    "equity": ["spy", "qqq", "s&p", "nasdaq", "stock", "earnings"],
}


def _categorize(headline: str) -> str:
    """从 headline 推断类别。找不到返回 'general'。"""
    h = (headline or "").lower()
    for cat, kws in _KEYWORD_CATEGORIES.items():
        for kw in kws:
            if kw in h:
                return cat
    return "general"


def _normalize_tavily(raw: Dict[str, Any], query: str) -> List[Dict[str, Any]]:
    """把 Tavily response.results 映射到标准 schema。"""
    results = raw.get("results") or []
    if not isinstance(results, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        headline = str(r.get("title") or "").strip()
        if not headline:
            continue
        url = r.get("url")
        ts = r.get("published_date")
        out.append({
            "ts": str(ts) if ts else None,
            "headline": headline,
            "url": str(url) if url else None,
            "source": "tavily",
            "relevance": 0.7,  # Tavily 自带 score 但格式不稳 — 留 0.7 占位
            "category": _categorize(headline),
            "_query": query[:50],
        })
    return out


def _normalize_serpapi(raw: Dict[str, Any], query: str) -> List[Dict[str, Any]]:
    """把 SerpAPI response.news_results 映射到标准 schema。"""
    results = raw.get("news_results") or []
    if not isinstance(results, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        headline = str(r.get("title") or "").strip()
        if not headline:
            continue
        url = r.get("link")
        date = r.get("date")
        out.append({
            "ts": str(date) if date else None,
            "headline": headline,
            "url": str(url) if url else None,
            "source": "serpapi",
            "relevance": 0.65,
            "category": _categorize(headline),
            "_query": query[:50],
        })
    return out


# ---------------------------------------------------------------------------
# HTTP backends
# ---------------------------------------------------------------------------


def _http_post_json(url: str, body: Dict[str, Any], timeout_s: int) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.debug("[news_search] POST %s failed: %s", url, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[news_search] POST %s unexpected: %s", url, exc)
        return None


def _http_get_json(url: str, timeout_s: int) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(url, headers={"accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.debug("[news_search] GET %s failed: %s", url, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[news_search] GET %s unexpected: %s", url, exc)
        return None


def _tavily(query: str, key: str, *, max_results: int, timeout_s: int) -> List[Dict[str, Any]]:
    """Tavily Search API — POST /search.

    字段约定：``search_depth`` 取 ``basic``（默认，最快）。
    Tavily 有效值: ``ultra-fast`` / ``fast`` / ``basic`` / ``advanced``。
    ``news`` 是无效值（实测 422）— 不要用。
    """
    payload = {
        "api_key": key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }
    raw = _http_post_json("https://api.tavily.com/search", payload, timeout_s)
    if raw is None:
        return []
    return _normalize_tavily(raw, query=query)


def _serpapi(query: str, key: str, *, max_results: int, timeout_s: int) -> List[Dict[str, Any]]:
    """SerpAPI Google News — GET /search.json?tbm=nws."""
    from urllib.parse import urlencode
    params = {
        "q": query,
        "tbm": "nws",
        "api_key": key,
        "num": max_results,
        "output": "json",
    }
    url = f"https://serpapi.com/search.json?{urlencode(params)}"
    raw = _http_get_json(url, timeout_s)
    if raw is None:
        return []
    return _normalize_serpapi(raw, query=query)


# ---------------------------------------------------------------------------
# Mock backend (preserves existing _MOCK_NEWS_POOL behavior)
# ---------------------------------------------------------------------------


_MOCK_NEWS_POOL: List[Dict[str, Any]] = [
    {
        "ts": "2026-06-04T14:00:00Z",
        "headline": "Fed holds rates steady, flags sticky services inflation",
        "category": "fed",
        "relevance": 0.92,
    },
    {
        "ts": "2026-06-03T13:30:00Z",
        "headline": "May CPI prints +3.1% YoY, core unchanged",
        "category": "cpi",
        "relevance": 0.88,
    },
    {
        "ts": "2026-06-02T18:00:00Z",
        "headline": "10Y Treasury yield jumps 8bp on supply concerns",
        "category": "rates",
        "relevance": 0.75,
    },
    {
        "ts": "2026-06-01T09:00:00Z",
        "headline": "ISM Manufacturing rebounds to 51.2, first expansion in 4 months",
        "category": "macro",
        "relevance": 0.65,
    },
    {
        "ts": "2026-05-31T20:00:00Z",
        "headline": "OPEC+ extends production cuts through Q3",
        "category": "oil",
        "relevance": 0.55,
    },
    {
        "ts": "2026-05-30T15:00:00Z",
        "headline": "Geopolitical tension lifts crude 2%; safe-haven bid for bonds",
        "category": "geopolitics",
        "relevance": 0.60,
    },
    {
        "ts": "2026-05-29T11:00:00Z",
        "headline": "ECB signals further easing; eurozone PMI below forecast",
        "category": "global",
        "relevance": 0.40,
    },
]


def _mock(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Mock — 不接 API, 返回 7 条假新闻，按 query 关键词打分排序。"""
    # query 清洗
    if not isinstance(query, str):
        query = ""
    q_clean = re.sub(r"[\x00-\x1f\x7f]", "", query)[:200]
    q_lower = q_clean.lower()

    scored: List[tuple] = []
    for item in _MOCK_NEWS_POOL:
        score = item.get("relevance", 0.5)
        if q_lower:
            if any(k in q_lower for k in [item.get("category", ""), item.get("headline", "").lower()]):
                score = min(1.0, score + 0.2)
            for kw_list in _KEYWORD_CATEGORIES.values():
                if any(kw in q_lower for kw in kw_list) and any(
                    kw in item.get("headline", "").lower() for kw in kw_list
                ):
                    score = min(1.0, score + 0.1)
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    # mock 硬上限 5（与原 llm/tools.py 行为一致）
    return [
        {**item, "url": None, "source": "mock_offline", "_query": q_clean[:50]}
        for score, item in scored[: min(5, max(1, max_results))]
    ]


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def search_news(
    query: str,
    *,
    provider: Optional[str] = None,
    max_results: Optional[int] = None,
    cfg: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """主入口：按 provider 调用对应 backend，失败自动 fallback。

    优先级: provider 参数 > env (tavily > serpapi) > mock
    fallback: tavily 失败 → serpapi → mock
    """
    cfg = cfg or get_news_config()
    try:
        n = int(max_results) if max_results is not None else int(cfg.get("max_results") or 5)
    except (TypeError, ValueError):
        n = 5
    n = max(1, min(10, n))  # 硬上限 10

    try:
        timeout = int(cfg.get("timeout_s") or 5)
    except (TypeError, ValueError):
        timeout = 5

    chosen = _resolve_provider(provider, cfg)
    tavily_key = cfg.get("tavily_key") or ""
    serpapi_key = cfg.get("serpapi_key") or ""

    last_err: Optional[str] = None

    # 1) 显式 provider
    if chosen == "tavily" and tavily_key:
        try:
            out = _tavily(query, tavily_key, max_results=n, timeout_s=timeout)
            if out:
                return out
            last_err = "tavily returned empty"
        except Exception as exc:  # noqa: BLE001
            last_err = f"tavily: {type(exc).__name__}: {exc}"
        # fall through to serpapi
        if serpapi_key:
            try:
                out = _serpapi(query, serpapi_key, max_results=n, timeout_s=timeout)
                if out:
                    return out
                last_err = (last_err + " | ") if last_err else ""
                last_err += "serpapi returned empty"
            except Exception as exc:  # noqa: BLE001
                last_err = (last_err + " | ") if last_err else ""
                last_err += f"serpapi: {type(exc).__name__}: {exc}"

    elif chosen == "serpapi" and serpapi_key:
        try:
            out = _serpapi(query, serpapi_key, max_results=n, timeout_s=timeout)
            if out:
                return out
            last_err = "serpapi returned empty"
        except Exception as exc:  # noqa: BLE001
            last_err = f"serpapi: {type(exc).__name__}: {exc}"
        # 兜底到 tavily
        if tavily_key:
            try:
                out = _tavily(query, tavily_key, max_results=n, timeout_s=timeout)
                if out:
                    return out
            except Exception as exc:  # noqa: BLE001
                logger.debug("[news_search] tavily fallback also failed: %s", exc)

    if last_err:
        logger.info("[news_search] all real providers failed, using mock: %s", last_err)
    return _mock(query, max_results=n)
