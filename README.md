# hermes-skills

My [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill collection.

## Skills

### us-etf-quant-system

US ETF quantitative DCA system for S&P 500 (SPY) and Nasdaq-100 (QQQ) exposure.

**Features:**
- CAPE估值驱动五档定投规则
- RSI / VIX / SMA 趋势过滤
- 3年历史回测（2022-2025）
- 周频自动定投建议

**Files:**
- `SKILL.md` — 完整策略说明
- `scripts/backtest_us_etf.py` — 回测引擎
- `scripts/current_market_advice.py` — 当日市场建议
- `references/backtest_3y_report.md` — 3年回测报告
- `references/strategy_rules.md` — 策略规则详解
