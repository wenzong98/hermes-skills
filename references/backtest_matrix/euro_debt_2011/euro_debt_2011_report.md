# US ETF Quant System — 3Y Backtest Report

## Window and assumptions
- Strategy version: 1.3.0-total-return
- Window: 2011-01-04 → 2011-12-30 (251 trading days)
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
- Strategy final value: $204,792.17
- Benchmark final value: $204,866.40
- Strategy profit vs contributed capital: $792.17 (0.39%)
- Benchmark profit vs contributed capital: $866.40 (0.42%)
- Relative final value difference: $-74.23
- Strategy XIRR: 0.53%
- Benchmark XIRR: 0.58%
- XIRR difference: -0.05%
- Strategy unitized max drawdown: -15.75%; account-value drawdown: -13.33%
- Benchmark unitized max drawdown: -16.21%; account-value drawdown: -13.74%
- Strategy Sharpe / Sortino: 0.169 / 0.219
- Benchmark Sharpe / Sortino: 0.175 / 0.229
- Strategy average cash: 4.73%; ending cash: 16.96%

## Latest signal
- Signal date: 2011-12-29 → execution date: 2011-12-30
- Regime: deep_value
- DCA multiplier: 2.00x
- Target new-buy split: SPY 50.00% / QQQ 50.00%
- Core/satellite: core SPY 40.00% + QQQ 40.00%; satellite SPY 10.00% + QQQ 10.00% (neutral_satellite)
- Panic tier: 0; decision cash reservoir: 16.90%
- CAPE: 20.52; VIX: 22.65; RSI14: 57.53
- Cash reservoir: 16.96%
- Reason: CAPE=20.5:deep_value; neutral_satellite

## Regime distribution
{
  "cheap": 0.6175,
  "deep_value": 0.3825
}

## DCA multiplier distribution
{
  "0.5": 0.2669,
  "1.5": 0.6016,
  "2.0": 0.1315
}
