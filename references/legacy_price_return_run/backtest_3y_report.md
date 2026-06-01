# US ETF Quant System — 3Y Backtest Report

## Window and assumptions
- Strategy version: 1.2.0-system-hardening
- Window: 2023-05-31 → 2026-05-29 (752 trading days)
- Signal/execution: previous completed close signal with execution at next open
- CAPE availability lag: 10 business days
- Price source: requested nasdaq_price_return / actual SPY nasdaq_price_return / actual QQQ nasdaq_price_return
- Adjusted for dividends: False; price-return only: True
- CAPE source: yale_shiller_primary_multpl_recent_fallback
- Contribution schedule: first trading day on/after Thursday in each ISO week
- Risk-free rate for Sharpe/Sortino: 0.00%
- Initial capital: $100,000.00
- Weekly capital budget: $2,000.00
- Transaction cost: 0.15%
- Benchmark: 50/50 SPY/QQQ buy-and-hold plus weekly 1x budget
- Data caveat: if `price_return_only` is true, dividends are still excluded for both strategy and benchmark.

## Headline results
- Strategy final value: $619,624.16
- Benchmark final value: $649,631.40
- Strategy profit vs contributed capital: $205,624.16 (49.67%)
- Benchmark profit vs contributed capital: $235,631.40 (56.92%)
- Relative final value difference: $-30,007.24
- Strategy XIRR: 22.87%
- Benchmark XIRR: 25.72%
- XIRR difference: -2.85%
- Strategy unitized max drawdown: -20.03%; account-value drawdown: -16.86%
- Benchmark unitized max drawdown: -21.01%; account-value drawdown: -17.85%
- Strategy Sharpe / Sortino: 1.354 / 1.808
- Benchmark Sharpe / Sortino: 1.393 / 1.876
- Strategy average cash: 5.21%; ending cash: 14.85%

## Latest signal
- Signal date: 2026-05-28 → execution date: 2026-05-29
- Regime: very_expensive
- DCA multiplier: 0.75x
- Target new-buy split: SPY 40.00% / QQQ 60.00%
- Core/satellite: core SPY 40.00% + QQQ 40.00%; satellite SPY 0.00% + QQQ 20.00% (qqq_strong_satellite_to_qqq)
- Panic tier: 0; decision cash reservoir: 14.89%
- CAPE: 41.04; VIX: 15.74; RSI14: 73.18
- Cash reservoir: 14.85%
- Reason: CAPE=41.0:very_expensive; overbought_expensive_soft_cap:RSI=73.2; trend_confirmed_min_0_75x; qqq_strong_satellite_to_qqq

## Regime distribution
{
  "elevated": 0.3604,
  "expensive": 0.3617,
  "fair": 0.0718,
  "very_expensive": 0.2061
}

## DCA multiplier distribution
{
  "0.5": 0.0585,
  "0.75": 0.5386,
  "1.0": 0.3098,
  "1.25": 0.0878,
  "1.5": 0.0053
}
