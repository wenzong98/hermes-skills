# US ETF Quant System — 3Y Backtest Report

## Window and assumptions
- Strategy version: 1.3.0-total-return
- Window: 2022-01-04 → 2022-12-30 (250 trading days)
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
- Strategy final value: $174,738.71
- Benchmark final value: $168,430.46
- Strategy profit vs contributed capital: $-29,261.29 (-14.34%)
- Benchmark profit vs contributed capital: $-35,569.54 (-17.44%)
- Relative final value difference: $6,308.25
- Strategy XIRR: -19.29%
- Benchmark XIRR: -23.38%
- XIRR difference: 4.08%
- Strategy unitized max drawdown: -25.17%; account-value drawdown: -7.23%
- Benchmark unitized max drawdown: -29.15%; account-value drawdown: -10.24%
- Strategy Sharpe / Sortino: -1.002 / -1.623
- Benchmark Sharpe / Sortino: -0.930 / -1.529
- Strategy average cash: 16.74%; ending cash: 14.07%

## Latest signal
- Signal date: 2022-12-29 → execution date: 2022-12-30
- Regime: fair
- DCA multiplier: 1.50x
- Target new-buy split: SPY 60.00% / QQQ 40.00%
- Core/satellite: core SPY 40.00% + QQQ 40.00%; satellite SPY 20.00% + QQQ 0.00% (panic_tier_2_falling_knife_satellite_to_spy)
- Panic tier: 2; decision cash reservoir: 14.04%
- CAPE: 28.32; VIX: 21.44; RSI14: 46.39
- Cash reservoir: 14.07%
- Reason: CAPE=28.3:fair; panic_ladder_2:tier2_panic:VIX=21.4,RSI=46.4,DD=-18.4%; panic_tier_2_falling_knife_satellite_to_spy

## Regime distribution
{
  "elevated": 0.344,
  "expensive": 0.16,
  "fair": 0.46,
  "very_expensive": 0.036
}

## DCA multiplier distribution
{
  "0.5": 0.244,
  "0.75": 0.096,
  "1.0": 0.068,
  "1.25": 0.26,
  "1.5": 0.328,
  "2.0": 0.004
}
