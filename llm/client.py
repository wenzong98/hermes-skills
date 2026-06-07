#!/usr/bin/env python3
"""
===================================
LLM Client (anthropic + openai-compat + offline mock)
===================================

极简 LLM 客户端，专为美股 ETF 周报副驾驶设计。

特性：
  1. 支持 anthropic / openai 兼容协议 — 通过环境变量切换
  2. 强超时 + 重试 + 失败兜底（绝不抛异常到上游）
  3. JSON 模式输出（Plan A/B 都用结构化 JSON 输出）
  4. Token 用量记录到 ``references/llm_usage.jsonl``
  5. 离线 mock 后端（``--llm-backend mock``）— 供测试和 CI

环境变量：
  - ``LLM_API_KEY``          必填（生产）
  - ``LLM_BASE_URL``         可选（默认 anthropic 官方；openai 兼容模式需要）
  - ``LLM_MODEL``            模型名，默认 ``claude-3-5-haiku-latest``
  - ``LLM_BACKEND``          ``anthropic`` (默认) | ``openai`` | ``mock``
  - ``LLM_TIMEOUT_S``        超时秒数，默认 20
  - ``LLM_MAX_RETRIES``      重试次数，默认 2
  - ``LLM_USAGE_LOG``        用量日志路径，默认 ``references/llm_usage.jsonl``
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
import ssl as _ssl

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSL fallback (for local proxies / LibreSSL envs that fail cert verify)
# ---------------------------------------------------------------------------


def _open_url_with_ssl_fallback(req, timeout: int):
    """urlopen with auto fallback to unverified SSL on cert mismatch.

    LibreSSL 2.8.3 (macOS system Python 3.9) 经常因为旧 CA bundle 失败。
    对内部代理 / 自签名证书场景，自动降级到 unverified context（一次性 warn）。
    """
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except (urllib.error.URLError, TimeoutError) as exc:
        reason = str(exc)
        if "CERTIFICATE_VERIFY_FAILED" in reason or "hostname mismatch" in reason.lower():
            logger.warning(
                "[llm] SSL verify failed (%s) — falling back to unverified context. "
                "Set PYTHONHTTPSVERIFY=0 or upgrade Python's certifi bundle if this is unexpected.",
                reason[:120],
            )
            ctx = _ssl._create_unverified_context()
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        raise


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LLMUnavailableError(RuntimeError):
    """LLM 服务不可用（无 key / 超时 / 网络错误）。永远不抛到上游。"""


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class LLMCallResult:
    content: str
    input_tokens: int
    output_tokens: int
    elapsed_s: float
    model: str
    backend: str
    raw: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def get_llm_config() -> Dict[str, str]:
    """读取 LLM 配置。所有字段都有兜底。"""
    return {
        "api_key": os.getenv("LLM_API_KEY", "").strip(),
        "base_url": os.getenv("LLM_BASE_URL", "").strip(),
        "model": os.getenv("LLM_MODEL", "claude-3-5-haiku-latest").strip(),
        "backend": os.getenv("LLM_BACKEND", "anthropic").strip().lower(),
        "timeout_s": os.getenv("LLM_TIMEOUT_S", "20").strip(),
        "max_retries": os.getenv("LLM_MAX_RETRIES", "2").strip(),
        "usage_log": os.getenv(
            "LLM_USAGE_LOG", "references/llm_usage.jsonl"
        ).strip(),
    }


def is_llm_enabled(cfg: Optional[Dict[str, str]] = None) -> bool:
    """LLM 是否启用？仅在没有 api_key 且不是 mock 后端时返回 False。"""
    cfg = cfg or get_llm_config()
    if cfg["backend"] == "mock":
        return True
    return bool(cfg["api_key"])


# ---------------------------------------------------------------------------
# Usage logger
# ---------------------------------------------------------------------------


def _log_usage(cfg: Dict[str, str], result: Optional[LLMCallResult], error: Optional[str]) -> None:
    """追加一行 jsonl 到 ``LLM_USAGE_LOG``。失败时静默（不影响主流程）。"""
    try:
        log_path = Path(cfg["usage_log"]).expanduser()
        if not log_path.is_absolute():
            log_path = Path.cwd() / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": time.time(),
            "backend": cfg["backend"],
            "model": cfg["model"],
            "input_tokens": result.input_tokens if result else 0,
            "output_tokens": result.output_tokens if result else 0,
            "elapsed_s": round(result.elapsed_s, 3) if result else 0.0,
            "ok": error is None,
            "error": error,
        }
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 - logging must never break the caller
        logger.debug("[llm] usage log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def _call_anthropic(cfg: Dict[str, str], system: str, user: str) -> LLMCallResult:
    """Anthropic Messages API."""
    base = cfg["base_url"] or "https://api.anthropic.com"
    url = base.rstrip("/") + "/v1/messages"
    body = {
        "model": cfg["model"],
        "max_tokens": 1024,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": cfg["api_key"],
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    timeout = int(cfg["timeout_s"])
    started = time.time()
    with _open_url_with_ssl_fallback(req, timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    elapsed = time.time() - started

    # OpenAI returns content as message.content

    # Anthropic returns content as list of blocks
    blocks = payload.get("content") or []
    text_chunks = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    content = "".join(text_chunks).strip()
    usage = payload.get("usage") or {}
    return LLMCallResult(
        content=content,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        elapsed_s=elapsed,
        model=str(payload.get("model") or cfg["model"]),
        backend="anthropic",
        raw=payload,
    )


def _call_openai(cfg: Dict[str, str], system: str, user: str) -> LLMCallResult:
    """OpenAI 兼容 Chat Completions API（DeepSeek/MiniMax/OpenAI/智谱 等）。"""
    base = cfg["base_url"] or "https://api.openai.com"
    url = base.rstrip("/") + "/v1/chat/completions"
    body = {
        "model": cfg["model"],
        "max_tokens": 1024,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {cfg['api_key']}",
        },
        method="POST",
    )

    timeout = int(cfg["timeout_s"])
    started = time.time()
    with _open_url_with_ssl_fallback(req, timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    elapsed = time.time() - started

    choices = payload.get("choices") or []
    content = ""
    if choices:
        content = (choices[0].get("message") or {}).get("content", "")
    usage = payload.get("usage") or {}
    return LLMCallResult(
        content=content.strip(),
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        elapsed_s=elapsed,
        model=str(payload.get("model") or cfg["model"]),
        backend="openai",
        raw=payload,
    )


def _call_mock(cfg: Dict[str, str], system: str, user: str) -> LLMCallResult:
    """Mock 后端 — 永远成功，返回固定结构化输出。

    用于：
      - 单元测试和 CI（不需要真 key）
      - 本地开发时 preview 推送内容
      - v2 工具化副驾驶（返回 review JSON 兜底，不调工具）
    """
    started = time.time()
    is_review = ("副驾驶审查" in system or "副驾驶" in system or "review" in system.lower())
    is_v2_strategy = (
        "工具调用协议" in system
        or "宏观周期审查" in system
        or "恐慌阶梯审查" in system
    )

    if is_v2_strategy:
        # v2 工具化副驾驶：模拟"先调 2 个工具，再出结论"
        # 这里直接返回最终 JSON（mock 不真的发 tool_call）
        # 用 display_name 而不是 "panic"（避免和工具描述里的 panic_tier 冲突）
        if "恐慌阶梯" in system:
            content = json.dumps(
                {
                    "verdict": "恐慌阶梯审查：当前 VIX=15.4 未触发任一阶梯",
                    "agreement": "agree",
                    "risks_blindspots": [
                        "VIX 距 28 阶梯还差 12.6，短期内无加仓窗口",
                        "建议持续监控地缘事件对 VIX 的扰动",
                    ],
                    "reminder": "系统 panic_tier=0 合理，无需调整",
                },
                ensure_ascii=False,
            )
        else:
            content = json.dumps(
                {
                    "verdict": "宏观周期审查：CAPE 41 确认 very_expensive 档",
                    "agreement": "agree",
                    "risks_blindspots": [
                        "Fed 利率路径仍不明朗，需关注 6 月 FOMC",
                        "若 10Y 收益率跳升 >20bp，定投节流需更激进",
                    ],
                    "reminder": "按 0.75x 节流执行即可",
                },
                ensure_ascii=False,
            )
    elif is_review:
        content = json.dumps(
            {
                "verdict": "系统在当前估值下保持了应有的定投节流，没有看到盲点。",
                "agreement": "agree",
                "risks_blindspots": [
                    "CAPE 41 已接近历史 95 分位，模型给的 0.75x 是合适上限；"
                    "若美债收益率意外跳升，定投节流可能需要更激进。",
                ],
                "reminder": "本周按系统建议执行即可，无需手动干预。",
            },
            ensure_ascii=False,
        )
    else:
        content = json.dumps(
            {
                "explanation": (
                    "当前 Shiller CAPE=41 处于极高估值档，规则引擎把定投倍率压到 0.75x；"
                    "SPY 仍在 SMA200 之上且 VIX<20，所以保留下限 0.75x 而不是全停。"
                    "QQQ 过去 63/126 日相对 SPY 略强，所以本周新买入 60% 偏向 QQQ，"
                    "整体继续按节奏执行，不要追高也不要停。"
                )
            },
            ensure_ascii=False,
        )
    elapsed = time.time() - started
    return LLMCallResult(
        content=content,
        input_tokens=len(system) + len(user),
        output_tokens=len(content),
        elapsed_s=elapsed,
        model=cfg["model"],
        backend="mock",
    )


# ---------------------------------------------------------------------------
# Public entry: with retry
# ---------------------------------------------------------------------------


def call_llm(
    system: str,
    user: str,
    cfg: Optional[Dict[str, str]] = None,
) -> LLMCallResult:
    """主入口：调用 LLM，带超时 + 重试 + 失败兜底。

    永不抛异常：失败返回的 LLMCallResult.content="" 且 raw=None。
    调用方应检查 ``result.content`` 是否非空。
    """
    cfg = cfg or get_llm_config()

    # 未启用 — 直接静默
    if not is_llm_enabled(cfg):
        logger.info("[llm] skipped: LLM_API_KEY missing and backend != mock")
        _log_usage(cfg, None, "disabled")
        return LLMCallResult("", 0, 0, 0.0, cfg["model"], cfg["backend"])

    backends = {
        "anthropic": _call_anthropic,
        "openai": _call_openai,
        "mock": _call_mock,
    }
    fn = backends.get(cfg["backend"])
    if fn is None:
        logger.warning("[llm] unknown backend %s, falling back to mock", cfg["backend"])
        fn = _call_mock

    retries = max(0, int(cfg["max_retries"]))
    last_error: Optional[str] = None
    for attempt in range(retries + 1):
        try:
            result = fn(cfg, system, user)
            _log_usage(cfg, result, None)
            return result
        except (socket.timeout, TimeoutError) as exc:
            last_error = f"timeout: {exc}"
            logger.warning("[llm] attempt %d timeout: %s", attempt + 1, exc)
        except urllib.error.HTTPError as exc:
            last_error = f"http {exc.code}: {exc.reason}"
            logger.warning("[llm] attempt %d http error: %s", attempt + 1, last_error)
        except urllib.error.URLError as exc:
            last_error = f"url error: {exc.reason}"
            logger.warning("[llm] attempt %d url error: %s", attempt + 1, last_error)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("[llm] attempt %d error: %s", attempt + 1, last_error)
        if attempt < retries:
            time.sleep(min(2 ** attempt, 5))

    logger.error("[llm] all retries failed: %s", last_error)
    _log_usage(cfg, None, last_error)
    return LLMCallResult("", 0, 0, 0.0, cfg["model"], cfg["backend"])
