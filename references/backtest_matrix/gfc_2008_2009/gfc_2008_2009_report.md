# US ETF Quant System — 3Y Backtest Report

## Window and assumptions
- Strategy version: 1.3.0-rc
- Window: 2008-01-03 → 2009-12-31 (504 trading days)
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
- Strategy final value: $321,181.20
- Benchmark final value: $327,225.93
- Strategy profit vs contributed capital: $11,181.20 (3.61%)
- Benchmark profit vs contributed capital: $17,225.93 (5.56%)
- Relative final value difference: $-6,044.73
- Strategy XIRR: 2.71%
- Benchmark XIRR: 4.16%
- XIRR difference: -1.45%
- Strategy unitized max drawdown: -50.69%; account-value drawdown: -31.43%
- Benchmark unitized max drawdown: -50.44%; account-value drawdown: -32.69%
- Strategy Sharpe / Sortino: -0.099 / -0.135
- Benchmark Sharpe / Sortino: -0.058 / -0.081
- Strategy average cash: 3.27%; ending cash: 0.00%

## Latest signal
- Signal date: 2009-12-30 → execution date: 2009-12-31
- Regime: deep_value
- DCA multiplier: 2.00x
- Target new-buy split: SPY 50.00% / QQQ 50.00%
- Core/satellite: core SPY 40.00% + QQQ 40.00%; satellite SPY 10.00% + QQQ 10.00% (neutral_satellite)
- Panic tier: 0; decision cash reservoir: 0.62%
- CAPE: 20.32; VIX: 19.96; RSI14: 62.33
- Cash reservoir: 0.00%
- Reason: CAPE=20.3:deep_value; neutral_satellite

## Regime distribution
{
  "cheap": 0.248,
  "deep_value": 0.7341,
  "fair": 0.0179
}

## DCA multiplier distribution
{
  "0.5": 0.1905,
  "1.25": 0.0119,
  "1.5": 0.0873,
  "2.0": 0.7103
}
