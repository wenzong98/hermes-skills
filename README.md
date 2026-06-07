# hermes-skills

My [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill collection.

## Skills

### us-etf-quant-system

US ETF quantitative DCA system for S&P 500 (SPY) and Nasdaq-100 (QQQ) exposure.

**Features:**
- CAPE估值驱动五档定投规则
- RSI / VIX / SMA 趋势过滤
- 3年历史回测（2023-2026），默认前一收盘信号、下一开盘成交
- 可选 adjusted/total-return-aware 数据源，当前无 key/网络不可用时保留 price-return caveat
- unitized NAV 回撤、数据 manifest、pytest/CI
- 周频自动定投建议
- **可叠加 overlays** (opt-in)：cash APY accrual、slippage 模型、vol-targeting 平滑层（详见 [references/validation/P0_P1_implementation_summary.md](references/validation/P0_P1_implementation_summary.md)）

**Files:**
- `SKILL.md` — 完整策略说明
- `scripts/backtest_us_etf.py` — 回测引擎
- `scripts/current_market_advice.py` — 当日市场建议
- `references/backtest_3y_report.md` — 3年回测报告
- `references/strategy_rules.md` — 策略规则详解
- `references/signal_timing_contract.md` — 无前视信号/成交约定
- `references/strategy_spec_v1.json` — 独立验证器读取的机器可读策略契约
- `references/validation/dsr_report.md` — Deflated Sharpe + 多重检验报告
- `references/validation/regime_eval/regime_report.md` — Regime-aware 评估
- `references/validation/P0_P1_implementation_summary.md` — P0/P1 实施汇总

---

## ⚠️ 风险声明 (2026-06-06 重要更新)

**3y 回测的 23.91% XIRR 严重依赖 bull-market 行情，扩展到 6y 窗口（含 2020 COVID / 2022 熊市）后掉到 15.81%**，对应 annualized Sharpe 从 1.41 跌到 0.87。**默认的 3y 回测窗口不应该被解读为"策略预期收益"**。

**Regime-aware 评估（默认 5 个 regime，6y 窗口）** 揭示了真实画像：

| Regime | 策略 cum ret | 基准 cum ret | Alpha | Sharpe | 最大 DD |
|---|---|---|---|---|---|
| covid_crash | -23.35% | -25.42% | **+2.07%** | -2.35 | -29.10% |
| post_covid_melt_up | +93.26% | +109.90% | **-16.64%** | 2.17 | -10.05% |
| bear_2022 | -23.15% | -29.67% | **+6.52%** | -1.62 | -23.15% |
| rebound_2023 | +28.23% | +42.91% | **-14.68%** | 1.73 | -7.96% |
| ai_rally_2024_2025 | +55.80% | +75.31% | **-19.51%** | 1.35 | -17.38% |

**含义**：策略**在熊市跑赢基准 +2% ~ +7%，在牛市跑输 -14% ~ -20%**。Aggregate XIRR 数字掩盖了 regime-specific 的 alpha 模式 —— **报告里看到的 aggregate 数字大约高估牛市期收益 15-20pp**。

**Block bootstrap（200 次重抽样，21d block）** 显示实际路径在 90% 区间右尾之外：
- 5% 分位: $117,558
- 实际: **$1,367,961**（>95% 分位的 $499,001）
- 这意味着历史回测结果在 block-bootstrap 视角下处于**罕见的好区间**。

**DSR 警告**：当前 6y 窗口下 Deflated Sharpe Ratio 处于无法给出明确 verdict 的状态（trial grid 偏窄，详见 [references/validation/dsr_report.md](references/validation/dsr_report.md)）。**单一 aggregate XIRR 不可作为"策略 alpha 已证实"的依据**。

**实战建议**：
1. 用 6y+ 窗口看 XIRR，不要用 3y
2. 用 regime 拆分看 alpha 是否一致
3. 打开 cash APY 4.5% 显式建模（`--cash-apy 0.045`），目前 backtest 不算 cash sleeve 的利息，6y 隐含 +$30k 没在 aggregate 数字里
4. 打开 vol-targeting 平滑层（`--vol-target-lookback 63`），Sharpe 从 0.87 → 2.16，最大 DD 从 -29% → -25%
5. 任何 regime split **熊市跑赢 + 牛市跑输** 的策略都是 risk-aware DCA 的预期画像，aggregate XIRR 不应作为对外汇报的单一指标
