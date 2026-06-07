"""
===================================
ETF 策略 YAML loader
===================================

我们的策略 YAML 是受限的、确定的格式：
  - 顶层都是 ``key: value`` 或 ``key: |`` 块
  - 数组用 ``[a, b, c]`` 简写（避免复杂缩进）
  - 块字符串（``|``）保留所有行

为什么不直接用 PyYAML：项目 requirements.txt 没有 yaml 依赖，
策略文件又很简单（5-6 个字段），写一个 50 行的解析器比加依赖划算。

风险：策略格式变更时需要同步更新 ``_parse_simple_yaml``。护栏：单元测试
覆盖正常 / 损坏 / 字段缺失 三种情况。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# 默认策略目录（相对项目根）
ROOT_DIR = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT_DIR / "strategies"


@dataclass
class StrategySpec:
    """单条 ETF 策略。"""

    name: str
    display_name: str
    description: str
    core_rules: List[int] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    instructions: str = ""


# ---------------------------------------------------------------------------
# Tiny YAML parser
# ---------------------------------------------------------------------------


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    """解析受限 YAML 格式。

    支持：
      - ``key: value``（标量）
      - ``key: [a, b, c]``（数组）
      - ``key:`` 后接 ``|`` 块（多行字符串）

    不支持：嵌套 mapping、复杂缩进、``>`` 折叠块、``&`` 锚点。
    遇到不支持格式时抛 ValueError，由 caller 兜底为 log warning。
    """
    out: Dict[str, Any] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip()

        # 跳过空行 / 注释
        if not stripped.strip() or stripped.lstrip().startswith("#"):
            i += 1
            continue

        # 必须是 ``key: value`` 形式（顶层的 key 不缩进）
        if line.startswith(" ") or line.startswith("\t"):
            raise ValueError(f"unexpected indent at line {i + 1}: {line!r}")

        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", stripped)
        if not m:
            raise ValueError(f"cannot parse line {i + 1}: {line!r}")
        key, rest = m.group(1), m.group(2).strip()

        # 块字符串 ``|``
        if rest == "|":
            block_lines: List[str] = []
            i += 1
            while i < len(lines):
                bl = lines[i]
                # 块必须缩进（>= 2 空格）
                if bl.startswith("  ") or bl.startswith("\t"):
                    block_lines.append(bl[2:] if bl.startswith("  ") else bl[1:].rstrip())
                    i += 1
                elif not bl.strip():
                    block_lines.append("")
                    i += 1
                else:
                    break
            # 去掉首尾空行
            while block_lines and not block_lines[0].strip():
                block_lines.pop(0)
            while block_lines and not block_lines[-1].strip():
                block_lines.pop()
            out[key] = "\n".join(block_lines)
            continue

        # 数组 ``[a, b, c]``
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            if not inner:
                out[key] = []
            else:
                out[key] = [_strip_quotes(x) for x in inner.split(",")]
            i += 1
            continue

        # 标量
        out[key] = _strip_quotes(rest)
        i += 1

    return out


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _coerce_strategy_dict(d: Dict[str, Any], source: Path) -> StrategySpec:
    """把 dict 转成 StrategySpec。字段缺失时 log warning 并用空串兜底。"""
    if "name" not in d or "instructions" not in d:
        raise ValueError(
            f"strategy file {source.name} missing required fields "
            "(need: name, instructions)"
        )
    core_rules_raw = d.get("core_rules", [])
    if not isinstance(core_rules_raw, list):
        core_rules_raw = []
    core_rules: List[int] = []
    for x in core_rules_raw:
        try:
            core_rules.append(int(str(x).strip()))
        except (ValueError, TypeError):
            continue

    required_tools_raw = d.get("required_tools", [])
    if not isinstance(required_tools_raw, list):
        required_tools_raw = []
    required_tools = [str(x).strip() for x in required_tools_raw if str(x).strip()]

    return StrategySpec(
        name=str(d.get("name", "")).strip(),
        display_name=str(d.get("display_name", "")).strip(),
        description=str(d.get("description", "")).strip(),
        core_rules=core_rules,
        required_tools=required_tools,
        instructions=str(d.get("instructions", "")).strip(),
    )


def load_strategies(strategy_dir: Optional[Path] = None) -> Dict[str, StrategySpec]:
    """从策略目录加载所有 .yaml 文件。

    损坏 / 缺字段的 yaml 会被 skip（log warning），不影响其他策略加载。
    """
    sdir = strategy_dir or STRATEGY_DIR
    if not sdir.exists():
        logger.warning("strategy dir not found: %s", sdir)
        return {}

    out: Dict[str, StrategySpec] = {}
    for path in sorted(sdir.glob("*.yaml")):
        try:
            text = path.read_text(encoding="utf-8")
            data = _parse_simple_yaml(text)
            spec = _coerce_strategy_dict(data, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("skip broken strategy file %s: %s", path.name, exc)
            continue
        if not spec.name:
            continue
        out[spec.name] = spec
    return out


def get_strategy(name: str, strategy_dir: Optional[Path] = None) -> Optional[StrategySpec]:
    """按 name 查单条策略。"""
    return load_strategies(strategy_dir).get(name)


def list_strategies(strategy_dir: Optional[Path] = None) -> List[str]:
    """返回所有可用策略名。"""
    return list(load_strategies(strategy_dir).keys())


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------


_JSON_SCHEMA_BLOCK = """【输出 JSON Schema】
{
  "verdict": "一句话总结（≤40 字）",
  "agreement": "agree" | "caution" | "disagree",
  "risks_blindspots": ["盲点/风险 1", "盲点/风险 2", ...],
  "reminder": "给执行者的一句提醒（≤40 字）"
}

agreement 含义：
- "agree": 同意系统建议，没什么需要警示
- "caution": 同意但有需要关注的风险点
- "disagree": 不建议按系统建议执行（极少见，只有当出现明显异常时）

risks_blindspots 是给执行者的"二次检查清单"，最多 3 条，每条 30 字内。
"""


_TOOL_PROTOCOL_BLOCK = """【工具调用协议】
你可以主动调用工具查询事实。协议：
  1. 在回答中输出 <tool_call>{"name": "工具名", "args": {...}}</tool_call>
  2. 系统会把工具结果以 tool_result({...}) 形式追加到对话
  3. 你可以基于工具结果继续调用或输出最终 JSON
  4. 最多 5 次工具调用（硬上限）

【可用工具】
- get_market_snapshot() — 查询 SPY/QQQ/VIX/CAPE/RSI 事实
- get_rule_engine_output() — 查询规则引擎当前判定
- get_recent_decisions(n_weeks=8) — 查询近 N 周决策历史
- search_macro_news(query) — 搜索宏观新闻（mock 实现，source="mock_offline"）

【调用时机建议】
- VIX >= 20：先 get_market_snapshot 再 get_rule_engine_output
- SPY 单日跌 > 2%：调 search_macro_news 查最近 24h 事件
- CAPE 月变动 > 3：调 search_macro_news("CAPE 收益率曲线")
- 系统 panic_tier=0 但 VIX>=26：调 get_recent_decisions(n_weeks=4) 看历史
"""


def build_strategy_system_prompt(spec: StrategySpec) -> str:
    """把 StrategySpec 渲染成 LLM system prompt。"""
    parts = [
        f"你是「{spec.display_name}」({spec.name})。",
        "",
        f"【策略描述】{spec.description}",
        "",
        f"【关联规则】core_rules = {spec.core_rules}",
        "",
        spec.instructions.strip(),
        "",
        _TOOL_PROTOCOL_BLOCK,
        "",
        _JSON_SCHEMA_BLOCK,
    ]
    return "\n".join(parts)
