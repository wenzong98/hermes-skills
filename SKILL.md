---
name: us-etf-quant-system
description: "Use when reviewing, running, or extending the US ETF quantitative DCA system for S&P 500/SPY and Nasdaq-100/QQQ exposure, including CAPE/RSI/VIX/SMA indicators, risk-aware weekly contribution rules, and reproducible 3-year backtests."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [us-etf, quant, backtesting, spy, qqq, cape, vix, dca]
    related_skills: [local-data-analysis-workflows]
---

# US ETF Quant System

## Overview

This skill packages the US ETF quantitative system designed for broad US equity exposure through S&P 500/SPY-like funds and Nasdaq-100/QQQ-like funds. It is a valuation- and risk-aware DCA framework rather than a short-term trading system.

The system combines:

- **Valuation anchor:** Shiller CAPE / S&P 500 valuation regime.
- **Trend anchor:** SPY price vs SMA200 and SMA50/SMA200 trend state.
- **Momentum / relative strength:** QQQ/SPY 63-trading-day relative return.
- **Risk anchor:** VIX level and RSI14 oversold/overbought state.
- **Execution model:** fixed weekly capital budget enters a cash reservoir; the strategy invests 0x-3x weekly budget based on regime and can use accumulated cash when panic/cheap conditions appear.

The included script and artifacts live in this skill folder:

- `scripts/backtest_us_etf.py` — reproducible data fetch + backtest engine.
- `scripts/current_market_advice.py` — request-time market diagnosis + portfolio-aware DCA/trim recommendation generator.
- `references/strategy_rules.md` — full reviewed and optimized rulebook.
- `references/current_advice_logic.md` — request-time advice field definitions and action mapping.
- `references/signal_timing_contract.md` — no-lookahead signal/execution contract for backtests and request-time advice.
- `references/strategy_spec_v1.json` — machine-readable contract consumed by the independent verifier.
- `references/backtest_3y_report.md` — latest 3-year backtest summary.
- `references/backtest_3y_results.json` — machine-readable metrics.
- `references/backtest_3y_equity_curve.csv` — daily equity curve.
- `references/backtest_3y_trades.csv` — simulated DCA/trim actions.
- `references/dashboard/index.html` — offline research dashboard combining daily advice, signal ranking, and backtest evidence, with a separate PIT/data-quality audit view.
- `references/joinquant_china_qdii_mapping.md` — how to map SPY/QQQ research into A股场内 QDII / LOF execution, including same-index ETF selection and premium-aware execution notes.
- `references/github_publishing_workflow.md` — current GitHub repo URL, first-time publishing commands, `gh repo create` pitfalls, and what to exclude from commits.
- `assets/backtest_3y_equity_curve.png` — equity curve chart.

Independent validation lives outside this skill at `~/.hermes/skills/research/us-etf-quant-system-verifier`. That verifier reads exported JSON/CSV/spec artifacts and must not import the production strategy module.

## When to Use

Use this skill when the user asks to:

- Review or explain the existing US ETF / 美股 ETF / 纳斯达克 + 标普 quantitative strategy.
- Generate a current SPY/QQQ allocation or weekly DCA multiplier.
- Backtest the strategy over the last 3 years or a custom window.
- Update CAPE/VIX/RSI/SMA based rules for US index funds.
- Convert the rules into an executable Hermes workflow or skill.

Do **not** use this as financial advice or a guaranteed trading signal. Treat outputs as systematic research and decision support.

## Quick Start

From the skill folder:

```bash
cd ~/.hermes/skills/research/us-etf-quant-system
python3 scripts/backtest_us_etf.py \
  --start 2023-05-29 \
  --end 2026-05-29 \
  --initial-capital 100000 \
  --weekly-budget 2000 \
  --execution-price next_open \
  --cape-lag-bdays 10 \
  --output-dir references/latest_run
```

Default data sources:

- SPY/QQQ daily OHLCV: Nasdaq public historical quote API.
- VIX: Cboe `VIX_History.csv`.
- CAPE: multpl.com Shiller PE monthly table.

Important data caveat: Nasdaq closes are price-return closes; dividends are excluded for both strategy and benchmark. This is acceptable for rule comparison but understates absolute long-run ETF total returns.

## Request-Time Advice Command

Generate a current market diagnosis and portfolio-aware action report:

```bash
cd ~/.hermes/skills/research/us-etf-quant-system
python3 scripts/current_market_advice.py \
  --portfolio-config ~/.hermes/portfolio_config.json \
  --recent-days 3 \
  --output-dir references/current_run
```

Outputs:

- `references/current_run/current_market_advice.md`
- `references/current_run/current_market_advice.json`
- `references/cron_push_dedup_logic.md` — 推送投递路径、dedupe 停用说明（2026-06-03）、手动推送方法及两个关键坑（cron stack 误判、`cmd[:-2]` 定时炸弹）。
- `references/push_notification_workflow.md` — 中文推送模板的推荐结构、6 字段去重规则，以及为何用 Python 而不是 bash 处理含中文 JSON/Markdown。
- `references/push_content_improvements.md` — 推送内容增强建议：QDII 溢价执行层、自动定投 vs 模型建议差额、本次触发原因、阈值预警和周报/月报分层。

## LLM 副驾驶（可选 — 方案 A 审查 + 方案 B 解释）

`current_market_advice.py` 内置可选的 LLM 副驾驶 hook，由 `--llm-copilot` 开启。LLM **只读不写**：审查规则引擎的输出（方案 A）、把人话翻译规则（方案 B），不参与 `dca_multiplier` 或 `new_buy_*_weight_pct` 的计算。LLM 失败时主推送照常发，产物只追加 `llm_review` / `llm_explanation` 字段，不动原始 payload。

**典型用法（开启副驾驶）：**

```bash
cd ~/.hermes/skills/research/us-etf-quant-system
python3 scripts/current_market_advice.py \
  --portfolio-config ~/.hermes/portfolio_config.json \
  --llm-copilot \
  --output-dir references/current_run
```

副驾驶额外产出：

- `references/current_run/llm_review.md` — 方案 A 推送副标题（verdict + 立场 + 风险盲点 + 一句提醒）
- `references/current_run/llm_explanation.md` — 方案 B 推送副标题（人话版规则理由，3-4 句）
- 原始 `current_market_advice.json` 增加 `llm_review` 和 `llm_explanation` 字段（默认；用 `--no-llm-rewrite` 可不合并）

**只跑其中一个方案：**

```bash
# 只跑方案 A（副驾驶审查）
python3 scripts/current_market_advice.py --llm-copilot --llm-plans review ...

# 只跑方案 B（rationale 解释）
python3 scripts/current_market_advice.py --llm-copilot --llm-plans explain ...
```

**环境变量：**

| 变量 | 默认 | 说明 |
|------|------|------|
| `LLM_API_KEY` | （空） | LLM API key。无 key 且非 mock 时副驾驶跳过。 |
| `LLM_BACKEND` | `anthropic` | `anthropic` / `openai` (兼容协议，含 DeepSeek/MiniMax/OpenAI/智谱) / `mock` |
| `LLM_MODEL` | `claude-3-5-haiku-latest` | 模型名 |
| `LLM_BASE_URL` | （默认官方） | OpenAI 兼容模式必填 |
| `LLM_TIMEOUT_S` | `20` | 单次超时秒数 |
| `LLM_MAX_RETRIES` | `2` | 重试次数 |
| `LLM_USAGE_LOG` | `references/llm_usage.jsonl` | 每次调用的 token 用量追加到 jsonl |

**离线模式（测试 / CI / 预览推送内容）：**

```bash
LLM_BACKEND=mock python3 scripts/llm_copilot.py \
  --advice-json references/current_run/current_market_advice.json \
  --output-dir /tmp/llm_preview
```

**重要护栏：**

1. **LLM 零决策权** — `dca_multiplier`、`new_buy_*_weight_pct` 永远由规则引擎计算，LLM 不能改
2. **失败兜底** — LLM 调用超时 / 解析失败 / API 报错时，主报告推送照常发，LLM 字段填 fallback 文案 + `error` 字段
3. **强 PIT** — 给 LLM 的事实数据全部是 T-1 收盘后快照，不含未来信息
4. **JSON 输出解析** — 严格 JSON Schema，宽松解析（去掉 ```json 包裹、抓首尾 {}）
5. **agreement 强约束** — LLM 输出 `disagree` 时不修改决策，只在 `reminder` 字段提示人工核查

**Token 成本（单次周报）：**

| 方案 | 输入 | 输出 | 估计成本 |
|------|------|------|------|
| 方案 A 副驾驶审查 | ~1.5k | ~200 | < ¥0.02 / 次 |
| 方案 B rationale 解释 | ~750 | ~200 | < ¥0.01 / 次 |
| 方案 A + B 合计 | ~2.2k | ~400 | < ¥0.03 / 次 |

按周跑 → 一年约 ¥1.5；按月跑 → 几乎免费。

**模块结构：**

- `llm/schema.py` — `WeeklyAdvice` 结构化输出契约（`LLMReview` + `LLMExplanation`）
- `llm/client.py` — 极简 LLM 客户端（anthropic / openai / mock），超时 + 重试 + 用量日志
- `llm/advisor.py` — `review_signal(advice)` + `explain_decision(advice)`，prompt 模板与解析
- `scripts/llm_copilot.py` — CLI 入口，独立跑副驾驶
- `tests/test_llm_copilot.py` — 16 个离线测试（mock 后端）

## LLM 副驾驶 v2（可选 — 工具化 + ETF 策略 YAML）

v2 在 v1 的"喂数据"基础上，让 LLM **主动调用工具** 拉取事实数据（市场快照 / 规则引擎输出 / 决策历史 / 宏观新闻），再生成审查结论。**LLM 仍然只读** — 工具返回值不修改任何 `dca_multiplier` 或权重。

**v1 vs v2 差异：**

| 维度 | v1 静态 | v2 工具化 |
|---|---|---|
| 数据流 | 我们喂给 LLM | LLM 主动拉 |
| 工具调用 | 0 | 最多 5 次（硬上限） |
| 审查依据 | 我们提供的事实 + 规则 | LLM 拉取的事实 + 主动新闻核查 |
| 透明度 | 输出 verdict + risks | 同左 + 工具调用历史 |
| Token 成本 | ~¥0.03 | ~¥0.10-0.20 |

**v2 典型用法：**

```bash
cd ~/.hermes/skills/research/us-etf-quant-system
LLM_BACKEND=mock python3 scripts/llm_copilot.py \
  --advice-json references/current_run/current_market_advice.json \
  --output-dir references/current_run \
  --plans review,explain \
  --strategy etf_macro_regime \
  --tool-budget 5
```

**可用策略（`strategies/*.yaml`）：**

| 策略 | 触发场景 | 工具 |
|---|---|---|
| `etf_macro_regime` | 任何周报（CAPE/VIX/趋势审查） | get_market_snapshot + get_rule_engine_output + search_macro_news |
| `etf_panic_ladder` | VIX 接近 28/35/45 阶梯 | get_market_snapshot + get_recent_decisions + search_macro_news |

新增策略：在 `strategies/` 下放 YAML，运行 `llm_strategies.list_strategies()` 即可看到。

**v2 额外产出：**

- `references/current_run/llm_strategy_review.md` — v2 推送副标题（策略名 + verdict + 风险 + 工具调用历史）
- 合并 JSON 增加 `llm_strategy_review` 字段：
  ```json
  {
    "strategy": "etf_macro_regime",
    "displayName": "ETF 宏观周期审查",
    "review": {... LLMReview fields ...},
    "toolCalls": [{"name", "args", "resultPreview"}, ...]
  }
  ```

**工具调用协议（轻量版）：**

- LLM 输出 `<tool_call>{"name": "工具名", "args": {...}}</tool_call>`
- 我们执行工具，把结果以 `tool_result(name): ```json\n{...}\n```` 形式追加
- 最多 5 次调用（`ToolBudget(max_calls=5)`）
- 失败兜底：工具抛异常 → 记录到 `tool_errors` → 继续循环
- 工具调用历史 + 工具错误都会出现在 `llm_strategy_review.md` 和 dashboard

**v2 工具：**

1. `get_market_snapshot()` — 最新 SPY/QQQ/VIX/CAPE 事实（PIT 快照）
2. `get_rule_engine_output()` — 规则引擎当前判定（regime / multiplier / 权重 / panic_tier）
3. `get_recent_decisions(n_weeks=8)` — 近 N 周决策历史
4. `search_macro_news(query)` — 宏观新闻搜索（详见下节）

**模块结构（v2 增量）：**

- `llm/tools.py` — 4 个只读工具 + `ToolBudget` + `execute_tool_call()`
- `llm/strategies.py` — YAML loader（无 PyYAML 依赖）
- `llm/advisor.py` — `review_with_tools_ex()` 返回 `(LLMReview, tool_log)`
- `strategies/etf_*.yaml` — 策略定义
- `tests/test_llm_tools.py` / `test_llm_strategies.py` / `test_llm_copilot_v2.py` — 共 51 个测试

## 新闻搜索：Tavily / SerpAPI（实接）

v2 工具 `search_macro_news` 实接 **Tavily**（主）+ **SerpAPI**（fallback）+ **mock**（最终兜底）。失败链路：Tavily HTTP 错误 → SerpAPI → 7 条硬编码 mock 新闻 → `[]`。

**环境变量：**

| 变量 | 默认 | 说明 |
|------|------|------|
| `TAVILY_API_KEY` | （空） | Tavily API key。免费档 1000 次/月。 |
| `SERPAPI_KEY` | （空） | SerpAPI API key。免费档 100 次/月。 |
| `NEWS_TIMEOUT_S` | `5` | 单次超时秒数 |
| `NEWS_MAX_RESULTS` | `5` | 单次最大返回数（硬上限 10） |

**两个都缺时：** 自动用 mock（7 条硬编码假新闻，标 `source=mock_offline`），让 LLM 仍能工作。

**API key 配置（推荐用 .env 文件）：**

```bash
# 复制模板
cp .env.example .env
# 编辑 .env 填入真实 key（.env 已在 .gitignore 中）
```

或直接 export：
```bash
export TAVILY_API_KEY=tvly-...
export SERPAPI_KEY=...
```

**安全护栏：**

- 全部失败时返回 `[]`，LLM 工具调用循环不崩
- `source` 字段明确标识 provider — LLM 不会把 mock 当真新闻
- query 清洗：剥控制字符 + 截断到 200 字符
- 每次调用 token 计入 `references/llm_usage.jsonl`
- **Tavily `search_depth` 必须用 `basic` / `fast` / `advanced` / `ultra-fast` 之一** — `news` 是无效值（实测 422）

## 宏观参考内容（Dashboard 旁路 — RSS / GDELT / 财经日历）

与 Tavily/SerpAPI 不同：**不进 LLM 工具调用**，仅作为 dashboard 旁路展示。

| 来源 | 用途 | 频率 | 缓存 |
|---|---|---|---|
| RSS (Reuters/Bloomberg/MarketWatch) | 财经头条 | 实时 | 6h TTL |
| GDELT 2.0 doc API | 全球事件流（rate-limit 严格，graceful degrade） | 24h TTL | 24h |
| ForexFactory 公开 JSON 镜像 | 经济日历（CPI/NFP/FOMC 等） | 6h TTL | 6h |
| Federal Reserve 公开日历 | FOMC 会议 + Fed 官员讲话 | 6h TTL | 6h |

**LLM 中文翻译（仅当 LLM_API_KEY 存在时）：**
- RSS 标题 + 财经日历事件名批量翻译为中文
- 失败兜底英文
- 双语显示：中文作主标题 + 英文作 `title=` 悬停 tooltip
- 单次成本：~¥0.002/批

**约束：**

- **只缓存 headline + url + ts**（不缓存正文 — 合规 + 版权）
- 缓存路径：`references/data_cache/macro_feeds/`，按 `<source>_<query>.json` 命名
- 失败 → 空列表 + log warning（绝不抛到 dashboard）
- GDELT rate-limit 写空 cache 防止重试风暴（24h TTL）

**启用方式：**

`scripts/build_dashboard.py` 自动调用 `_macro_feeds_block()` 拉取，无需额外 CLI 标志。Dashboard 在 LLM 副驾驶卡片下方显示独立的 "宏观参考内容" 卡片，三栏布局：RSS / GDELT / 日历。

## Dashboard

Open `references/dashboard/index.html` for the offline research dashboard. It combines decision, signal, and backtest evidence under `#overview`, keeps `#data-quality` as the audit view, and bundles ECharts under `references/dashboard/vendor/`.

Run the complete daily production flow in order with:

```bash
python3 scripts/run_daily_pipeline.py
```

The pipeline stops on the first failed step and runs: update market databases → generate strict advice → archive the daily decision → rerun the strict backtest → build the dashboard. Daily decision snapshots are stored in `data/decisions.db` and drive the dashboard decision calendar.

Real executions are deliberately separate from model advice and backtest trades. Initialize or record them with:

```bash
python3 scripts/trade_ledger.py init
python3 scripts/trade_ledger.py record \
  --executed-at 2026-06-05T09:35:00-04:00 \
  --account brokerage --ticker QQQ --side BUY \
  --quantity 2 --price 715.25 --fee 1.00 --order-id order-123
python3 scripts/trade_ledger.py list
```

The independent real-execution ledger is `data/trades.db`. Never insert model recommendations or backtest trades into it automatically.

When changing the dashboard, edit `scripts/_dashboard_template.html` and, if display fields are needed, `scripts/build_dashboard.py`; then rebuild with:

```bash
python3 scripts/build_dashboard.py --no-open
```

Do not manually edit `references/dashboard/index.html` for durable changes because it is generated from the template and normalized `data.json`.

## Portfolio Config Conventions

When the user has multiple RMB auto-DCA legs across banks / brokerages, keep `~/.hermes/portfolio_config.json` aligned with the *actual execution schedule* instead of collapsing it into a single legacy weekly plan.

Recommended shape:

- `plan.amount` — **weekly base budget** used by `current_market_advice.py` when converting signals into a concrete RMB recommendation. This must be the weekly-equivalent total across all active auto-DCA legs.
- `plan.weekly_total` — explicit duplicate of the same weekly-equivalent total for readability.
- `plan.items[]` — each real-world auto-DCA leg with `target`, `amount`, `frequency`, `weekly_equivalent`, `platform`, optional `status` (`active` / `paused`), and optional `note`.
- Paused standing orders should be kept in `plan.items[]` with `status: "paused"` and `weekly_equivalent: 0` so historical intent stays visible while `plan.amount` reflects only active cash flow.
- `plan.baseline_buy_weights_pct` — descriptive baseline split implied by the active real auto-DCA schedule before any tactical overlay.
- `strategy.base_weekly_budget` — should match `plan.amount` so request-time advice and strategy docs stay consistent.

Example: if the user invests RMB 1000 every Thursday into SPY-like exposure, RMB 500 every weekday into QQQ-like exposure, and another RMB 1000 every Thursday into QQQ-like exposure, the weekly-equivalent base budget is RMB 4500, not RMB 2000.

Use the strategy engine's `weekly base budget` as the advisory baseline, then explain that any mismatch between the systematic recommendation and the broker's standing orders should be interpreted as a signal for manual top-up / reduction / temporary pause, not as proof that the model can directly rewrite the bank's automation.

## Current Production Rule Summary

### 1. DCA multiplier from valuation

The base weekly DCA multiplier is set by Shiller CAPE:

- CAPE < 22: **2.0x** deep value.
- 22 ≤ CAPE < 25: **1.5x** cheap.
- 25 ≤ CAPE < 30: **1.25x** fair.
- 30 ≤ CAPE < 35: **1.0x** elevated but acceptable.
- 35 ≤ CAPE < 38: **0.75x** expensive.
- 38 ≤ CAPE < 42: **0.75x** very expensive but still trend-aware.
- CAPE ≥ 42: **0.5x** extreme valuation baseline.

This intentionally improves the older rule that fully paused above CAPE 38. The 2023-2026 regime showed that valuation-only pauses can accumulate excessive cash during a trend-confirmed bull market.

### 2. Trend and volatility overlays

- If SPY is below SMA200 and there is no panic/oversold confirmation, cap DCA at **0.5x**.
- If VIX ≥ 25 and market is not oversold, cap DCA at **0.5x**.
- If RSI14 ≥ 70 and CAPE ≥ 35, cap DCA at **0.75x**.
- If SPY is above SMA200, VIX < 20, QQQ/SPY relative strength is positive, and CAPE ≥ 35, enforce a **minimum 0.75x** DCA so valuation slows but does not fully stop participation.
- Scheme 8 panic ladder: Tier 1 **1.25x** (drawdown ≤ -8% + RSI ≤ 40, or VIX ≥ 28 + RSI ≤ 40), Tier 2 **1.5x** (drawdown ≤ -15% or VIX ≥ 35), Tier 3 **2.0x** (drawdown ≤ -22% or VIX ≥ 45), with a falling-knife guard when trend/momentum are still deteriorating.
- Cash-reservoir cap: if model cash exceeds **20%-30%** while SPY remains above SMA200 and VIX < 25, lift the DCA floor to **0.75x-1.0x** to avoid long-term cash drag.

### 3. SPY/QQQ new-buy weights

New buys use Scheme 11B core-satellite allocation instead of a full-account rotation:

- 80% core sleeve: fixed SPY 40% / QQQ 40%.
- 20% satellite sleeve: driven by QQQ/SPY 63d/126d relative strength, QQQ vs SMA200, VIX, valuation heat, and panic tier.
- Satellite to SPY: total SPY 60% / QQQ 40%.
- Satellite to QQQ: total SPY 40% / QQQ 60%.
- Mild QQQ strength: total SPY 45% / QQQ 55%.
- Neutral: total SPY 50% / QQQ 50%.

### 4. Stop-profit / risk trims

The older v2 implementation sold every day while a negative signal persisted, which could create unrealistic liquidation behavior. The optimized system throttles sells:

- Trims are at most **once per calendar month**.
- CAPE ≥ 42 and RSI14 ≥ 75: sell 3% of QQQ exposure as a micro-trim.
- CAPE ≥ 40 and RSI14 ≥ 78: sell 3% of QQQ exposure as profit lock.
- SPY below SMA200 and VIX ≥ 30: sell 10% of QQQ exposure as risk-off trim.

For fund execution, treat trims as alerts requiring confirmation unless the user explicitly wants automation.

## Backtest Methodology

The script models a fixed weekly capital budget:

1. Initial capital is invested on the first tradable execution row after a valid prior signal exists.
2. By default, indicators from a completed close are used only from the next trading bar onward; default execution is next open.
3. Monthly CAPE observations are delayed by 10 business days before they become available to daily signals.
4. Every week, the weekly budget enters cash on the first trading day on/after Thursday.
5. Strategy invests `weekly_budget × multiplier`, capped by available cash.
6. The multiplier includes valuation, trend/VIX caps, Scheme 8 panic ladder, and cash-reservoir floor control.
7. New buys use the 80/20 core-satellite split; only the satellite sleeve rotates.
8. Benchmark invests exactly 1x weekly budget into static 50/50 SPY/QQQ.
9. Metrics use cash-flow-aware daily returns and XIRR because there are repeated contributions.

Primary metrics:

- Final value and profit vs contributed capital.
- XIRR / money-weighted annualized return.
- Unitized max drawdown for comparable drawdown; account-value drawdown is still reported as a user-experience metric.
- Volatility, Sharpe, Sortino, win rate. Sharpe/Sortino use the configured annual risk-free rate.
- Average and ending cash reservoir.

## Latest 3-Year Backtest Snapshot

Window: **2023-05-31 → 2026-05-29** using $100,000 initial capital, $2,000 weekly budget, previous-close signals, next-open execution, and 10-business-day CAPE availability lag. The packaged run still uses Nasdaq price-return data because Yahoo/Alpha adjusted providers are optional and not active in this run.

- Strategy final value: **$630,424.43**.
- Benchmark final value: **$661,267.00**.
- Strategy XIRR: **23.91%**.
- Benchmark XIRR: **26.80%**.
- Strategy unitized max drawdown: **-19.89%**; account-value drawdown: **-16.75%**.
- Benchmark unitized max drawdown: **-20.83%**; account-value drawdown: **-17.70%**.
- Strategy average cash: **5.01%**; ending cash: **14.58%**.
- Latest signal from 2026-05-28 for 2026-05-29 execution: **very_expensive**, **0.75x DCA**, new-buy split **SPY 40% / QQQ 60%**.

Interpretation: in a strong high-valuation bull market, the risk-aware strategy trails the 50/50 fully-invested benchmark, but it keeps a cash reservoir and slightly reduces drawdown. This is expected behavior, not a bug. If the user explicitly prioritizes maximum bull-market capture, increase the trend-confirmed minimum from 0.75x to 1.0x.

## Common Pitfalls

1. **Using simple CAGR with recurring contributions.** Use XIRR or cash-flow-adjusted returns.
2. **Using same-day close signals and same-day close execution.** This is kept only as `--execution-price same_close` for research comparison and is marked as lookahead.
3. **Selling every day while a risk signal persists.** Trims must be throttled by month or regime.
4. **Treating CAPE as a tactical timing signal.** CAPE is slow-moving; use it as a DCA throttle, not a daily exit trigger.
5. **Ignoring trend confirmation in expensive markets.** High CAPE can persist for years; trend and VIX prevent premature full pauses.
6. **Comparing price-return data to total-return expectations.** Nasdaq closes exclude dividends. Comparison is fair within the script because both strategy and benchmark use the same price data, but absolute returns are understated.
7. **Double-charging ETF expense ratios.** ETF prices already embed fund expenses; only transaction cost is modeled separately.
8. **Mixing research tickers with execution tickers.** For Chinese users, SPY/QQQ often describe the research layer while actual execution happens via A股场内 QDII / LOF proxies. Separate the “index view” from the “which domestic fund to buy” decision.
9. **Ignoring QDII premium / quota distortions.** Same-index domestic ETFs can diverge materially because of申购赎回限制、外汇额度、节假日和场内溢价. When advice is meant to be actionable in RMB channels, add an execution layer that checks premium, liquidity, and availability.
10. **Publishing runtime noise to GitHub.** When exporting this skill to the user's `wenzong98/hermes-skills` repository, keep durable skill assets but avoid Python caches and transient cron/current-run state unless the user explicitly asks to archive a snapshot. See `references/github_publishing_workflow.md`.
11. **Pushing `current_market_advice.py` with stripped flags from the cron wrapper.** When the Telegram push wrapper (`~/.hermes/scripts/us_etf_current_advice_push.py`) reuses a base `strict_cmd` list and slices it to build a second invocation (e.g. `strict_cmd[:-2] + ["--output-dir", ...]` for the legacy `cron_run` copy), the slice can silently drop a paired flag+value such as `--cape-vintage-path <path>`. With `--require-adjusted` set, `prepare_dataset` then aborts with `--require-adjusted is set but no --cape-vintage-path was provided`, taking the whole push down. Rule: when a cron wrapper reuses a flag set, spell out the full flag list in the second call rather than slicing — `cmd[:-2]` looks innocent but is a time bomb. If the push ever fails with that exact RuntimeError, the wrapper is the first place to inspect.

## Verification Checklist

- [ ] Run `python3 scripts/backtest_us_etf.py --start <date> --end <date> --output-dir <dir>` successfully.
- [ ] Confirm output JSON contains `strategy`, `benchmark`, `relative`, and `latest_signal` sections.
- [ ] Confirm output JSON meta contains `strategy_version`, `git_commit`, `script_sha256`, `data_snapshot_sha256`, `signal_timing`, and `execution_price`.
- [ ] Confirm default trades have `signal_date < date`; only `--execution-price same_close` may have equal dates and must carry `lookahead_warning`.
- [ ] Confirm equity curve starts after SMA200 warm-up and uses the latest common SPY/QQQ market date.
- [ ] Confirm benchmark and strategy receive the same external weekly budget.
- [ ] Confirm latest signal reason includes CAPE, RSI/VIX overlays, and target SPY/QQQ weights.
- [ ] If changing rules, rerun the 3-year backtest and update `references/backtest_3y_report.md` and `references/backtest_3y_results.json`.

## Operational Cadence

This section captures the minimum recurring-maintenance rituals that keep the strategy PIT-correct, the verifier green, and the production paths free of stale data.

### Daily Cron Pipeline (LLM 副驾驶 v2 集成)

每天早上 9 点（Mon-Sat）的 LaunchAgent `com.hermes.usetf-daily-refresh` 触发 `~/.hermes/scripts/usetf-daily-refresh.sh`，按以下顺序跑：

```
[step 1/3] run_daily_pipeline.py       # market DBs → advice → backtest → dashboard
[step 2/3] us_etf_llm_copilot.sh       # LLM 副驾驶 v1 (review/explain) + v2 (etf_macro_regime)
[step 3/3] us_etf_current_advice_push.py  # Telegram 推送
```

**LLM 副驾驶步骤（`us_etf_llm_copilot.sh`）的细节：**

1. **加载 env** — `source ~/.hermes/.env` 注入 `TAVILY_API_KEY` / `SERPAPI_KEY` / `LLM_API_KEY`
2. **决定 backend** — `LLM_API_KEY` 缺失则自动 `LLM_BACKEND=mock`（0 远程调用，仅本地审查）
3. **找最新 advice** — 优先 `current_run_strict/`，fallback `current_run/`
4. **去重** — 如果 state 文件 signature 与当前 advice 一致，跳过整个 LLM 步骤（**省 token**）；per-output 再检查 `with_llm.json` mtime
5. **跑 LLM** — 对 `current_run_strict/` 和 `cron_run/` 两个目录各跑一次
   - v1: `review` + `explain`（~2k 输入 + 400 输出，~¥0.03）
   - v2: `etf_macro_regime` 工具化（最多 5 次工具调用，~¥0.10-0.20）
6. **重建 dashboard** — 让 v2 + macro feeds 出现在浏览器

**成本估算（按真实 key）：**

| 模式 | 每次 | 一年（按 250 工作日） |
|---|---|---|
| 全 mock（无 key） | ¥0 | ¥0 |
| v1 + v2 全套（anthropic） | ~¥0.15-0.25 | ~¥40-60 |
| 去重命中（不重跑） | ¥0 | ¥0 |

实际会比估算低，因为周一周五（regime 不变）经常命中 dedup。

**API key 配置：**

```bash
# 推荐：把 key 加到 ~/.hermes/.env（不与项目代码混合）
echo 'TAVILY_API_KEY=tvly-...' >> ~/.hermes/.env
echo 'SERPAPI_KEY=...' >> ~/.hermes/.env
echo 'LLM_API_KEY=sk-ant-...' >> ~/.hermes/.env

# 或用项目级 .env（已 .gitignore）
cp .env.example .env
vim .env
```

**日志：**

- `~/.hermes/cron/usetf-daily-refresh.log` — 整个 pipeline
- `~/.hermes/cron/usetf-llm-copilot.log` — LLM copilot 步骤
- `references/llm_usage.jsonl` — token 用量 jsonl

**手动触发（调试）：**

```bash
bash ~/.hermes/scripts/us_etf_llm_copilot.sh
# 或强制跳过去重（重新跑 LLM）
touch ~/.hermes/skills/research/us-etf-quant-system/references/cron_run/.advice_state.json
bash ~/.hermes/scripts/us_etf_llm_copilot.sh
```

### CAPE Vintage Refresh

The CAPE vintage file (`references/cape_vintage.csv`) is the source of truth for `--cape-vintage-path` users and for the `assert_latest_cape_pit` runtime check in `current_market_advice.py`. It must be refreshed at least monthly because:

- Yale Shiller's `ie_data.xls` is updated roughly every month-end.
- multpl.com is more frequent but only used as a research fallback since v1.3.0.
- Without a fresh refresh, `available_at` lags behind the calendar and the request-time PIT assertion can over-aggressively abort runs that are still PIT-correct.

Refresh command:

```bash
python3 scripts/update_cape_snapshot.py --output references/data_cache/cape_vintage.csv
# It is also copied to references/cape_vintage.csv as the canonical delivery path.
```

Recommended cadence: run on the 5th business day of each month (after Yale's typical publication window). Pair it with a cron entry or scheduled workflow.

### Strict Replay CI

`.github/workflows/strict_replay.yml` checks out both this repository and the verifier at the same SHA and runs:

```bash
python verifier/replay_strategy.py \
  --main-repo ../us-etf-quant-system \
  --strict-total-return \
  --strict-cape-vintage
```

A failing run blocks merge. The verifier's `test_verifier_does_not_import_production_strategy_code` enforces that this remains an independent re-implementation, not a wrapper around production code.

### Trim State File Location

The persistent QQQ trim state lives at `references/cron_run/.trim_state.json` (gitignored). A symlink at `~/.hermes/us_etf_trim_state.json` points to the new location for backward compatibility with existing cron entries. To migrate a cron job, prefer the new path; the symlink is only a soft bridge.

### `--require-adjusted` Production Gate

When running with `--require-adjusted` (recommended for any non-research, total-return production run), the system now refuses to start unless both:

1. The actual SPY/QQQ data source is dividend-adjusted (Tiingo, Yahoo chart, or Alpha Vantage adjusted).
2. A CAPE vintage path is supplied via `--cape-vintage-path`.

The 10-business-day multpl/yale fallback is research-only and intentionally not allowed in `--require-adjusted` mode, because it lacks the PIT-correct `available_at` enforcement that production runs need.
