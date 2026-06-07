#!/usr/bin/env python3
"""
===================================
LLM Copilot CLI — 副驾驶审查 + rationale 解释
===================================

入口脚本：读 ``current_market_advice.json`` → 调 LLM → 写出 LLM 增强版。

用法::

    # 用 mock 后端（默认；测试/CI 友好）
    python scripts/llm_copilot.py \\
        --advice-json references/current_run/current_market_advice.json \\
        --output-dir references/current_run

    # 用 anthropic 后端
    LLM_API_KEY=sk-ant-... LLM_BACKEND=anthropic python scripts/llm_copilot.py ...

    # 只跑方案 A（review）
    python scripts/llm_copilot.py --plans review

    # 只跑方案 B（explain）
    python scripts/llm_copilot.py --plans explain

    # 同时跑（默认）
    python scripts/llm_copilot.py --plans review,explain

输出文件：
  - ``<output-dir>/current_market_advice.json``   原 payload 加上 ``llm_review`` / ``llm_explanation``
  - ``<output-dir>/llm_review.md``                 方案 A 推送副标题片段
  - ``<output-dir>/llm_explanation.md``            方案 B 推送副标题片段

设计：
  - LLM 调用失败时，原 payload 不变，只追加 error 字段 — 推送照常发
  - 不改写任何 multiplier/权重 — 严格只读
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import List

# Make project root importable so `from llm.advisor import ...` works
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm.advisor import (
    explain_decision,
    fallback_explanation_text,
    fallback_review_text,
    render_strategy_review_markdown,
    review_signal,
    review_with_tools_ex,
)
from llm.schema import WeeklyAdvice
from llm.strategies import get_strategy, list_strategies

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_review_md(advice: WeeklyAdvice) -> str:
    """把 LLMReview 转成推送副标题 markdown。"""
    review = advice.llm_review
    if review is None or not review.enabled:
        return ""
    lines = []
    if review.error:
        lines += [
            "## LLM 副驾驶审查（fallback — LLM 未启用或失败）",
            "",
            f"> ⚠️ {review.error}",
            "",
            f"> {fallback_review_text(advice)}",
            "",
        ]
        return "\n".join(lines)

    lines += [
        "## LLM 副驾驶审查",
        "",
    ]
    if review.verdict:
        lines.append(f"**{review.verdict}**")
    agreement_label = {
        "agree": "🟢 同意",
        "caution": "🟡 谨慎同意",
        "disagree": "🔴 不同意",
    }.get(review.agreement or "", "🟡 谨慎同意")
    lines += [
        "",
        f"立场：{agreement_label}",
        "",
    ]
    if review.risks_blindspots:
        lines.append("**风险/盲点：**")
        for r in review.risks_blindspots:
            lines.append(f"- {r}")
        lines.append("")
    if review.reminder:
        lines.append(f"> 💡 {review.reminder}")
        lines.append("")
    lines += [
        "",
        f"<sub>模型：{review.model} · 入 {review.input_tokens} / 出 {review.output_tokens} tokens · 生成于 {review.generated_at}</sub>",
    ]
    return "\n".join(lines)


def _render_explanation_md(advice: WeeklyAdvice) -> str:
    """把 LLMExplanation 转成推送副标题 markdown。"""
    expl = advice.llm_explanation
    if expl is None or not expl.enabled:
        return ""
    lines = [
        "## 为什么要这样建议（人话版）",
        "",
    ]
    if expl.error:
        lines += [
            f"> ⚠️ LLM 解释失败（{expl.error}），使用系统原文：",
            "",
            f"> {advice.diagnosis.rule_reason}",
            "",
        ]
        return "\n".join(lines)
    lines += [
        expl.explanation or "",
        "",
        f"<sub>模型：{expl.model} · 入 {expl.input_tokens} / 出 {expl.output_tokens} tokens</sub>",
    ]
    return "\n".join(lines)


def _load_advice(path: Path) -> WeeklyAdvice:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return WeeklyAdvice.from_payload_dict(payload)


def _save_advice(advice: WeeklyAdvice, original_payload: dict, path: Path) -> None:
    """把 LLM 字段加回原始 payload，保留一切现有字段。"""
    out = dict(original_payload)
    if advice.llm_review is not None:
        out["llm_review"] = advice.llm_review.__dict__
    if advice.llm_explanation is not None:
        out["llm_explanation"] = advice.llm_explanation.__dict__
    path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 副驾驶 — 审查 + 解释")
    parser.add_argument(
        "--advice-json",
        required=True,
        help="current_market_advice.build_payload 的输出 JSON 路径",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="输出目录（覆盖写入 advice-json 副本 + llm_review.md + llm_explanation.md）",
    )
    parser.add_argument(
        "--plans",
        default="review,explain",
        help="逗号分隔：review,explain（默认都跑）",
    )
    parser.add_argument(
        "--no-rewrite-advice-json",
        action="store_true",
        help="不写回 advice-json，只产出 review/explanation md 副本",
    )
    parser.add_argument(
        "--strategy",
        default="none",
        help=(
            "v2 工具化副驾驶使用的策略名（strategies/*.yaml 的 name 字段）。"
            "默认 'none' = 不跑 v2。"
            f"可用：{', '.join(list_strategies())}"
        ),
    )
    parser.add_argument(
        "--tool-budget",
        type=int,
        default=5,
        help="v2 工具调用预算（默认 5）",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    plans = [p.strip() for p in args.plans.split(",") if p.strip()]
    if not plans:
        plans = ["review", "explain"]

    advice_path = Path(args.advice_json).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not advice_path.exists():
        logger.error("advice json not found: %s", advice_path)
        return 1

    logger.info("loading advice from %s", advice_path)
    original_payload = json.loads(advice_path.read_text(encoding="utf-8"))
    advice = _load_advice(advice_path)
    logger.info(
        "loaded advice: date=%s regime=%s mult=%.2fx",
        advice.market.latest_market_date,
        advice.diagnosis.regime,
        advice.decision.dca_multiplier,
    )

    # 方案 A — 副驾驶审查
    if "review" in plans:
        logger.info("[plan A] running LLM review...")
        advice.llm_review = review_signal(advice)
        if advice.llm_review.error:
            logger.warning("[plan A] failed: %s", advice.llm_review.error)
        else:
            logger.info(
                "[plan A] done: agreement=%s verdict=%s",
                advice.llm_review.agreement,
                (advice.llm_review.verdict or "")[:60],
            )

    # 方案 B — rationale 解释
    if "explain" in plans:
        logger.info("[plan B] running LLM explanation...")
        advice.llm_explanation = explain_decision(advice)
        if advice.llm_explanation.error:
            logger.warning("[plan B] failed: %s", advice.llm_explanation.error)
        else:
            logger.info(
                "[plan B] done: %d chars",
                len(advice.llm_explanation.explanation or ""),
            )

    # 写回 advice-json
    if not args.no_rewrite_advice_json:
        # 默认行为：写一个增强副本到 output_dir，不覆盖原 advice_json
        target = output_dir / "current_market_advice_with_llm.json"
        _save_advice(advice, original_payload, target)
        logger.info("wrote enhanced advice to %s", target)
    else:
        # 显式覆盖原 advice-json
        _save_advice(advice, original_payload, advice_path)
        logger.info("rewrote original advice at %s", advice_path)

    # 写 review/explanation md
    review_md = _render_review_md(advice)
    if review_md:
        (output_dir / "llm_review.md").write_text(review_md, encoding="utf-8")
        logger.info("wrote %s/llm_review.md", output_dir)

    explanation_md = _render_explanation_md(advice)
    if explanation_md:
        (output_dir / "llm_explanation.md").write_text(explanation_md, encoding="utf-8")
        logger.info("wrote %s/llm_explanation.md", output_dir)

    # v2 — 工具化副驾驶审查（optional）
    strategy_review_meta = None
    if args.strategy and args.strategy != "none":
        spec = get_strategy(args.strategy)
        if spec is None:
            logger.warning(
                "unknown strategy: %s (available: %s) — skipping v2",
                args.strategy, ", ".join(list_strategies()) or "none",
            )
        else:
            logger.info(
                "[v2] running strategy review: %s (tool budget=%d)",
                args.strategy, args.tool_budget,
            )
            v2_review, tool_log = review_with_tools_ex(
                advice,
                strategy_name=args.strategy,
                tool_budget=max(0, args.tool_budget),
                enable_tools=(args.tool_budget > 0),
            )
            # 注意：v2 不修改 advice.llm_review（保持 v1 不变）
            v2_md = render_strategy_review_markdown(
                review=v2_review,
                advice=advice,
                strategy_name=args.strategy,
                tool_log=tool_log,
            )
            (output_dir / "llm_strategy_review.md").write_text(v2_md, encoding="utf-8")
            logger.info(
                "wrote %s/llm_strategy_review.md (tool_calls=%d)",
                output_dir, len(tool_log),
            )
            strategy_review_meta = {
                "strategy": args.strategy,
                "agreement": v2_review.agreement,
                "verdict": v2_review.verdict,
                "error": v2_review.error,
                "tokens": {
                    "in": v2_review.input_tokens or 0,
                    "out": v2_review.output_tokens or 0,
                },
                "toolCalls": len(tool_log),
            }
            # 把 v2 review + tool_log 写到独立 JSON 字段 — 不污染 v1 advice
            target = output_dir / "current_market_advice_with_llm.json"
            if target.exists():
                merged = json.loads(target.read_text(encoding="utf-8"))
            else:
                merged = dict(original_payload)
            # 尝试取 strategy 的 display_name（用户友好）
            from llm.strategies import get_strategy as _gs
            _spec = _gs(args.strategy)
            merged["llm_strategy_review"] = {
                "strategy": args.strategy,
                "displayName": _spec.display_name if _spec else args.strategy,
                "review": v2_review.__dict__,
                "toolCalls": tool_log,
            }
            target.write_text(
                json.dumps(merged, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    # 总结
    summary = {
        "plans": plans,
        "review": {
            "enabled": bool(advice.llm_review and advice.llm_review.enabled),
            "agreement": advice.llm_review.agreement if advice.llm_review else None,
            "verdict": advice.llm_review.verdict if advice.llm_review else None,
            "error": advice.llm_review.error if advice.llm_review else None,
            "tokens": {
                "in": advice.llm_review.input_tokens if advice.llm_review else 0,
                "out": advice.llm_review.output_tokens if advice.llm_review else 0,
            },
        },
        "explanation": {
            "enabled": bool(advice.llm_explanation and advice.llm_explanation.enabled),
            "error": advice.llm_explanation.error if advice.llm_explanation else None,
            "tokens": {
                "in": advice.llm_explanation.input_tokens if advice.llm_explanation else 0,
                "out": advice.llm_explanation.output_tokens if advice.llm_explanation else 0,
            },
        },
        "output_dir": str(output_dir),
    }
    if strategy_review_meta is not None:
        summary["strategy_review"] = strategy_review_meta
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
