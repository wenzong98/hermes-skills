"""
===================================
Reference Feeds — RSS / GDELT / 财经日历
===================================

dashboard 旁路的"参考内容"层 — **不进 LLM 工具调用**。

定位差异：
- Tavily / SerpAPI = **LLM 工具调用**（按 query 拉取，实时）
- RSS / GDELT / 日历 = **后台预拉取，元数据缓存，dashboard 展示**

数据源：
- **Macro RSS**: Reuters Markets / WSJ Markets / FT Markets / Bloomberg / MarketWatch
- **GDELT 2.0 doc API**: https://api.gdeltproject.org/api/v2/doc/doc
  公开、无 key、按 tone 过滤
- **财经日历**: 暂以 GDELT 事件流替代（Investing.com 需要爬虫，复杂度高）

关键约束：
- **只缓存 metadata（headline + url + ts）**，不缓存正文（合规 + 版权）
- 全部用 ``urllib.request``，零新依赖
- 失败 → 返回 ``[]`` + log warning（绝不抛到 dashboard）
- 缓存路径：``references/data_cache/macro_feeds/``，TTL 1 小时
- 工具描述标注"参考内容" — 与 ``search_macro_news``（LLM 工具）**明确区分**
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# .env 加载 (从 ~/.hermes/.env 或项目级 .env)
# ---------------------------------------------------------------------------
# 之所以需要: hermes 把 LLM_API_KEY / MINIMAX_API_KEY 等放在 ~/.hermes/.env
# 启动 cron 时不会自动 source,这里手动 parse 注入到 os.environ。
# 不依赖 python-dotenv, 纯 stdlib。

def _load_env_file(path: Path) -> None:
    """解析 .env 文件, 把 KEY=VALUE 注入到 os.environ。

    优先级: process env > .env (后面覆盖前面)。
    如果 process env 已有该 key (比如用户 export 的),则保留。
    .env 文件内部,后写行覆盖先写行(修复 .env 重复 key 时的优先级 bug)。
    """
    if not path.exists():
        return
    try:
        # 先扫一遍: 如果 process env 已有,记录下来
        process_keys = set(os.environ.keys())
        # 第一次扫:只填 process env 没有的 key
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            if key not in process_keys:
                os.environ[key] = value
    except Exception as exc:  # noqa: BLE001
        logger.debug("[reference_feeds] .env load from %s failed: %s", path, exc)


def _bootstrap_env() -> None:
    """依次加载 ~/.hermes/.env → ~/.claude/.env → 项目 .env,
    只在 key 未设置时填入。
    """
    candidates = [
        Path.home() / ".hermes" / ".env",
        Path.home() / ".claude" / ".env",
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    for p in candidates:
        _load_env_file(p)

    # MiniMax 兼容处理: 如果用户配了 MINIMAX_API_KEY 但没配 LLM_API_KEY,
    # 自动桥接 (让 call_llm 直接走 anthropic 协议打 minimaxi)
    if os.environ.get("MINIMAX_API_KEY") and not os.environ.get("LLM_API_KEY"):
        os.environ["LLM_API_KEY"] = os.environ["MINIMAX_API_KEY"]
    if os.environ.get("MINIMAX_BASE_URL") and not os.environ.get("LLM_BASE_URL"):
        os.environ["LLM_BASE_URL"] = os.environ["MINIMAX_BASE_URL"]
    if os.environ.get("MINIMAX_MODEL") and not os.environ.get("LLM_MODEL"):
        os.environ["LLM_MODEL"] = os.environ["MINIMAX_MODEL"]


_bootstrap_env()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_RSS_FEEDS: List[str] = [
    # Reuters Business News 在本机 / 代理 HTTPS 隧道下 SSL_ERROR_SYSCALL, 已实测无法访问
    # "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.bloomberg.com/markets/news.rss",
]

DEFAULT_GDELT_QUERY = "Federal Reserve OR inflation OR Treasury yields"  # 短查询触发限流少

REFERENCE_FEED_CACHE_DIR = Path("references/data_cache/macro_feeds")
DEFAULT_TTL_SECONDS = 6 * 3600  # 6h — macro feeds 不需要实时，限流友好

# GDELT rate-limit 5s/req。保留 24h cache 以减少 hit。
GDELT_TTL_SECONDS = 24 * 3600

# 检测 GDELT 限流响应 — API 返回纯文本 "Please limit requests..."
GDELT_RATE_LIMIT_MARKER = "Please limit requests"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _read_cache(cache_dir: Path, key: str, *, ttl_seconds: int) -> Optional[Any]:
    """读 JSON 缓存。过期 / 不存在 / 损坏 → 返回 None。"""
    path = cache_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or "_cached_at" not in data:
        return None
    age = time.time() - float(data["_cached_at"])
    if age > ttl_seconds:
        return None
    return data.get("payload")


def _write_cache(cache_dir: Path, key: str, payload: Any) -> None:
    """写 JSON 缓存（带 _cached_at 时间戳）。失败静默。"""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        data = {"_cached_at": time.time(), "payload": payload}
        (cache_dir / f"{key}.json").write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.debug("[reference_feeds] cache write failed: %s", exc)


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _http_get_bytes(url: str, *, timeout_s: int = 10, user_agent: str = "us-etf-quant-system/1.0 (research; non-commercial)") -> Optional[bytes]:
    """GET 一个 URL，返回 bytes。失败返回 None。

    实现: 用 curl subprocess 代替 Python urllib, 解决代理兼容性:
      - Python urllib 用 HTTP/1.0 CONNECT 发 HTTPS 隧道, 部分 CDN (Reuters)
        拒绝 HTTP/1.0 隧道请求导致 SSL 握手失败
      - curl 默认 HTTP/1.1 CONNECT, 兼容所有 HTTPS 代理
      - 代理配置自动从 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY 读

    缓存 6h(由调用方控制,这里只是裸 GET)。
    """
    # 防御性: 万一 _bootstrap_env 没跑过 (例如 subprocess 入口), 重新跑
    if "HTTPS_PROXY" not in os.environ and "HTTP_PROXY" not in os.environ:
        try:
            _bootstrap_env()
        except Exception:
            pass

    # 找 curl 路径
    curl_bin = "/usr/bin/curl"
    if not os.path.exists(curl_bin):
        for candidate in ("/opt/homebrew/bin/curl", "/usr/local/bin/curl"):
            if os.path.exists(candidate):
                curl_bin = candidate
                break
        else:
            # fallback 到 Python urllib
            return _http_get_bytes_urllib(url, timeout_s=timeout_s, user_agent=user_agent)

    cmd = [curl_bin, "-sS", "-L", "--max-time", str(timeout_s), "-A", user_agent, url]
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("ALL_PROXY")
        or ""
    )
    if proxy:
        # 把 -x 插在 -sS 后
        cmd[1:1] = ["-x", proxy]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout_s + 10, check=False,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
        logger.debug("[reference_feeds] curl %s failed rc=%d stderr=%s", url, result.returncode, result.stderr[:200])
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.debug("[reference_feeds] curl exec failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[reference_feeds] curl unexpected: %s", exc)
        return None


def _http_get_bytes_urllib(url: str, *, timeout_s: int, user_agent: str) -> Optional[bytes]:
    """urllib fallback (Python 3.9 协议栈对部分代理兼容差)."""
    try:
        req = urllib.request.Request(url, headers={"user-agent": user_agent})
        proxy_handler = urllib.request.ProxyHandler({
            "http": os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY") or "",
            "https": os.environ.get("HTTPS_PROXY") or os.environ.get("ALL_PROXY") or "",
        })
        if not proxy_handler.proxies:
            proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        with opener.open(req, timeout=timeout_s) as resp:
            return resp.read()
    except Exception as exc:
        logger.debug("[reference_feeds] urllib fallback failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# RSS parser
# ---------------------------------------------------------------------------


def _parse_rss(xml_text: str, *, source_name: str) -> List[Dict[str, Any]]:
    """解析 RSS 2.0 XML。返回标准化 item 列表。"""
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.debug("[reference_feeds] RSS XML parse error: %s", exc)
        return []

    items: List[Dict[str, Any]] = []
    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        pub_el = item.find("pubDate")
        cat_el = item.find("category")

        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            continue
        link = (link_el.text or "").strip() if link_el is not None else ""
        pub = (pub_el.text or "").strip() if pub_el is not None else ""
        cat = (cat_el.text or "").strip() if cat_el is not None else ""

        items.append({
            "ts": pub or None,
            "headline": title,
            "url": link or None,
            "category": cat or "general",
            "source": f"rss:{source_name}",
            "origin": "rss",
        })
    return items


# ---------------------------------------------------------------------------
# RSS fetcher
# ---------------------------------------------------------------------------


def fetch_macro_rss(
    feeds: Optional[List[str]] = None,
    *,
    cache_dir: Union[Path, str, None] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_results: int = 30,
) -> List[Dict[str, Any]]:
    """拉取多个 RSS feed，合并去重，返回标准化列表。"""
    feeds = feeds or DEFAULT_RSS_FEEDS
    cache_dir = Path(cache_dir) if cache_dir else REFERENCE_FEED_CACHE_DIR

    all_items: List[Dict[str, Any]] = []
    seen_headlines: set = set()

    for feed_url in feeds:
        # 缓存 key 用 URL hash 的简写
        key = "rss_" + re.sub(r"[^a-zA-Z0-9]", "_", feed_url)[:60]

        items: Optional[List[Dict[str, Any]]] = None
        cached = _read_cache(cache_dir, key, ttl_seconds=ttl_seconds)
        if cached is not None:
            items = cached
        else:
            raw = _http_get_bytes(feed_url, timeout_s=10)
            if raw is not None:
                source_name = feed_url.split("//")[-1].split("/")[0] or "rss"
                items = _parse_rss(raw.decode("utf-8", errors="replace"), source_name=source_name)
                _write_cache(cache_dir, key, items)
            else:
                items = []

        for it in items:
            h = it.get("headline", "")
            if h and h not in seen_headlines:
                seen_headlines.add(h)
                all_items.append(it)

    # 按 ts 降序（None 排最后）
    all_items.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return all_items[:max_results]


# ---------------------------------------------------------------------------
# GDELT parser
# ---------------------------------------------------------------------------


def _parse_gdelt(raw: Dict[str, Any], query: str = "", *, _query: Optional[str] = None) -> List[Dict[str, Any]]:
    """解析 GDELT 2.0 doc API 响应。"""
    articles = raw.get("articles") or []
    if not isinstance(articles, list):
        return []
    q = _query if _query is not None else query
    out: List[Dict[str, Any]] = []
    for a in articles:
        if not isinstance(a, dict):
            continue
        title = str(a.get("title") or "").strip()
        if not title:
            continue
        url = a.get("url")
        tone = a.get("tone")
        # tone 是浮点数，>0 正面，<0 负面；to dict 可能是单值或对象
        try:
            tone_val = float(tone) if tone is not None and not isinstance(tone, dict) else None
        except (TypeError, ValueError):
            tone_val = None
        loc = a.get("location") or {}
        country = loc.get("countryCode") if isinstance(loc, dict) else None
        seen = a.get("seendate")
        out.append({
            "ts": seen,
            "headline": title,
            "url": str(url) if url else None,
            "tone": tone_val,
            "country": country,
            "source": "gdelt",
            "origin": "gdelt",
            "_query": q[:50],
        })
    return out


# ---------------------------------------------------------------------------
# GDELT fetcher
# ---------------------------------------------------------------------------


def fetch_gdelt_events(
    query: str = DEFAULT_GDELT_QUERY,
    *,
    cache_dir: Union[Path, str, None] = None,
    ttl_seconds: int = GDELT_TTL_SECONDS,
    max_results: int = 30,
    max_tone: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """拉取 GDELT 全球事件流。

    max_tone: 过滤掉 tone > max_tone 的事件（保留更负面的）。默认 None 不过滤。

    **注意：GDELT 2.0 公共 doc API 在实际使用中限流极严（远超过文档说的 5s/req），
    从多数网络 IP 调用会持续返回 "Please limit requests..."。本函数检测到
    限流时写空 cache 防止重试风暴，并返回 []。Dashboard 在 GDELT 不可用
    时优雅降级（仍显示 RSS + 财经日历）。**
    """
    cache_dir = Path(cache_dir) if cache_dir else REFERENCE_FEED_CACHE_DIR

    key = f"gdelt_{re.sub(r'[^a-zA-Z0-9]', '_', query)[:40]}"
    cached = _read_cache(cache_dir, key, ttl_seconds=ttl_seconds)
    if cached is not None:
        items = cached
    else:
        from urllib.parse import urlencode
        params = {
            "query": query,
            "mode": "ArtList",
            "maxrecords": str(max(50, max_results * 2)),
            "format": "json",
            "sort": "datedesc",
        }
        url = f"https://api.gdeltproject.org/api/v2/doc/doc?{urlencode(params)}"
        # 用浏览器 UA（GDELT 对机器人 UA 更严）
        raw_bytes = _http_get_bytes(url, timeout_s=10, user_agent="Mozilla/5.0")
        if raw_bytes is None:
            _write_cache(cache_dir, key, [])
            return []
        try:
            text = raw_bytes.decode("utf-8", errors="replace")
            if GDELT_RATE_LIMIT_MARKER in text:
                logger.debug("[reference_feeds] GDELT rate limited — caching empty")
                _write_cache(cache_dir, key, [])
                return []
            raw = json.loads(text)
        except json.JSONDecodeError:
            _write_cache(cache_dir, key, [])
            return []
        items = _parse_gdelt(raw, query=query)
        _write_cache(cache_dir, key, items)

    if max_tone is not None:
        items = [it for it in items if (it.get("tone") is None or it.get("tone") <= max_tone)]
    return items[:max_results]


# ---------------------------------------------------------------------------
# 全球事件流替代源 (NewsAPI.org / Currents API / RSS 头条合并)
# ---------------------------------------------------------------------------
# GDELT 2.0 公共 doc API 在实际使用中限流极严(返回 "Please limit requests...")。
# 为了 dashboard 始终有内容,加 3 个 fallback:
#   1. NewsAPI.org (免费档 100 req/day, 需要 NEWSAPI_KEY 环境变量)
#   2. Currents API (免费档 600 req/day, 需要 CURRENTS_API_KEY)
#   3. RSS 头条 关键词过滤 (Bloomberg/Reuters/MarketWatch 与 macro RSS 共享, 零依赖)
# 全部失败 → 返回 [], dashboard 显示"全球事件暂不可用"。
# ---------------------------------------------------------------------------

def _newsapi_get(url: str, api_key: str, timeout_s: int = 10) -> Optional[bytes]:
    """NewsAPI 专用 GET: 必须带 X-Api-Key 头(不能用 _http_get_bytes)。

    NewsAPI 文档: https://newsapi.org/docs/get-started
    - /v2/everything: q + language + sortBy + pageSize
    - /v2/top-headlines: country + category + pageSize (无需 q)
    - 鉴权: 必须用 X-Api-Key 头(Authorization Bearer 不支持)

    走 curl subprocess, 避免 Python urllib 代理 SSL 兼容问题。
    """
    curl_bin = "/usr/bin/curl"
    if not os.path.exists(curl_bin):
        for candidate in ("/opt/homebrew/bin/curl", "/usr/local/bin/curl"):
            if os.path.exists(candidate):
                curl_bin = candidate
                break
        else:
            return _newsapi_get_urllib(url, api_key, timeout_s)

    cmd = [
        curl_bin, "-sS", "-L", "--max-time", str(timeout_s),
        "-H", f"X-Api-Key: {api_key}",
        "-A", "us-etf-quant/1.0 (research; non-commercial)",
        url,
    ]
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("ALL_PROXY")
        or ""
    )
    if proxy:
        cmd[1:1] = ["-x", proxy]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout_s + 10, check=False)
        if result.returncode == 0 and result.stdout:
            return result.stdout
        logger.debug("[reference_feeds] NewsAPI curl failed rc=%d stderr=%s", result.returncode, result.stderr[:200])
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.debug("[reference_feeds] NewsAPI curl exec failed: %s", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("[reference_feeds] NewsAPI curl unexpected: %s", exc)
        return None


def _newsapi_get_urllib(url: str, api_key: str, timeout_s: int) -> Optional[bytes]:
    """urllib fallback for NewsAPI."""
    try:
        req = urllib.request.Request(url, headers={
            "X-Api-Key": api_key,
            "User-Agent": "us-etf-quant/1.0 (research; non-commercial)",
        })
        proxy_handler = urllib.request.ProxyHandler({
            "http": os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY") or "",
            "https": os.environ.get("HTTPS_PROXY") or os.environ.get("ALL_PROXY") or "",
        })
        if not proxy_handler.proxies:
            proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        with opener.open(req, timeout=timeout_s) as resp:
            return resp.read()
    except Exception as exc:
        logger.debug("[reference_feeds] NewsAPI urllib fallback failed: %s", exc)
        return None


def fetch_newsapi_events(
    *,
    query: str = "stock market OR Federal Reserve OR inflation",
    language: str = "en",
    cache_dir: Union[Path, str, None] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """NewsAPI.org 替代源。免费档 100 req/day,需要 NEWSAPI_KEY。

    策略:
      1) 优先 /v2/top-headlines?country=us&category=business (免费档可用, 实时头条)
      2) fallback /v2/everything?q=stock+market&sortBy=publishedAt (按关键字搜索)

    两个任一成功即返回 (去重后), 全部失败返回 []。
    """
    api_key = os.environ.get("NEWSAPI_KEY", "").strip()
    if not api_key:
        return []
    cache_dir = Path(cache_dir) if cache_dir else REFERENCE_FEED_CACHE_DIR
    key = f"newsapi_{query[:30].replace(' ', '_')}_{language}"
    cached = _read_cache(cache_dir, key, ttl_seconds=ttl_seconds)
    if cached is not None:
        return cached[:max_results]

    out: List[Dict[str, Any]] = []
    seen_titles: set = set()

    def _consume(articles: list) -> None:
        for a in articles:
            if len(out) >= max_results:
                break
            if not isinstance(a, dict):
                continue
            title = (a.get("title") or "").strip()
            if not title or title == "[Removed]" or title in seen_titles:
                continue
            seen_titles.add(title)
            source_obj = a.get("source") or {}
            src_name = source_obj.get("name", "newsapi") if isinstance(source_obj, dict) else "newsapi"
            out.append({
                "ts": a.get("publishedAt"),
                "headline": title,
                "url": a.get("url"),
                "tone": None,
                "country": a.get("country") or "US",
                "source": f"newsapi:{src_name[:30]}",
                "origin": "newsapi",
                "category": "global_event",
            })

    # 1) /v2/top-headlines (免费档支持, 实时)
    top_url = (
        f"https://newsapi.org/v2/top-headlines?country=us&category=business"
        f"&pageSize={max_results}"
    )
    raw = _newsapi_get(top_url, api_key, timeout_s=10)
    if raw:
        try:
            data = json.loads(raw)
            if data.get("status") == "ok":
                _consume(data.get("articles") or [])
        except json.JSONDecodeError:
            pass

    # 2) /v2/everything (关键字搜索)
    if len(out) < max_results:
        ev_url = (
            f"https://newsapi.org/v2/everything?q={urllib.parse.quote(query)}"
            f"&language={language}&sortBy=publishedAt&pageSize={max_results}"
        )
        raw = _newsapi_get(ev_url, api_key, timeout_s=10)
        if raw:
            try:
                data = json.loads(raw)
                if data.get("status") == "ok":
                    _consume(data.get("articles") or [])
            except json.JSONDecodeError:
                pass

    _write_cache(cache_dir, key, out)
    return out[:max_results]


def fetch_currents_events(
    *,
    categories: Optional[List[str]] = None,
    language: str = "en",
    cache_dir: Union[Path, str, None] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """Currents API 替代源。免费档 600 req/day,需要 CURRENTS_API_KEY。

    **实际验证 (2026-06-07)**: Currents API 可用,返回真实全球新闻。
    优先用 ``category`` 参数拉"world/finance/economy/business" 4 个相关分类
    (实测 keywords 参数不支持,会返回 400)。

    返回的 items 已经按"全球事件"分类打了 category 标签。
    """
    api_key = os.environ.get("CURRENTS_API_KEY", "").strip()
    if not api_key:
        return []
    cats = categories or ["world", "finance", "economy", "business"]
    cache_dir = Path(cache_dir) if cache_dir else REFERENCE_FEED_CACHE_DIR
    key = f"currents_cat={'+'.join(cats)}_{language}"
    cached = _read_cache(cache_dir, key, ttl_seconds=ttl_seconds)
    if cached is not None:
        return cached[:max_results]

    out: List[Dict[str, Any]] = []
    seen_titles: set = set()
    # 每个 cat 分配 1/max_results 的名额, 保证 4 个分类都有代表
    per_cat = max(2, max_results // max(len(cats), 1) + 1)
    for cat in cats:
        if len(out) >= max_results:
            break
        url = (
            f"https://api.currentsapi.services/v1/latest-news?"
            f"apiKey={urllib.parse.quote(api_key)}&language={language}"
            f"&category={urllib.parse.quote(cat)}"
        )
        raw_bytes = _http_get_bytes(url, timeout_s=10, user_agent="us-etf-quant/1.0")
        if raw_bytes is None:
            continue
        try:
            raw = json.loads(raw_bytes)
        except json.JSONDecodeError:
            continue
        if raw.get("status") != "ok":
            continue
        added_from_cat = 0
        for news in (raw.get("news") or []):
            if added_from_cat >= per_cat or len(out) >= max_results:
                break
            title = (news.get("title") or "").strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            out.append({
                "ts": news.get("published"),
                "headline": title,
                "url": news.get("url"),
                "tone": None,
                "country": None,
                "source": f"currents:{cat}",
                "origin": "currents",
                "category": "global_event",
            })
            added_from_cat += 1
    _write_cache(cache_dir, key, out)
    return out[:max_results]


def fetch_global_events_fallback(
    *,
    cache_dir: Union[Path, str, None] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_results: int = 10,
    keywords: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """GDELT 限流时的多源合并 fallback 链(实测优先级 2026-06-07):

    1) **Currents API** (实测可用, 免费档 600 req/day, 已配 key)
       — 拉 world/finance/economy/business 4 个分类,去重后取 max_results//2
    2) **NewsAPI.org** (v2/top-headlines 优先 + v2/everything 兜底)
       — 补齐到 max_results,标题去重
    3) **Macro RSS 头条关键词过滤** (零依赖, 永远有内容, 最后兜底)

    返回去重后的合并列表,最多 max_results 条。
    """
    cache_dir = Path(cache_dir) if cache_dir else REFERENCE_FEED_CACHE_DIR
    out: List[Dict[str, Any]] = []
    seen: set = set()

    def _consume(items: List[Dict[str, Any]], cap: int) -> None:
        for it in items:
            if len(out) >= max_results or len([x for x in out if x.get("origin") == it.get("origin")]) >= cap:
                break
            if not isinstance(it, dict):
                continue
            title = (it.get("headline") or "").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            out.append(it)

    # 1) Currents (一半配额)
    currents = fetch_currents_events(
        cache_dir=cache_dir, ttl_seconds=ttl_seconds, max_results=max_results,
    )
    _consume(currents, cap=max(1, max_results // 2))

    # 2) NewsAPI 补齐 (另一半配额)
    if len(out) < max_results:
        remaining = max_results - len(out)
        newsapi = fetch_newsapi_events(
            cache_dir=cache_dir, ttl_seconds=ttl_seconds, max_results=remaining + 2,
        )
        _consume(newsapi, cap=max_results)

    if out:
        return out[:max_results]

    # 3) Macro RSS 关键词过滤 (零依赖兜底)
    kw = keywords or [
        "Federal Reserve", "Fed", "inflation", "CPI", "GDP", "rate",
        "stock", "S&P", "Nasdaq", "Wall Street", "treasury", "yield",
        "China", "PBOC", "ECB", "BOE", "BOJ",
    ]
    try:
        rss_items = fetch_macro_rss(
            cache_dir=cache_dir, ttl_seconds=ttl_seconds,
            max_results=80,
        )
    except Exception as exc:
        logger.debug("[reference_feeds] RSS fallback failed: %s", exc)
        return []
    # 关键词过滤 + 标记 origin
    out: List[Dict[str, Any]] = []
    seen_titles = set()
    for it in rss_items:
        title = (it.get("headline") or "").strip()
        if not title or title in seen_titles:
            continue
        title_lower = title.lower()
        if any(k.lower() in title_lower for k in kw):
            out.append({
                "ts": it.get("ts"),
                "headline": title,
                "url": it.get("url"),
                "tone": None,
                "country": None,
                "source": it.get("source", "rss"),
                "origin": "rss_fallback",
                "category": "global_event",
            })
            seen_titles.add(title)
        if len(out) >= max_results:
            break
    return out


# ---------------------------------------------------------------------------
# 经济日历
# ---------------------------------------------------------------------------


def _parse_calendar_investing(raw: Dict[str, Any], days_ahead: int = 7, *, _days_ahead: Optional[int] = None) -> List[Dict[str, Any]]:
    """解析 Investing.com 风格的日历响应。

    字段约定（基于 GDELT 事件流转换的格式）:
        events: [{date, time, country, event, importance, previous, forecast}]
    """
    d = _days_ahead if _days_ahead is not None else days_ahead
    events = raw.get("events") or []
    if not isinstance(events, list):
        return []
    today = dt.date.today()
    cutoff = today + dt.timedelta(days=d)
    out: List[Dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        date_str = str(ev.get("date") or "")
        try:
            ev_date = dt.date.fromisoformat(date_str)
        except (TypeError, ValueError):
            continue
        if ev_date < today or ev_date > cutoff:
            continue
        out.append({
            "date": date_str,
            "time": str(ev.get("time") or ""),
            "country": str(ev.get("country") or ""),
            "event": str(ev.get("event") or ""),
            "importance": int(ev.get("importance") or 0),
            "previous": str(ev.get("previous") or ""),
            "forecast": str(ev.get("forecast") or ""),
            "source": "calendar",
            "origin": "calendar",
        })
    out.sort(key=lambda x: (x.get("date") or "", x.get("time") or ""))
    return out


# ForexFactory 公开 JSON 镜像 — 替代 Investing scraping
# 字段：title / country / date (ISO 8601) / impact (Low/Medium/High/Holiday) / forecast / previous
# 注意：ff_calendar_nextweek.json 不存在（404），只支持 thisweek
_FOREXFACTORY_MIRROR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# Federal Reserve 公开日历 — 无 key，JSON 格式（带 BOM）
# 字段：month / days / time / title / type / description / location / live
_FED_CALENDAR_URL = "https://www.federalreserve.gov/json/calendar.json"

_IMPACT_TO_INT = {"Low": 1, "Medium": 2, "High": 3}


def _parse_forexfactory_json(raw_list: list, days_ahead: int = 7) -> List[Dict[str, Any]]:
    """解析 ForexFactory mirror 的 JSON 数组。

    字段映射:
        title → event
        country → country
        date (ISO 8601) → date + time
        impact (Low/Medium/High) → importance (1/2/3)
        Holiday → 跳过（非事件）
    """
    if not isinstance(raw_list, list):
        return []
    today = dt.date.today()
    cutoff = today + dt.timedelta(days=days_ahead)
    out: List[Dict[str, Any]] = []
    for ev in raw_list:
        if not isinstance(ev, dict):
            continue
        # 跳过假日标记
        impact = str(ev.get("impact") or "").strip()
        if impact == "Holiday":
            continue
        # 解析日期
        date_iso = str(ev.get("date") or "")
        try:
            ev_dt = dt.datetime.fromisoformat(date_iso)
        except (TypeError, ValueError):
            continue
        ev_date = ev_dt.date()
        ev_time = ev_dt.strftime("%H:%M")
        if ev_date < today or ev_date > cutoff:
            continue
        out.append({
            "date": ev_date.isoformat(),
            "time": ev_time,
            "country": str(ev.get("country") or ""),
            "event": str(ev.get("title") or ""),
            "importance": _IMPACT_TO_INT.get(impact, 1),
            "previous": str(ev.get("previous") or ""),
            "forecast": str(ev.get("forecast") or ""),
            "source": "forexfactory",
            "origin": "calendar",
        })
    out.sort(key=lambda x: (x.get("date") or "", x.get("time") or ""))
    return out


def fetch_economic_calendar(
    *,
    days_ahead: int = 30,
    days_back: int = 0,
    cache_dir: Union[Path, str, None] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_results: int = 30,
    include_fed_calendar: bool = True,
    min_importance: int = 3,
) -> List[Dict[str, Any]]:
    """拉取经济日历事件（仅未来 days_ahead 天,默认 30 天）。

    数据源：
    1. **ForexFactory 镜像**（nfs.faireconomy.media/ff_calendar_thisweek.json）
       — 完整经济日历（GDP/CPI/NFP/PMI 等）
    2. **Federal Reserve 公开日历**（federalreserve.gov/json/calendar.json）
       — FOMC 会议 + 美联储官员讲话

    两者合并去重，按时间排序。ForexFactory 失败 / 不可达时只显示 Fed 日历。

    参数：
    - ``min_importance``：最低重要性（1=Low, 2=Medium, 3=High）。默认 3（只看 High Impact）。
    - 去重: 用 (date, normalized_event_name) 避免同一天同事件多次出现。
    - 过滤: 只看未来 (today, today+days_ahead) 区间, 跳过已过的事件。
    """
    cache_dir = Path(cache_dir) if cache_dir else REFERENCE_FEED_CACHE_DIR

    all_events: List[Dict[str, Any]] = []
    today = dt.date.today()
    win_start = today - dt.timedelta(days=days_back)
    win_end = today + dt.timedelta(days=days_ahead)

    # ---- 1. ForexFactory (thisweek) ----
    key = "calendar_ff_thisweek"
    cached = _read_cache(cache_dir, key, ttl_seconds=ttl_seconds)
    if cached is not None:
        raw_list = cached
    else:
        raw_bytes = _http_get_bytes(_FOREXFACTORY_MIRROR_URL, timeout_s=10, user_agent="Mozilla/5.0")
        if raw_bytes is None:
            _write_cache(cache_dir, key, [])
            raw_list = []
        else:
            try:
                raw = json.loads(raw_bytes.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                _write_cache(cache_dir, key, [])
                raw_list = []
            raw_list = raw if isinstance(raw, list) else []
            _write_cache(cache_dir, key, raw_list)

    for ev in raw_list:
        if not isinstance(ev, dict):
            continue
        impact = str(ev.get("impact") or "").strip()
        if impact == "Holiday":
            continue
        date_iso = str(ev.get("date") or "")
        try:
            ev_dt = dt.datetime.fromisoformat(date_iso)
        except (TypeError, ValueError):
            continue
        ev_date = ev_dt.date()
        if ev_date < win_start or ev_date > win_end:
            continue
        all_events.append({
            "date": ev_date.isoformat(),
            "time": ev_dt.strftime("%H:%M"),
            "country": str(ev.get("country") or ""),
            "event": str(ev.get("title") or ""),
            "importance": _IMPACT_TO_INT.get(impact, 1),
            "previous": str(ev.get("previous") or ""),
            "forecast": str(ev.get("forecast") or ""),
            "source": "forexfactory",
            "origin": "calendar",
        })

    # ---- 2. Federal Reserve 公开日历 ----
    if include_fed_calendar:
        fed_key = "calendar_fed"
        fed_cached = _read_cache(cache_dir, fed_key, ttl_seconds=ttl_seconds)
        if fed_cached is not None:
            fed_raw = fed_cached
        else:
            fed_bytes = _http_get_bytes(_FED_CALENDAR_URL, timeout_s=10, user_agent="Mozilla/5.0 (research)")
            if fed_bytes is None:
                _write_cache(cache_dir, fed_key, [])
                fed_raw = []
            else:
                try:
                    # Fed JSON 有 UTF-8 BOM
                    fed_text = fed_bytes.decode("utf-8-sig", errors="replace")
                    fed_obj = json.loads(fed_text)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    _write_cache(cache_dir, fed_key, [])
                    fed_raw = []
                else:
                    # 兼容 fed_obj 是 list 或 dict（防御性）
                    if isinstance(fed_obj, dict):
                        fed_raw = fed_obj.get("events") or []
                    elif isinstance(fed_obj, list):
                        fed_raw = fed_obj
                    else:
                        fed_raw = []
                    _write_cache(cache_dir, fed_key, fed_raw)

        for ev in fed_raw:
            if not isinstance(ev, dict):
                continue
            # Fed 字段格式不同：month="2026-06", days="4", time="10:00 a.m."
            month_str = str(ev.get("month") or "")
            day_str = str(ev.get("days") or "")
            if not month_str or not day_str:
                continue
            try:
                ev_date = dt.date.fromisoformat(f"{month_str}-{int(day_str):02d}")
            except (TypeError, ValueError):
                continue
            if ev_date < win_start or ev_date > win_end:
                continue
            ev_time = str(ev.get("time") or "")
            ev_title = str(ev.get("title") or "")
            ev_type = str(ev.get("type") or "").lower()
            # Fed 重要程度映射 + 排除"每日发布的统计"(H.4.1 / H.6 / H.8 / H.10 / H.15 / CP)
            # 这些是每天/每周都发布的细碎数据, 不是用户关注的"重要事件"
            skip_daily_stat_prefixes = (
                "h.4.1", "h.6", "h.8", "h.10", "h.15", "cp -", "g.5", "g.19", "g.20",
            )
            if ev_type in ("stat",) and any(ev_title.lower().startswith(p) for p in skip_daily_stat_prefixes):
                continue
            importance = 1
            if "fomc" in ev_type or "monetary policy" in ev_type or "beige" in ev_type:
                importance = 3
            elif "testimony" in ev_type or "speech" in ev_type or "board" in ev_type or "conference" in ev_type:
                importance = 2
            all_events.append({
                "date": ev_date.isoformat(),
                "time": ev_time,
                "country": "USD",
                "event": ev_title,
                "importance": importance,
                "previous": "",
                "forecast": "",
                "source": "federalreserve",
                "origin": "calendar",
            })

    # 去重: 用 (date, 规范化事件名) 避免同一天同事件多次出现
    # 例如 "FOMC Member Daly Speaks" 在 ForexFactory 和 Fed 日历里都出现
    def _norm_event(name: str) -> str:
        n = re.sub(r"\s+", " ", name or "").strip().lower()
        # 去掉数字/标点差异 ("FOMC Member Daly Speaks" vs "FOMC Member Daly Speaks")
        n = re.sub(r"[^a-z0-9 ]+", "", n)
        # 去掉表示"重要性"的数字 ("3 ⭐" 等)
        return n

    seen = set()
    deduped = []
    for ev in all_events:
        key = (ev["date"], _norm_event(ev["event"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ev)

    # 重要性过滤（min_importance: 1=全部, 2=Medium+, 3=High only）
    if min_importance > 1:
        deduped = [ev for ev in deduped if ev.get("importance", 1) >= min_importance]

    deduped.sort(key=lambda x: (x.get("date") or "", x.get("time") or ""))
    # 重要: 不要在这里截断 max_results. ForexFactory thisweek 单周 ~80 条事件
    # 会把 6/15+ Fed Reserve 长窗口事件挤掉. 让 caller 拿到全量后再用
    # importance 二次过滤截断 (build_dashboard.py 会做这步).
    return deduped



def translate_to_chinese(texts):
    """把所有标题统一交给 MiniMax (走 anthropic 协议) 翻译为简体中文。

    无 key / 网络失败时,返回 [None, None, ...] — caller 用英文原文兜底。

    设计:
      - 单次 API 调用批量翻译所有标题(节省 token, 降低延迟)
      - 系统 prompt 内置财经术语词典（强制译法表）+ 输出格式硬约束 (A1)
      - 解析时宽松(去掉 ``` 包裹, 抓首尾 {})
      - 已包含较多中文的标题直接原样返回, 不浪费 API 额度
      - Per-item fallback retry (A2): 批量解析后缺失的项单条 prompt 再调一次
      - 质量守门 (A3): 译后压缩检测 (字符数 < 原文单词数 × 0.8 且原文 > 5 词 → 弃用)
    """
    if not texts:
        return []

    # 1) 已经是中文的项直接跳过(节省 API 额度)
    results: List[Optional[str]] = []
    pending_indices: List[int] = []
    pending_texts: List[str] = []
    for i, t in enumerate(texts):
        if not t or not t.strip():
            results.append(None)
            continue
        chinese_chars = sum(1 for c in t if "一" <= c <= "鿿")
        if chinese_chars > len(t) * 0.5:
            results.append(t)
        else:
            results.append(None)
            pending_indices.append(i)
            pending_texts.append(t)

    if not pending_texts:
        return results

    # 2) 单次 API 调用, 批量翻译
    try:
        from llm.client import call_llm, get_llm_config
        cfg = get_llm_config()
    except Exception as exc:
        logger.debug("[reference_feeds] llm.client import failed: %s", exc)
        return results

    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(pending_texts))
    system = _build_translation_system_prompt()
    user = numbered

    # 应用层重试: MiniMax 对小 batch(< 10) 经常返回空,3 次重试救场
    result = None
    for attempt in range(3):
        try:
            result = call_llm(system, user, cfg=cfg)
        except Exception as exc:
            logger.debug("[reference_feeds] MiniMax call attempt %d failed: %s", attempt + 1, exc)
            continue
        if result and result.content and result.content.strip():
            break
        logger.debug("[reference_feeds] MiniMax attempt %d returned empty content (batch size=%d)", attempt + 1, len(pending_texts))
        time.sleep(1.5)  # 退避, 避免连续 0 batch 失败
    if result is None:
        return results

    # 3) 解析 — 宽松处理, 支持各种格式:
    #   A) "1. xxx" / "1) xxx" / "1: xxx" / "1 xxx"
    #   B) "1. **原文** → 译文" (带 markdown + 箭头)
    #   C) 纯裸中文 (按行顺序对应)
    text = result.content
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    out_map: Dict[int, str] = {}
    seq_match_count = 0
    for line in lines:
        # 模式 A/B: 带序号的 "N. xxx" / "N) xxx" / "N: xxx"
        m = re.match(r"^(\d+)\s*[\.\)\:\-]\s*(.*)$", line)
        if m:
            n = int(m.group(1))
            translation = m.group(2).strip()
            # 去掉 markdown 加粗 **xxx** 和尾部箭头 "→ xxx"
            # 模式: "1. **原文** → 译文" → 取箭头后内容
            if "→" in translation:
                parts = translation.split("→", 1)
                if len(parts) == 2 and parts[1].strip():
                    translation = parts[1].strip()
            # 去掉 **xxx**
            translation = re.sub(r"\*\*([^*]+)\*\*", r"\1", translation)
            # 去掉 "原文：" "原: " 之类前缀
            translation = re.sub(r"^[^：:]+[：:]\s*", "", translation).strip()
            if translation and 1 <= n <= len(pending_texts):
                out_map[n - 1] = translation
                seq_match_count += 1

    # 兜底: 解析失败/无序号 → 按行顺序对应原数组
    if seq_match_count == 0 and lines:
        for i, line in enumerate(lines[:len(pending_texts)]):
            t = line
            if "→" in t:
                parts = t.split("→", 1)
                if len(parts) == 2 and parts[1].strip():
                    t = parts[1].strip()
            t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
            t = re.sub(r"^\d+[\.\)\:\-]\s*", "", t)
            t = re.sub(r"^[^：:]+[：:]\s*", "", t).strip()
            out_map[i] = t

    # 4) Per-item fallback retry (A2): 找 out_map 缺失的 index, 单条 prompt 再调一次
    missing = [i for i in range(len(pending_texts)) if i not in out_map or not out_map.get(i)]
    if missing:
        logger.info("[reference_feeds] per-item retry for %d/%d items", len(missing), len(pending_texts))
        for mi in missing:
            single_text = pending_texts[mi]
            single_user = f"1. {single_text}"
            single_result = None
            for attempt in range(2):
                try:
                    single_result = call_llm(system, single_user, cfg=cfg)
                except Exception as exc:
                    logger.debug("[reference_feeds] per-item retry attempt %d failed for idx %d: %s", attempt + 1, mi, exc)
                    continue
                if single_result and single_result.content and single_result.content.strip():
                    break
                time.sleep(1.0)
            if single_result is None:
                continue
            stext = single_result.content.strip()
            if stext.startswith("```"):
                stext = re.sub(r"^```[a-zA-Z]*\n?", "", stext)
                stext = re.sub(r"\n?```\s*$", "", stext)
            sline = next((l.strip() for l in stext.split("\n") if l.strip()), "")
            sline = re.sub(r"^\d+\s*[\.\)\:\-]\s*", "", sline)
            if "→" in sline:
                parts = sline.split("→", 1)
                if len(parts) == 2 and parts[1].strip():
                    sline = parts[1].strip()
            sline = re.sub(r"\*\*([^*]+)\*\*", r"\1", sline)
            sline = re.sub(r"^[^：:]+[：:]\s*", "", sline).strip()
            if sline:
                out_map[mi] = sline

    # 5) 写回 results — 应用质量守门 (A3)
    for i, orig_idx in enumerate(pending_indices):
        if i in out_map and out_map[i]:
            translated = out_map[i]
            orig = pending_texts[i]
            # 守门 1: 压缩检测 — 译文字符数 < 原文单词数 × 0.8 且原文 > 5 词 → 弃用
            orig_word_count = len(orig.split())
            if orig_word_count > 5 and len(translated) < orig_word_count * 0.8:
                logger.warning(
                    "[reference_feeds] suspicious compression rejected: idx=%d orig=%r translated=%r (chars %d vs words %d)",
                    orig_idx, orig[:60], translated[:60], len(translated), orig_word_count,
                )
                continue  # 弃用, results[orig_idx] 保持 None
            # 守门 2: 术语守门 — 原文含特定关键词时译文必须含对应中文译名 (warning only)
            for keyword, required_zh in _TERM_GUARD.items():
                if keyword in orig and required_zh not in translated:
                    logger.warning(
                        "[reference_feeds] term-guard mismatch: idx=%d keyword=%r expected=%r in %r",
                        orig_idx, keyword, required_zh, translated[:60],
                    )
                    break
            results[orig_idx] = translated

    return results


# ---------------------------------------------------------------------------
# 翻译 prompt 模板 (A1) — 财经术语词典 + 输出格式硬约束
# ---------------------------------------------------------------------------

# 术语守门: 原文含这些英文关键词时, 译文必须包含指定中文译名
# 规则: 关键词越具体越好 (Core CPI 比 CPI 更具体), 避免误伤
_TERM_GUARD: Dict[str, str] = {
    "Core CPI": "消费者价格指数",
    "OPEC-JMMC": "欧佩克-联合部长级监督委员会",
    "FOMC": "FOMC",
    "JOLTS": "JOLTS",
    "BRENT": "布伦特",
    "UoM": "密歇根大学",
    "University of Michigan": "密歇根大学",
}


def _build_translation_system_prompt() -> str:
    """构造翻译 system prompt — 包含财经术语词典 (强制) + 输出格式硬约束。

    设计要点:
      1. 术语词典: 列出常见财经专有名词的官方/约定译法, 强制 LLM 使用一致译名
      2. 格式硬约束: 编号 + 一行一条 + 禁止 markdown 包裹
      3. 长度守门 (防"豪华"类 2-4 字压缩)
      4. 已含中文的输入也要按编号原样回显, 不要省略
    """
    term_lines = "\n".join(
        f"  - {en} → {zh}"
        for en, zh in [
            ("CPI / Core CPI / Headline CPI", "消费者价格指数 (CPI)"),
            ("PPI", "生产者价格指数 (PPI)"),
            ("NFP / Non-Farm Payrolls", "非农就业人数 (NFP)"),
            ("JOLTS", "职位空缺与劳动力流动调查 (JOLTS)"),
            ("PCE", "个人消费支出物价指数 (PCE)"),
            ("GDP", "国内生产总值 (GDP)"),
            ("FOMC / Federal Reserve / Fed", "美联储 (FOMC)"),
            ("ECB", "欧洲央行 (ECB)"),
            ("BOJ", "日本央行 (BOJ)"),
            ("BOE", "英国央行 (BOE)"),
            ("OPEC", "欧佩克 (OPEC)"),
            ("OPEC-JMMC", "欧佩克-联合部长级监督委员会会议"),
            ("BRENT", "布伦特原油"),
            ("WTI", "WTI 原油"),
            ("DXY / Dollar Index", "美元指数 (DXY)"),
            ("S&P 500 / SPX", "标普 500"),
            ("Nasdaq / NDX", "纳斯达克"),
            ("Treasury / UST", "美债"),
            ("UoM / University of Michigan", "密歇根大学"),
            ("Confidence Index", "消费者信心指数"),
            ("Rate Decision / Rate Statement", "利率决议 / 利率声明"),
            ("Press Conference", "新闻发布会"),
            ("Reuters / Bloomberg / WSJ / CNBC (媒体名)", "保留英文不译"),
            ("country code ALL flag area", "全球"),
        ]
    )

    return (
        "你是资深财经新闻翻译员。把每条英文标题翻译为简体中文, 仅返回编号译文。\n\n"
        "【财经术语词典 (强制使用, 译法不可改)】\n"
        f"{term_lines}\n\n"
        "【翻译规则】\n"
        "  1. 严格保留人名 / 数字 / 机构名 / 百分比方向 (+ / -) / 货币符号\n"
        "  2. 译后中文字符数应 >= 原文单词数 × 0.8 (防'豪华''关注要点'类 2-4 字压缩)\n"
        "  3. 原文若是全数字/全符号/全专有名词, 仍按编号原样回显, 不要省略\n"
        "  4. 不要添加解释、不要 markdown 包裹、不要 ``` 代码块、不要开头问候语\n\n"
        "【输出格式 (严格遵守)】\n"
        "  1. 第一条译文\n"
        "  2. 第二条译文\n"
        "  3. 第三条译文\n"
        "  ...\n"
        "  N. 第 N 条译文\n"
        "  必须是 N 行, 一条不少, 序号与输入对齐。"
    )


# ---------------------------------------------------------------------------
# Combined fetcher
# ---------------------------------------------------------------------------


def fetch_all_reference_feeds(
    *,
    cache_dir: Union[Path, str, None] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    max_rss: int = 10,
    max_gdelt: int = 10,
    max_calendar: int = 20,
    min_calendar_importance: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """一次拉取全部 3 个 feed。

    ``min_calendar_importance``: 1=Low(全部), 2=Medium+, 3=High only。

    GDELT 三级 fallback 链: GDELT 官方 → NewsAPI → Currents → RSS 关键词过滤。
    """
    rss = fetch_macro_rss(cache_dir=cache_dir, ttl_seconds=ttl_seconds, max_results=max_rss)
    # GDELT 三级 fallback
    gdelt = fetch_gdelt_events(cache_dir=cache_dir, ttl_seconds=ttl_seconds, max_results=max_gdelt)
    if not gdelt:
        gdelt = fetch_global_events_fallback(
            cache_dir=cache_dir, ttl_seconds=ttl_seconds, max_results=max_gdelt,
        )
    cal = fetch_economic_calendar(
        cache_dir=cache_dir, ttl_seconds=ttl_seconds, max_results=max_calendar,
        min_importance=min_calendar_importance,
    )
    return {
        "rss": rss[:max_rss],
        "gdelt": gdelt[:max_gdelt],
        "calendar": cal[:max_calendar],
    }
