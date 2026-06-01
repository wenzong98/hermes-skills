# US ETF Quant System — 3Y Backtest Report

## Window and assumptions
- Strategy version: 1.3.0-total-return
- Window: 2020-02-04 → 2020-06-30 (103 trading days)
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
- Strategy final value: $147,107.70
- Benchmark final value: $149,145.72
- Strategy profit vs contributed capital: $5,107.70 (3.60%)
- Benchmark profit vs contributed capital: $7,145.72 (5.03%)
- Relative final value difference: $-2,038.02
- Strategy XIRR: 10.79%
- Benchmark XIRR: 15.29%
- XIRR difference: -4.50%
- Strategy unitized max drawdown: -29.18%; account-value drawdown: -22.27%
- Benchmark unitized max drawdown: -30.89%; account-value drawdown: -24.10%
- Strategy Sharpe / Sortino: 0.250 / 0.307
- Benchmark Sharpe / Sortino: 0.340 / 0.412
- Strategy average cash: 8.04%; ending cash: 9.93%

## Latest signal
- Signal date: 2020-06-29 → execution date: 2020-06-30
- Regime: fair
- DCA multiplier: 1.25x
- Target new-buy split: SPY 50.00% / QQQ 50.00%
- Core/satellite: core SPY 40.00% + QQQ 40.00%; satellite SPY 10.00% + QQQ 10.00% (neutral_satellite)
- Panic tier: 0; decision cash reservoir: 10.08%
- CAPE: 28.84; VIX: 31.78; RSI14: 50.15
- Cash reservoir: 9.93%
- Reason: CAPE=28.8:fair; neutral_satellite

## Regime distribution
{
  "cheap": 0.2136,
  "elevated": 0.2718,
  "fair": 0.5146
}

## DCA multiplier distribution
{
  "0.5": 0.2427,
  "1.0": 0.165,
  "1.25": 0.1748,
  "1.5": 0.3398,
  "2.0": 0.0777
}
