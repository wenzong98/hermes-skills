# US ETF Quant System — 3Y Backtest Report

## Window and assumptions
- Strategy version: 1.3.0-rc
- Window: 2006-01-04 → 2026-05-29 (5132 trading days)
- Signal/execution: previous completed close signal with execution at next open
- CAPE availability lag: 10 business days
- Price source: requested yahoo_chart_adjusted / actual SPY yahoo_chart_adjusted / actual QQQ yahoo_chart_adjusted
- Adjusted for dividends: True; price-return only: False
- CAPE source: vintage_file:cape_vintage.csv
- Contribution schedule: first trading day on/after Thursday in each ISO week
- Risk-free rate for Sharpe/Sortino: 0.00%
- Initial capital: $100,000.00
- Weekly capital budget: $2,000.00
- Transaction cost: 0.15%
- Benchmark: 50/50 SPY/QQQ buy-and-hold plus weekly 1x budget
- Data caveat: if `price_return_only` is true, dividends are still excluded for both strategy and benchmark.

## Headline results
- Strategy final value: $10,647,017.90
- Benchmark final value: $16,631,809.38
- Strategy profit vs contributed capital: $8,417,017.90 (377.44%)
- Benchmark profit vs contributed capital: $14,401,809.38 (645.82%)
- Relative final value difference: $-5,984,791.49
- Strategy XIRR: 12.97%
- Benchmark XIRR: 16.34%
- XIRR difference: -3.38%
- Strategy unitized max drawdown: -49.61%; account-value drawdown: -32.83%
- Benchmark unitized max drawdown: -53.84%; account-value drawdown: -39.70%
- Strategy Sharpe / Sortino: 0.701 / 0.872
- Benchmark Sharpe / Sortino: 0.741 / 0.935
- Strategy average cash: 8.78%; ending cash: 17.32%

## Latest signal
- Signal date: 2026-05-28 → execution date: 2026-05-29
- Regime: very_expensive
- DCA multiplier: 0.75x
- Target new-buy split: SPY 40.00% / QQQ 60.00%
- Core/satellite: core SPY 40.00% + QQQ 40.00%; satellite SPY 0.00% + QQQ 20.00% (qqq_strong_satellite_to_qqq)
- Panic tier: 0; decision cash reservoir: 17.36%
- CAPE: 41.04; VIX: 15.74; RSI14: 73.28
- Cash reservoir: 17.32%
- Reason: CAPE=41.0:very_expensive; overbought_expensive_soft_cap:RSI=73.3; trend_confirmed_min_0_75x; qqq_strong_satellite_to_qqq

## Regime distribution
{
  "cheap": 0.1502,
  "deep_value": 0.1888,
  "elevated": 0.1756,
  "expensive": 0.0978,
  "fair": 0.3492,
  "very_expensive": 0.0384
}

## DCA multiplier distribution
{
  "0.5": 0.1175,
  "0.75": 0.1249,
  "1.0": 0.1335,
  "1.25": 0.3145,
  "1.5": 0.1434,
  "2.0": 0.1662
}
