# Strategy Rules — US ETF Quant System

## 1. Strategy objective

This system is built for long-horizon exposure to US broad indexes through SPY/S&P 500-like and QQQ/Nasdaq-100-like funds. It is not a short-term market timing model. The main objective is:

1. Keep a persistent US equity core.
2. Use valuation to modulate contribution speed, not to fully exit.
3. Use trend/VIX/RSI to avoid buying aggressively into unresolved downside momentum.
4. Use small, throttled trims only when valuation and momentum are simultaneously stretched or risk-off conditions are confirmed.
5. Preserve a cash reservoir when valuation/risk is poor, then redeploy it when oversold/panic conditions appear.

## 2. Existing-rule review

### v1: SPY-only CAPE + RSI

Strengths:

- Simple and interpretable.
- CAPE was correctly used as a slow valuation anchor.
- RSI added short-term oversold/overbought context.

Limitations:

- Only modeled SPY; did not reflect the user's actual S&P 500 + Nasdaq-100 exposure.
- Signals were too coarse for a high-valuation secular bull market.
- A pure CAPE pause can remain defensive for years and miss trend-confirmed gains.

### v2: SPY + QQQ + VIX + SMA200

Strengths:

- Added Nasdaq/S&P dual exposure.
- Added VIX regime and SMA200 trend filter.
- Added allocation tilts between SPY and QQQ.

Problems found during review:

- The old implementation could sell repeatedly every day while a negative signal persisted. This is unrealistic and can distort drawdowns/returns.
- Earlier benchmark calculations mixed contribution/expense assumptions and could produce misleading comparisons.
- yfinance access is unreliable in this environment; the packaged script now uses Nasdaq + Cboe + multpl public endpoints instead.
- The older rule `CAPE > 38 => pause` was too aggressive for the 2023-2026 market regime.

## 3. Production indicators

### Valuation

- `cape`: Shiller CAPE from multpl.com, monthly and forward-filled to trade dates.
- Use as a contribution throttle, not as a hard exit.

### Trend

- `spy_sma200`: long-term trend filter.
- `spy_sma50`: medium trend.
- `trend_up`: SPY close > SMA200.
- `trend_strong`: SMA50 > SMA200 and QQQ/SPY relative strength is positive.

### Momentum / stretch

- `spy_rsi14`: Wilder RSI over 14 trading days.
- RSI < 35 indicates oversold.
- RSI > 70 indicates overbought; with high CAPE it caps contribution speed.

### Risk

- `vix`: Cboe VIX close.
- VIX ≥ 25: risk elevated.
- VIX ≥ 30 with RSI/drawdown confirmation: panic redeployment candidate.

### Nasdaq relative strength / satellite allocation

- `qqq_rel_63d`: 63-trading-day change in QQQ/SPY ratio.
- `qqq_rel_126d`: 126-trading-day confirmation of the same QQQ/SPY ratio.
- `qqq_trend_up`: QQQ close > QQQ SMA200.
- These drive the 20% satellite sleeve in the 80/20 core-satellite model, not a full-portfolio rotation.

## 4. DCA multiplier rules

Base multiplier from CAPE:

- CAPE < 22: 2.0x.
- 22 ≤ CAPE < 25: 1.5x.
- 25 ≤ CAPE < 30: 1.25x.
- 30 ≤ CAPE < 35: 1.0x.
- 35 ≤ CAPE < 38: 0.75x.
- 38 ≤ CAPE < 42: 0.75x.
- CAPE ≥ 42: 0.5x.

Overlay rules:

- SPY below SMA200 and no panic confirmation: cap at 0.5x.
- VIX ≥ 25 and not oversold: cap at 0.5x.
- RSI14 ≥ 70 and CAPE ≥ 35: cap at 0.75x.
- SPY above SMA200 + VIX < 20 + QQQ/SPY relative strength positive + CAPE ≥ 35: enforce at least 0.75x.
- Scheme 8 panic ladder:
  - Tier 1: drawdown ≤ -8% with RSI ≤ 40, or VIX ≥ 28 with RSI ≤ 40 → at least 1.25x.
  - Tier 2: drawdown ≤ -15%, VIX ≥ 35, or RSI ≤ 32 with drawdown ≤ -8% → at least 1.5x.
  - Tier 3: drawdown ≤ -22% or VIX ≥ 45 → at least 2.0x.
  - Falling-knife guard: if SPY is below SMA200, VIX is above VIX SMA20, and 21d momentum is still negative, cap the emergency step-up so cash is deployed progressively rather than all at once.
- Cash-reservoir cap:
  - If simulated strategy cash exceeds 20%-30% while SPY remains above SMA200 and VIX < 25, lift the DCA floor to 0.75x-1.0x depending on valuation/overbought state.

Rationale: high CAPE still slows buying, but strong trend/low VIX and an already-large cash reservoir prevent the system from sitting on excessive cash for an entire bull-market leg. Panic conditions use the reserved cash more aggressively, but only in defined tiers.

## 5. SPY/QQQ allocation rules for new buys

Allocation applies to new DCA buys. It does not force daily portfolio rebalancing.

The production model now uses Scheme 11B:

- 80% core sleeve: fixed SPY 40% / QQQ 40% of each new-buy amount.
- 20% satellite sleeve: switches according to relative strength, trend, valuation heat, and panic tier.

Satellite sleeve rules:

- Risk-off: satellite goes to SPY → total new buy SPY 60% / QQQ 40%.
- Extreme valuation + RSI overbought: satellite goes to SPY → SPY 60% / QQQ 40%.
- QQQ strong: QQQ/SPY 63d ≥ +8%, QQQ/SPY 126d ≥ 0, QQQ above SMA200, VIX < 22 → satellite goes to QQQ → SPY 40% / QQQ 60%.
- QQQ mild strength: QQQ/SPY 63d ≥ +3%, QQQ above SMA200, VIX < 25 → satellite tilts QQQ → SPY 45% / QQQ 55%.
- QQQ weak: QQQ/SPY 63d ≤ -5% or QQQ below SMA200 → satellite goes to SPY → SPY 60% / QQQ 40%.
- Panic Tier 1: satellite is balanced or mildly QQQ-tilted depending on QQQ/SPY weakness.
- Panic Tier 2/3: satellite goes to QQQ only if the market is trend-up or deeply oversold; otherwise it goes to SPY under the falling-knife guard.
- Otherwise: satellite neutral → SPY 50% / QQQ 50%.

Rationale: this merges the old relative-strength and dual-momentum ideas into the new-buy layer. The core keeps persistent US equity exposure, while the satellite captures QQQ/SPY leadership without turning the whole portfolio into a high-turnover rotation system.

## 6. Stop-profit and risk-trim rules

Trims are deliberately small and throttled.

- At most one trim per calendar month.
- CAPE ≥ 42 and RSI14 ≥ 75: sell 3% of QQQ exposure.
- CAPE ≥ 40 and RSI14 ≥ 78: sell 3% of QQQ exposure.
- SPY below SMA200 and VIX ≥ 30: sell 10% of QQQ exposure.

Do not execute repeated daily sells from a persistent signal. The system produces risk-trim alerts; execution should be confirmed unless the user explicitly requests automation.

## 7. Backtest model

The packaged script uses a cash-reservoir model:

1. Start with initial capital on the first tradable row after a valid prior signal exists.
2. Use completed-close indicators only from the next trading bar onward by default.
3. Execute at next open by default; `same_close` is research-only and marked as lookahead.
4. Delay monthly CAPE observations by 10 business days before daily use.
5. Add a fixed weekly budget to strategy cash and benchmark cash.
6. Strategy invests `weekly_budget × multiplier`, capped by available cash.
7. Benchmark invests the full weekly budget into static 50/50 SPY/QQQ.
8. Strategy can accumulate cash in expensive/risk-off regimes.
9. Strategy can deploy more than 1x weekly budget only if cash reservoir exists.
10. If the simulated cash reservoir exceeds 20%-30% while trend and volatility remain supportive, the engine lifts the DCA floor to reduce long-term cash drag.
11. New buys use 80/20 core-satellite allocation; only the 20% satellite sleeve rotates by relative strength / panic state.

Metrics are contribution-aware:

- XIRR for annualized money-weighted return.
- Cash-flow-adjusted daily returns for volatility, Sharpe, Sortino, win rate.
- Unitized max drawdown for strategy-vs-benchmark comparison.
- Account-value max drawdown as a separate user-experience metric.

## 8. Latest backtest interpretation

3-year window: 2023-05-31 → 2026-05-29, using previous-close signals, next-open execution, and a 10-business-day CAPE availability lag.

- The fully invested 50/50 benchmark outperformed because this was a strong bull-market window.
- The optimized strategy intentionally held more cash; the latest lagged CAPE value is very expensive but not in the extreme valuation bucket.
- The trade-off was lower XIRR, slightly lower unitized drawdown, and a cash reservoir for future dislocations.
- Latest regime is very expensive, but trend/VIX remain supportive, so the rule is 0.75x rather than a full pause.

Latest signal from the packaged run:

- CAPE: 41.04.
- VIX: 15.74.
- RSI14: 73.18.
- DCA multiplier: 0.75x.
- New-buy allocation: SPY 40% / QQQ 60%.
- Cash reservoir: 14.85%.

## 9. How to adjust risk appetite

More conservative:

- Change trend-confirmed minimum from 0.75x to 0.5x.
- Increase SPY weights by 5-10 percentage points in CAPE ≥ 35 regimes.
- Keep trim thresholds unchanged.

More growth-oriented:

- Change trend-confirmed minimum from 0.75x to 1.0x.
- Allow QQQ 45%-50% in CAPE ≥ 40 only when VIX < 20 and SPY > SMA200.
- Do not increase trim sizes; use contribution speed and new-buy allocation first.

## 10. Operational rule for the user's current plan

The user's stored plan is a weekly 2000 unit定投. Translate multiplier into execution amount:

- 0.5x: 1000.
- 0.75x: 1500.
- 1.0x: 2000.
- 1.25x: 2500.
- 1.5x: 3000.
- 2.0x: 4000.

For the latest signal (0.75x, SPY 40% / QQQ 60%):

- Total weekly buy: 1500.
- SPY/S&P 500-like fund: 600.
- QQQ/Nasdaq-100-like fund: 900.

If the platform only supports the existing S&P 500定投, either keep that plan at 1000-1500 and manually add Nasdaq buys, or update the plan to split across the S&P 500 and Nasdaq funds.
