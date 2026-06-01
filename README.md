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

**Files:**
- `SKILL.md` — 完整策略说明
- `scripts/backtest_us_etf.py` — 回测引擎
- `scripts/current_market_advice.py` — 当日市场建议
- `references/backtest_3y_report.md` — 3年回测报告
- `references/strategy_rules.md` — 策略规则详解
- `references/signal_timing_contract.md` — 无前视信号/成交约定
- `references/strategy_spec_v1.json` — 独立验证器读取的机器可读策略契约
