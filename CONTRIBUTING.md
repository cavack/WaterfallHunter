# Contributing

WaterfallHunter is a `SIGNAL_ONLY` monitoring and research system. Changes must preserve the boundary that order placement is disabled and `LIVE_TRADING_ENABLED=false` remains mandatory.

## Development workflow

1. Start from current protected `main` and create a short-lived branch or isolated worktree.
2. Keep the change narrowly scoped. Add a focused RED regression before a correctness fix when practical.
3. Run the smallest relevant checks during development, then the repository validation appropriate to the changed surfaces.
4. Open a pull request. Required CI must pass and review conversations must be resolved before merge.
5. Do not deploy from a feature branch. Production deploys only from protected `main` through the explicit guarded workflow described in `docs/DEPLOYMENT.md`.

Common validation commands:

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests
npm --prefix frontend ci
npm --prefix frontend run test:contract
npm --prefix frontend run typecheck
npm --prefix frontend run build
docker compose config --quiet
python scripts/verify_repository_hygiene.py --root .
```

For a release candidate, use `./scripts/validate_clean_install.sh` on a clean committed SHA.

## Documentation-only changes

Documentation must describe the current repository/runtime rather than stale chat or historical snapshots. Check links, `git diff --check`, repository hygiene, and any contract tests that cover the modified documentation or configuration.

## Safety and data handling

Never commit credentials, `.env`, production databases, evidence packets, logs, backups, exchange-account details, or generated research datasets. Never enable live trading or promote weights, thresholds, gates, or lifecycle semantics without explicit approval and documented scientific validation.

Missing evidence is `UNAVAILABLE`, not success. Research rankings, lifecycle labels, replay results, historical outcomes, execution observations, and AI advisory output must not be presented as a parallel actionable signal.

## Dependency changes

Keep dependencies pinned or lockfile-backed. Security findings from dependency audits must be resolved or explicitly documented before merge. Dependabot covers Python, npm, GitHub Actions, and the project Dockerfiles.

Read [SECURITY.md](SECURITY.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), [SUPPORT.md](SUPPORT.md), and [docs/REPOSITORY_GOVERNANCE.md](docs/REPOSITORY_GOVERNANCE.md) before contributing.
