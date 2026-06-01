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
- `references/cron_push_dedup_logic.md` — 工作日 9:00 推送、与前次建议去重、以及投递语义/手动发送坑点说明。
- `references/push_notification_workflow.md` — 中文推送模板的推荐结构、6 字段去重规则，以及为何用 Python 而不是 bash 处理含中文 JSON/Markdown。
- `references/push_content_improvements.md` — 推送内容增强建议：QDII 溢价执行层、自动定投 vs 模型建议差额、本次触发原因、阈值预警和周报/月报分层。

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

- Strategy final value: **$619,624.16**.
- Benchmark final value: **$649,631.40**.
- Strategy XIRR: **22.87%**.
- Benchmark XIRR: **25.72%**.
- Strategy unitized max drawdown: **-20.03%**; account-value drawdown: **-16.86%**.
- Benchmark unitized max drawdown: **-21.01%**; account-value drawdown: **-17.85%**.
- Strategy average cash: **5.21%**; ending cash: **14.85%**.
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
