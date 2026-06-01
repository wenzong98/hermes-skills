# US ETF Quant System — 3Y Backtest Report

## Window and assumptions
- Strategy version: 1.3.0-rc
- Window: 2018-09-05 → 2018-12-31 (81 trading days)
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
- Strategy final value: $116,279.53
- Benchmark final value: $116,302.69
- Strategy profit vs contributed capital: $-17,720.47 (-13.22%)
- Benchmark profit vs contributed capital: $-17,697.31 (-13.21%)
- Relative final value difference: $-23.16
- Strategy XIRR: -39.87%
- Benchmark XIRR: -39.82%
- XIRR difference: -0.04%
- Strategy unitized max drawdown: -20.57%; account-value drawdown: -11.12%
- Benchmark unitized max drawdown: -20.96%; account-value drawdown: -11.57%
- Strategy Sharpe / Sortino: -1.823 / -2.564
- Benchmark Sharpe / Sortino: -1.773 / -2.517
- Strategy average cash: 1.16%; ending cash: 6.53%

## Latest signal
- Signal date: 2018-12-28 → execution date: 2018-12-31
- Regime: fair
- DCA multiplier: 1.25x
- Target new-buy split: SPY 60.00% / QQQ 40.00%
- Core/satellite: core SPY 40.00% + QQQ 40.00%; satellite SPY 20.00% + QQQ 0.00% (risk_off_satellite_to_spy)
- Panic tier: 1; decision cash reservoir: 6.58%
- CAPE: 28.29; VIX: 28.34; RSI14: 39.28
- Cash reservoir: 6.53%
- Reason: CAPE=28.3:fair; falling_knife_guard; panic_ladder_1:tier1_dip:VIX=28.3,RSI=39.3,DD=-14.7%; risk_off_satellite_to_spy

## Regime distribution
{
  "elevated": 0.8765,
  "fair": 0.1235
}

## DCA multiplier distribution
{
  "0.5": 0.2716,
  "1.0": 0.5062,
  "1.25": 0.2222
}
