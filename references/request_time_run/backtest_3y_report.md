# US ETF Quant System — 3Y Backtest Report

## Window and assumptions
- Window: 2023-05-30 → 2026-05-28 (752 trading days)
- Initial capital: $100,000.00
- Weekly capital budget: $2,000.00
- Transaction cost: 0.15%
- Benchmark: 50/50 SPY/QQQ buy-and-hold plus weekly 1x budget
- Data caveat: Nasdaq closes are price-return data; dividends are excluded for both strategy and benchmark.

## Headline results
- Strategy final value: $615,985.99
- Benchmark final value: $647,531.02
- Strategy profit vs contributed capital: $201,985.99 (48.79%)
- Benchmark profit vs contributed capital: $233,531.02 (56.41%)
- Relative final value difference: $-31,545.03
- Strategy XIRR: 22.54%
- Benchmark XIRR: 25.55%
- XIRR difference: -3.01%
- Strategy max drawdown: -16.48%
- Benchmark max drawdown: -17.82%
- Strategy Sharpe / Sortino: 1.347 / 1.808
- Benchmark Sharpe / Sortino: 1.381 / 1.863
- Strategy average cash: 5.25%; ending cash: 15.57%

## Latest signal
- Date: 2026-05-28
- Regime: extreme_valuation
- DCA multiplier: 0.75x
- Target new-buy split: SPY 60.00% / QQQ 40.00%
- CAPE: 42.55; VIX: 15.74; RSI14: 73.18
- Cash reservoir: 15.57%
- Reason: CAPE=42.5:extreme_valuation; overbought_expensive_soft_cap:RSI=73.2; trend_confirmed_min_0_75x

## Regime distribution
{
  "elevated": 0.3324,
  "expensive": 0.363,
  "extreme_valuation": 0.0013,
  "fair": 0.0864,
  "very_expensive": 0.2168
}

## DCA multiplier distribution
{
  "0.5": 0.0838,
  "0.75": 0.5426,
  "1.0": 0.2846,
  "1.25": 0.0771,
  "1.5": 0.004,
  "2.0": 0.008
}
