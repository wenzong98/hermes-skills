# Remediation Progress

Last updated: 2026-06-01

## Status Summary

| Priority | Issue | Status | Evidence |
|---|---|---|---|
| P0 | Same-day close signal and same-day close execution | Fixed | `meta.signal_timing=previous_close_signal`, `meta.execution_price=next_open`; verifier checks all trades have `signal_date < date` |
| P0 | CAPE point-in-time availability | Fixed | `cape_available_lag_bdays=10`; vintage mechanism added via `update_cape_snapshot.py` with `available_at` constraints; `--cape-vintage-path` CLI flag |
| P0 | Backtest artifacts inconsistent with code | Fixed | `references/backtest_3y_results.json`, CSVs, report, and `backtest_3y_data_manifest.json` regenerated from current code |
| P0 | Request-time trim monthly state | Fixed | `current_market_advice.py` reads `~/.hermes/us_etf_trim_state.json` and separates raw/effective trim signals |
| P1 | Max drawdown from raw account value | Fixed | `unitized_max_drawdown` added and used as primary `max_drawdown`; account-value drawdown remains separately reported |
| P1 | Price-return data excludes dividends | Fixed | Data source layer refactored into `scripts/data_sources.py`; `tiingo_adjusted` provider added; `--require-adjusted` flag prevents silent fallback; `--cache-dir` for data caching with SHA256 manifests |
| P1 | Missing tests / requirements / CI | Fixed | `requirements.txt`, pytest tests, and GitHub Actions CI added |
| P1 | Missing data snapshot and hash | Fixed | result metadata and `backtest_3y_data_manifest.json` include script/data hashes and source metadata |
| P2 | Hard-coded Thursday schedule | Fixed | `--contribution-weekday` parameter added; default remains Thursday |
| P2 | Sharpe did not deduct risk-free rate | Fixed | `--risk-free-rate` added; Sharpe/Sortino use excess returns |
| P2 | Only 2023-2026 bull-market window | Fixed | `scripts/run_backtest_matrix.py` added with 7 windows: 2006-2026, 2008-2009, 2011, 2018 Q4, 2020, 2022, 2023-2026 |
| P2 | QDII execution layer | Fixed | `scripts/qdii_execution_layer.py` added with premium/volume/subscription rules; `references/qdii_universe.json` template |

## Version

Strategy version: `1.3.0-total-return`

## New Files in This Release

### Main repo

- `scripts/data_sources.py` — Extracted data provider layer with Nasdaq, Yahoo, Alpha Vantage, Tiingo providers; caching with SHA256 manifests
- `scripts/update_cape_snapshot.py` — CAPE vintage mechanism with `available_at` constraints
- `scripts/run_backtest_matrix.py` — Multi-window backtest matrix runner
- `scripts/qdii_execution_layer.py` — QDII execution layer with premium/volume/subscription rules
- `references/qdii_universe.json` — QDII fund universe template

### Verifier repo

- `verifier/replay_strategy.py` — Independent strategy rule replay: RSI Wilder, SMA, panic ladder, multiplier rules, 11B core/satellite, monthly trim throttle, cash reservoir policy — all without importing main repo code
- Updated `tests/test_verifier_no_imports.py` — Checks for no importlib, no network calls, independent implementations

## Key CLI Additions

```bash
# Total-return backtest with Tiingo
python3 scripts/backtest_us_etf.py \
  --start 2023-05-29 --end 2026-05-29 \
  --price-source tiingo_adjusted \
  --require-adjusted \
  --cache-dir references/data_cache \
  --output-dir references

# CAPE vintage update
python3 scripts/update_cape_snapshot.py \
  --output references/data_cache/cape_vintage.csv

# Backtest with vintage CAPE
python3 scripts/backtest_us_etf.py \
  --start 2023-05-29 --end 2026-05-29 \
  --cape-vintage-path references/data_cache/cape_vintage.csv

# Multi-window backtest matrix
python3 scripts/run_backtest_matrix.py \
  --output-dir references/backtest_matrix \
  --price-source tiingo_adjusted \
  --require-adjusted

# QDII execution advice
python3 scripts/qdii_execution_layer.py \
  --universe references/qdii_universe.json \
  --spy-buy 600 --qqq-buy 900

# Independent verifier replay
cd ~/.hermes/skills/research/us-etf-quant-system-verifier
python3 verifier/replay_strategy.py --main-repo ../us-etf-quant-system
```

## Independent Verifier

Created at:

```text
~/.hermes/skills/research/us-etf-quant-system-verifier
```

Verifier constraints:

- reads exported JSON/CSV/spec artifacts;
- does not import `scripts/backtest_us_etf.py`;
- does not use `importlib` to load main repo code;
- independently implements: RSI Wilder, SMA, panic ladder, multiplier rules, 11B core/satellite, monthly trim throttle, cash reservoir policy;
- independently recomputes unitized and account-value drawdowns;
- checks no default same-day signal/execution lookahead;
- outputs `trades_diff.csv`, `metrics_diff.json`, `validation_report.md`.

## Remaining Work

1. Run the full backtest matrix with an adjusted data source (requires `TIINGO_API_KEY` or `ALPHAVANTAGE_API_KEY` environment variable).
2. Run the CAPE vintage snapshot update and validate `available_at` constraints in a live run.
3. Final commit and tag for both repos.
