# External Verification Contract

This document defines the minimum contract an outside reviewer needs to
independently re-derive the `us-etf-quant-system` trade list, without
importing or executing the production code, and to match the
`us-etf-quant-system-verifier` output to zero.

The internal verifier (`us-etf-quant-system-verifier`) already satisfies
this contract today. The purpose of this document is to make the contract
explicit so a third party — academic reviewer, audit firm, or open-source
re-implementer — can run their own re-implementation against the same data
and rule spec and have confidence their results are comparable.

## Inputs (Frozen Data Bundle)

The reviewer receives a versioned data bundle, immutable per release tag,
with the following files. Each file's SHA256 must be checked on receipt.

| File | Purpose | Required columns / schema |
| --- | --- | --- |
| `spy_ohlcv.csv` | SPY daily OHLCV, dividend-adjusted | `date, open, high, low, close, volume` (date in `YYYY-MM-DD`) |
| `qqq_ohlcv.csv` | QQQ daily OHLCV, dividend-adjusted | same as above |
| `vix.csv` | Cboe VIX daily close | `date, vix` |
| `cape_vintage.csv` | PIT-correct CAPE vintage | `observation_month, available_at, cape, source, downloaded_at` (see `update_cape_snapshot.py` for full schema) |
| `manifest.json` | Bundle metadata | SHA256 of every other file, download timestamps, source URLs |

The bundle must be **frozen per release tag** so that the production code,
the verifier, and any external port all consume identical bytes. A reviewer
that observes a non-matching SHA256 should refuse to proceed and report the
mismatch.

## Rule Specification

`references/strategy_spec_v1.json` is the machine-readable rule spec
(decision rules, indicators, decision_required_columns, signal/execution
contract, output schema, etc.). It is the authoritative contract for the
reviewer's re-implementation.

The reviewer MUST consume this JSON (or its successor `strategy_spec_vN.json`)
and NOT a prose re-statement of the rules. The JSON is what the internal
verifier consumes; any drift between the JSON and prose is resolved in
favor of the JSON.

## Signal / Execution Contract (PIT)

The reviewer's re-implementation MUST honor the same PIT contract as the
production code:

- Indicators are computed from **previous completed close**.
- Execution defaults to **next available open** (`--execution-price next_open`).
- `same_close` is **research only** and any trades with `signal_date == date`
  must carry `lookahead_warning: true` in their row.
- The chosen CAPE `available_at` MUST be `<= signal_date` for every signal
  row. A reviewer that uses a future-dated CAPE has produced a lookahead
  result and is invalid.

## Required Outputs

The reviewer must produce, at minimum:

1. A trades list with the same columns as
   `references/backtest_3y_trades.csv`. The column order may vary; the
   content must match.
2. A daily equity curve with at least `date, strategy_nav, benchmark_nav`.
3. A summary metrics block including XIRR, max drawdown, ending cash, and
   final value for both strategy and benchmark.

## Acceptance Criterion

A reviewer passes when their output equals the verifier's
`trades_diff.csv` to **zero diffs** (modulo column ordering and floating
point representation within `1e-9`). The internal verifier exits non-zero
on any other outcome.

## Re-Implementation Guidance (Non-Normative)

The reviewer is free to use any stack. Common starting points:

- **Python (polars)** — the production code uses pandas, but polars is
  acceptable as long as the PIT rules are honored. The CAPE `available_at`
  constraint should be expressed as a `set_sorted` / `asof_join` against
  the signal date.
- **R** — `dplyr` + `tidyr` work well; the CAPE constraint is a
  `rolljoin` with `direction = "<="`.
- **Stata / SAS** — generally not recommended for PIT work because the
  default `merge` does not enforce temporal ordering; the reviewer must
  explicitly verify it.

## Audit Trail

The reviewer's submission MUST include:

- The exact SHA256 of every input file in the frozen bundle.
- The version of `strategy_spec_v1.json` consumed (or its successor).
- The reviewer-implementation version (commit SHA or release tag).
- A clear enumeration of any differences from the verifier output and a
  written justification for each one.

A submission without these four items is not eligible for an academic
review pass.

## Open Items (v1.3.0-total-return)

- The frozen data bundle is currently produced on demand by the production
  code (see `scripts/export_market_inputs.py`); it is not yet published
  as a tagged artifact. The next milestone is to tag a bundle per release
  and serve it from a stable URL.
- The rule spec `strategy_spec_v1.json` covers decision rules and output
  schema but does not yet enumerate every indicator and every panic
  threshold. The first version of the contract only pins what the
  verifier currently checks; expansion is tracked separately.
