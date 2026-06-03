## What this PR does

<!-- One-paragraph summary. Cite the issue or report this addresses. -->

## Type of change

- [ ] `feat` — new user-visible functionality
- [ ] `fix` — bug fix
- [ ] `chore` — tooling / docs / refactor (no behavior change)

## Affected components

- [ ] `scripts/backtest_us_etf.py`
- [ ] `scripts/current_market_advice.py`
- [ ] `scripts/build_dashboard.py`
- [ ] `scripts/_dashboard_template.html`
- [ ] `scripts/data_sources.py` / `update_cape_snapshot.py`
- [ ] `portfolio_config.json`
- [ ] `tests/`
- [ ] `references/` (data inputs)
- [ ] docs / skill files

## Pre-merge checklist

- [ ] `pytest -q` passes (expected: 18+ tests)
- [ ] If you touched the dashboard, `python scripts/build_dashboard.py --no-open` succeeds
- [ ] If you touched the dashboard, `data.json.summary.metrics.oneYearReturn` reflects `strategy_nav` (not `strategy_value`) — returns must exclude DCA inflows
- [ ] If you touched `currentAction`, the source is `advice.recommended` / `decision.trim_*`, not `bt.recent_trades[-1]`
- [ ] No raw `innerHTML` interpolation of `data.json` text — use `esc()` / `textContent` / `pillHtml()`
- [ ] If you touched backtest logic, verifier `replay_strategy.py --strict-total-return --strict-cape-vintage` passes
- [ ] No new lookahead / future-dated data introduced

## Risk

<!-- One or two lines: what's the worst case if this ships with a bug? How would we roll back? -->

## Screenshots / data diff

<!-- Paste the new oneYearReturn / currentAction fields from data.json if relevant. -->
