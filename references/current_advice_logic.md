# Request-Time Current Market Advice Logic

This reference documents how `scripts/current_market_advice.py` converts the latest available US market data into a Chinese action report for S&P 500/SPY-like and Nasdaq-100/QQQ-like fund exposure.

## Inputs

- SPY and QQQ daily prices from the Nasdaq public historical quote API.
- VIX daily close from Cboe `VIX_History.csv`.
- Shiller CAPE monthly value from multpl.com.
- Optional user portfolio config from `~/.hermes/portfolio_config.json`.

The latest signal uses the latest common trading day available across SPY, QQQ, VIX, and CAPE-derived monthly data. If the user asks during Asian daytime before the current US session closes, the latest market date will normally be the previous US trading day.

## Market Diagnosis Fields

The report always surfaces the latest signal plus the latest 3 trading-day signal history by default:

- `CAPE`: long-term valuation regime.
- `SPY vs SMA200`: long-term trend confirmation.
- `SMA50 vs SMA200`: medium-term trend quality.
- `RSI14`: overbought/oversold pressure.
- `VIX` and `VIX SMA20`: volatility/risk premium state.
- `QQQ/SPY 63d relative strength` and `QQQ/SPY 126d relative strength`: satellite allocation signals.
- `QQQ vs SMA200`: whether Nasdaq itself remains in an uptrend.
- `SPY 21d return` and `252d drawdown`: short-term momentum and drawdown context.
- `model cash reservoir`: simulated strategy cash share used to prevent excessive cash drag when trend/risk remain supportive.

## Action Mapping

### Increase DCA / add exposure

- CAPE < 30: valuation allows 1.25x or higher DCA.
- CAPE < 35 and trend remains above SMA200: at least normal 1.0x DCA.
- Scheme 8 Tier 1: drawdown <= -8% with RSI <= 40, or VIX >= 28 with RSI <= 40, raises DCA to at least 1.25x.
- Scheme 8 Tier 2: drawdown <= -15%, VIX >= 35, or RSI <= 32 with drawdown <= -8%, raises DCA to at least 1.5x.
- Scheme 8 Tier 3: drawdown <= -22% or VIX >= 45, raises DCA to at least 2.0x.
- Cash-reservoir cap: if simulated cash is above 20%-30% and SPY remains above SMA200 with VIX < 25, lift the DCA floor to 0.75x-1.0x to avoid long-term underparticipation.

### Decrease DCA / slow buying

- CAPE >= 35 and RSI >= 70: cap DCA at 0.75x.
- VIX >= 25 without oversold confirmation: cap DCA at 0.5x.
- SPY below SMA200 without panic confirmation: cap DCA at 0.5x.
- CAPE >= 42 baseline is 0.5x, but if trend is healthy, VIX < 20, and QQQ/SPY relative strength is positive, the bull-market guard lifts it back to 0.75x to avoid valuation-only underparticipation.

### New-buy allocation

The request-time report uses Scheme 11B:

- 80% core sleeve: fixed SPY 40% / QQQ 40%.
- 20% satellite sleeve: driven by QQQ/SPY 63d/126d relative strength, QQQ vs SMA200, VIX, valuation heat, and panic tier.
- Satellite to QQQ gives total SPY 40% / QQQ 60%; satellite to SPY gives SPY 60% / QQQ 40%; partial QQQ tilt gives SPY 45% / QQQ 55%; neutral gives 50/50.
- This is a new-money rule, not a full-portfolio rebalance order.

### Trim / reduce exposure

Trims are alerts, not automatic orders, and are meant to be at most monthly:

- CAPE >= 42 and RSI >= 75: QQQ micro-trim 3%.
- CAPE >= 40 and RSI >= 78: QQQ profit-lock trim 3%.
- SPY below SMA200 and VIX >= 30: QQQ risk-off trim 10%.

## Portfolio-Aware Output

If portfolio config is available, the script converts the model signal into:

- next-period total buy amount = weekly budget × DCA multiplier;
- SPY-like buy amount and QQQ-like buy amount;
- core/satellite decomposition and satellite signal;
- current and post-buy SPY/QQQ weights;
- simulated model cash reservoir used by the cash-drag control overlay;
- optional diagnostic shift amount if the whole portfolio were forced to match the current new-buy target.

The diagnostic shift is not a default trade recommendation. By default, the system prefers changing future buy allocation first, then using small monthly trims only when rule triggers fire.
