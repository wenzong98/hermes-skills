# US ETF Quant System — 3Y Backtest Report

## Window and assumptions
- Strategy version: 1.3.0-total-return-pit
- Window: 2023-05-31 → 2026-06-05 (757 trading days)
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
- Strategy final value: $613,234.42
- Benchmark final value: $639,651.77
- Strategy profit vs contributed capital: $197,234.42 (47.41%)
- Benchmark profit vs contributed capital: $223,651.77 (53.76%)
- Relative final value difference: $-26,417.35
- Strategy XIRR: 21.83%
- Benchmark XIRR: 24.34%
- XIRR difference: -2.51%
- Strategy unitized max drawdown: -19.89%; account-value drawdown: -16.75%
- Benchmark unitized max drawdown: -20.83%; account-value drawdown: -17.70%
- Strategy Sharpe / Sortino: 1.332 / 1.763
- Benchmark Sharpe / Sortino: 1.362 / 1.811
- Strategy average cash: 5.07%; ending cash: 15.07%

## Latest signal
- Signal date: 2026-06-04 → execution date: 2026-06-05
- Regime: very_expensive
- DCA multiplier: 0.75x
- Target new-buy split: SPY 40.00% / QQQ 60.00%
- Core/satellite: core SPY 40.00% + QQQ 40.00%; satellite SPY 0.00% + QQQ 20.00% (qqq_strong_satellite_to_qqq)
- Panic tier: 0; decision cash reservoir: 14.59%
- CAPE: 41.04; VIX: 15.40; RSI14: 69.74
- Cash reservoir: 15.07%
- Reason: CAPE=41.0:very_expensive; trend_confirmed_min_0_75x; qqq_strong_satellite_to_qqq

## Regime distribution
{
  "elevated": 0.358,
  "expensive": 0.3593,
  "fair": 0.0713,
  "very_expensive": 0.2114
}

## DCA multiplier distribution
{
  "0.5": 0.0542,
  "0.75": 0.5429,
  "1.0": 0.3078,
  "1.25": 0.0898,
  "1.5": 0.0053
}
