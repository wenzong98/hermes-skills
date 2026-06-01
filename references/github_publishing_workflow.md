# GitHub Publishing Workflow for US ETF Quant Skill

Use this when the user asks whether the strategy is on GitHub or asks to publish / update the public skill repository.

## Current repository

- GitHub repo: https://github.com/wenzong98/hermes-skills
- Local skill source: `~/.hermes/skills/research/us-etf-quant-system`
- Remote: `origin https://github.com/wenzong98/hermes-skills.git`
- Default branch: `main`

## First-time repo creation pattern

The `gh repo create` CLI accepts `OWNER/REPO` as the positional repo name. It does **not** accept `--owner`.

```bash
gh repo create wenzong98/hermes-skills \
  --public \
  --description "My Hermes Agent skill collection"
```

If pushing from an existing local repo, initialize and push separately:

```bash
cd ~/.hermes/skills/research/us-etf-quant-system
git init
git remote add origin https://github.com/wenzong98/hermes-skills.git
git add -A
git commit -m "feat: add us-etf-quant-system skill"
git push -u origin main
```

`gh repo create --push` only works together with `--source`; otherwise the CLI errors with: `the --push option can only be used with --source`.

## What to publish

Publish durable skill assets:

- `SKILL.md`
- `README.md`
- `scripts/*.py`
- curated `references/*.md`, `references/*.json`, `references/*.csv`
- useful charts under `assets/`

Avoid committing transient runtime artifacts unless the user explicitly wants snapshots archived:

- `scripts/__pycache__/`
- `.DS_Store`
- `references/cron_run/.advice_state.json`
- throwaway `references/current_run/` or `references/request_time_run/` outputs if they are not curated

Before future pushes, consider adding a `.gitignore` for Python cache and transient cron/current-run state.

## Update pattern

For future changes:

```bash
cd ~/.hermes/skills/research/us-etf-quant-system
git status --short
git add SKILL.md README.md scripts references assets
git commit -m "docs: update us-etf strategy notes"  # adjust message
git push
```

Always verify the remote URL after pushing:

```bash
gh repo view wenzong98/hermes-skills --web=false
```
