# Contribution & Branching Guide

## Branches

- **`main`** — protected. Always green, always releasable. Direct pushes are
  rejected; all changes land via PR.
- **`feature/<slug>`** — new functionality. Branch from `main`. PR back to
  `main` once tests + reviewer are happy.
- **`fix/<slug>`** — bug fix. Same flow as `feature/`, but the PR title uses
  `fix(<scope>):` so it can be cherry-picked to a release branch later.
- **`chore/<slug>`** — tooling, refactors, docs, dep bumps. No user-visible
  behavior change.
- **`release/<version>`** *(optional)* — cut just before tagging. Use only
  for release-blocking fixes.

## Commit messages — Conventional Commits

Required format:

```
<type>(<scope>): <short summary>

<body explaining the why; the what is in the diff>

<footer with breaking-change notes or refs>
```

Common types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`.
Scope examples: `dashboard`, `advice`, `backtest`, `data`, `ci`, `config`.

## Local workflow

```bash
git checkout main
git pull --rebase
git checkout -b fix/your-issue
# ... edit, ...
pytest -q
python scripts/build_dashboard.py --no-open   # if you touched dashboard
git add -p
git commit -m "fix(dashboard): your short summary"
git push -u origin fix/your-issue
# open PR via gh or web UI
```

## Self-review checklist (before requesting review)

- [ ] `pytest -q` passes (currently 18 tests)
- [ ] If you touched `scripts/build_dashboard.py` or the template:
  - [ ] `python scripts/build_dashboard.py --no-open` succeeds
  - [ ] `data.json.summary.metrics.oneYearReturn` matches `strategy_nav`,
        not `strategy_value` (return metrics must exclude DCA inflows)
  - [ ] `data.json.summary.currentAction.action` agrees with
        `advice.recommended` (trim signal / buy amount), not
        `bt.recent_trades[-1]`
  - [ ] No raw `innerHTML` on user-visible text from `data.json` — use
        `esc()`, `textContent`, or `pillHtml()` from the template
- [ ] If you touched backtest logic:
  - [ ] `replay_strategy.py --strict-total-return --strict-cape-vintage`
        from the verifier repo passes
  - [ ] `tests/test_no_lookahead.py` passes (no future-dated CAPE rows)
- [ ] If you touched `portfolio_config.json`:
  - [ ] Each `holdings.funds[*]` has an explicit `target: "SPY" | "QQQ"`
        field — don't rely on name substring matching

## Out of scope for direct commits to main

- Dashboard build artifacts (`references/dashboard/{data.json,index.html,dashboard.html}`)
  are git-ignored. They regenerate from the template + data inputs.
- The vendor ECharts file (`references/dashboard/vendor/echarts.min.js`) is
  also ignored; it downloads on first build via `_ensure_vendor()`.
- Work-in-progress scratch (`work/`, `outputs/`) is ignored.

## Releases

Tags are `vMAJOR.MINOR.PATCH`. The current development line is **1.3.x**
(total-return aware). New branches should target `main`; only after
acceptance is a release cut from `release/<v>`.
