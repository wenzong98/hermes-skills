# Signal Timing Contract

This contract keeps the research backtest aligned with tradable timing.

## Default Backtest Contract

- Signal timestamp: indicators are computed from a completed close.
- Execution timestamp: the next tradable bar.
- Default execution price: next open via `--execution-price next_open`.
- Alternative conservative execution: next close via `--execution-price next_close`.
- Research-only comparison: same close via `--execution-price same_close`.

`same_close` is explicitly marked with `lookahead_warning` in output metadata because close-derived SMA, RSI, VIX, relative-strength, and CAPE-fed signals are not fully knowable before that same close.

## Trade Audit Fields

Every trade row must include:

- `date`: execution date.
- `signal_date`: signal observation date.
- `execution_price_mode`: `next_open`, `next_close`, or `same_close`.
- `spy_trade_price` and `qqq_trade_price`: actual simulated execution prices.

For default production-style runs, `signal_date < date` must hold for every trade. Equal dates are allowed only in `same_close` research runs.

## CAPE Availability

Monthly CAPE observations are delayed before becoming available to daily signals. The packaged default is:

```bash
--cape-lag-bdays 10
```

This is a conservative proxy for publication lag. It is still not a true historical vintage feed, so final validation should use a saved CAPE snapshot or a source with real publication timestamps.

## Request-Time Advice

`current_market_advice.py` uses the latest completed market close as a signal for the next available user action. It should not be read as an instruction that could have been executed at the same close used to compute the signal.
