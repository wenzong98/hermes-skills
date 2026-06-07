"""LLM 副驾驶模块。

- ``schema``: WeeklyAdvice 结构化输出契约
- ``client``: 轻量 LLM 客户端（anthropic + openai 兼容 + offline mock）
- ``advisor``: 方案 A（副驾驶审查）+ 方案 B（rationale 解释）入口

设计目标：
  1. LLM 零决策权 — 不参与 multiplier / 权重的计算
  2. 强 PIT — 给 LLM 的数据是 T-1 收盘后的事实快照
  3. 失败兜底 — LLM 调用失败时原推送照常发，副标题使用 fallback 文案
  4. Token 经济 — 一次调用 ≤ 2k 输入 + 500 输出
"""
from __future__ import annotations

from llm.schema import (
    LLMExplanation,
    LLMReview,
    MarketSnapshot,
    Diagnosis,
    Decision,
    RecentSignal,
    TrimState,
    PortfolioSummary,
    RecommendedAction,
    WeeklyAdvice,
    build_fake_advice,
)

__all__ = [
    "LLMExplanation",
    "LLMReview",
    "MarketSnapshot",
    "Diagnosis",
    "Decision",
    "RecentSignal",
    "TrimState",
    "PortfolioSummary",
    "RecommendedAction",
    "WeeklyAdvice",
    "build_fake_advice",
]
