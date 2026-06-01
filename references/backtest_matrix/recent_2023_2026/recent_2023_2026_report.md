# US ETF Quant System — 3Y Backtest Report

## Window and assumptions
- Strategy version: 1.3.0-rc
- Window: 2023-05-31 → 2026-05-29 (752 trading days)
- Signal/execution: previous completed close signal with execution at next open
- CAPE availability lag: 10 business days
- Price source: requested yahoo_chart_adjusted / actual SPY None / actual QQQ None
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
- Strategy final value: $630,424.46
- Benchmark final value: $661,267.03
- Strategy profit vs contributed capital: $216,424.46 (52.28%)
- Benchmark profit vs contributed capital: $247,267.03 (59.73%)
- Relative final value difference: $-30,842.57
- Strategy XIRR: 23.91%
- Benchmark XIRR: 26.80%
- XIRR difference: -2.89%
- Strategy unitized max drawdown: -19.89%; account-value drawdown: -16.75%
- Benchmark unitized max drawdown: -20.83%; account-value drawdown: -17.70%
- Strategy Sharpe / Sortino: 1.407 / 1.879
- Benchmark Sharpe / Sortino: 1.448 / 1.949
- Strategy average cash: 5.01%; ending cash: 14.58%

## Latest signal
- Signal date: 2026-05-28 → execution date: 2026-05-29
- Regime: very_expensive
- DCA multiplier: 0.75x
- Target new-buy split: SPY 40.00% / QQQ 60.00%
- Core/satellite: core SPY 40.00% + QQQ 40.00%; satellite SPY 0.00% + QQQ 20.00% (qqq_strong_satellite_to_qqq)
- Panic tier: 0; decision cash reservoir: 14.62%
- CAPE: 41.04; VIX: 15.74; RSI14: 73.28
- Cash reservoir: 14.58%
- Reason: CAPE=41.0:very_expensive; overbought_expensive_soft_cap:RSI=73.3; trend_confirmed_min_0_75x; qqq_strong_satellite_to_qqq

## Regime distribution
{
  "elevated": 0.3604,
  "expensive": 0.3617,
  "fair": 0.0718,
  "very_expensive": 0.2061
}

## DCA multiplier distribution
{
  "0.5": 0.0545,
  "0.75": 0.5399,
  "1.0": 0.3098,
  "1.25": 0.0904,
  "1.5": 0.0053
}
