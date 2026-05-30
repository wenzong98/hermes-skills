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
- `references/backtest_3y_report.md` — latest 3-year backtest summary.
- `references/backtest_3y_results.json` — machine-readable metrics.
- `references/backtest_3y_equity_curve.csv` — daily equity curve.
- `references/backtest_3y_trades.csv` — simulated DCA/trim actions.
- `references/joinquant_china_qdii_mapping.md` — how to map SPY/QQQ research into A股场内 QDII / LOF execution, including same-index ETF selection and premium-aware execution notes.
- `assets/backtest_3y_equity_curve.png` — equity curve chart.

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

1. Initial capital is invested on the first backtest date according to the strategy's target split.
2. Every week, the weekly budget enters cash on the first trading day on/after Thursday.
3. Strategy invests `weekly_budget × multiplier`, capped by available cash.
4. The multiplier includes valuation, trend/VIX caps, Scheme 8 panic ladder, and cash-reservoir floor control.
5. New buys use the 80/20 core-satellite split; only the satellite sleeve rotates.
6. Benchmark invests exactly 1x weekly budget into static 50/50 SPY/QQQ.
7. Metrics use cash-flow-aware daily returns and XIRR because there are repeated contributions.

Primary metrics:

- Final value and profit vs contributed capital.
- XIRR / money-weighted annualized return.
- Max drawdown.
- Volatility, Sharpe, Sortino, win rate.
- Average and ending cash reservoir.

## Latest 3-Year Backtest Snapshot

Window: **2023-05-30 → 2026-05-28** using $100,000 initial capital and $2,000 weekly budget.

- Strategy final value: **$615,985.99**.
- Benchmark final value: **$647,531.02**.
- Strategy XIRR: **22.54%**.
- Benchmark XIRR: **25.55%**.
- Strategy max drawdown: **-16.48%**.
- Benchmark max drawdown: **-17.82%**.
- Strategy average cash: **5.25%**; ending cash: **15.57%**.
- Latest signal on 2026-05-28: **extreme_valuation**, **0.75x DCA**, new-buy split **SPY 60% / QQQ 40%**.

Interpretation: in a strong high-valuation bull market, the risk-aware strategy trails the 50/50 fully-invested benchmark, but it keeps a cash reservoir and slightly reduces drawdown. This is expected behavior, not a bug. If the user explicitly prioritizes maximum bull-market capture, increase the trend-confirmed minimum from 0.75x to 1.0x.

## Common Pitfalls

1. **Using simple CAGR with recurring contributions.** Use XIRR or cash-flow-adjusted returns.
2. **Selling every day while a risk signal persists.** Trims must be throttled by month or regime.
3. **Treating CAPE as a tactical timing signal.** CAPE is slow-moving; use it as a DCA throttle, not a daily exit trigger.
4. **Ignoring trend confirmation in expensive markets.** High CAPE can persist for years; trend and VIX prevent premature full pauses.
5. **Comparing price-return data to total-return expectations.** Nasdaq closes exclude dividends. Comparison is fair within the script because both strategy and benchmark use the same price data, but absolute returns are understated.
6. **Double-charging ETF expense ratios.** ETF prices already embed fund expenses; only transaction cost is modeled separately.
7. **Mixing research tickers with execution tickers.** For Chinese users, SPY/QQQ often describe the research layer while actual execution happens via A股场内 QDII / LOF proxies. Separate the “index view” from the “which domestic fund to buy” decision.
8. **Ignoring QDII premium / quota distortions.** Same-index domestic ETFs can diverge materially because of申购赎回限制、外汇额度、节假日和场内溢价. When advice is meant to be actionable in RMB channels, add an execution layer that checks premium, liquidity, and availability.

## Verification Checklist

- [ ] Run `python3 scripts/backtest_us_etf.py --start <date> --end <date> --output-dir <dir>` successfully.
- [ ] Confirm output JSON contains `strategy`, `benchmark`, `relative`, and `latest_signal` sections.
- [ ] Confirm equity curve starts after SMA200 warm-up and uses the latest common SPY/QQQ market date.
- [ ] Confirm benchmark and strategy receive the same external weekly budget.
- [ ] Confirm latest signal reason includes CAPE, RSI/VIX overlays, and target SPY/QQQ weights.
- [ ] If changing rules, rerun the 3-year backtest and update `references/backtest_3y_report.md` and `references/backtest_3y_results.json`.
