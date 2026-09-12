# Developer Onboarding

1. Read `README.md` and `docs/PROJECT_HANDOFF.md`.
2. Use only `https://github.com/cavack/WFH-ORG`; start from current `main`.
3. Create a short-lived branch/worktree; never implement directly on protected `main`.
4. Use Python 3.13 and Node.js 26 as defined in `.github/runtime-versions.json`.
5. Run `make setup`, then `make validate` or use Docker/CI-equivalent commands.
6. Preserve `SIGNAL_ONLY` and `LIVE_TRADING_ENABLED=false`.
7. Only `ENTRY_READY` is proactively actionable. Lifecycle/Research labels are not entry signals.
8. Database/schema changes require migrations and backup/rehearsal coverage.
9. Production changes flow through guarded `main` CI/deployment; never edit the live checkout as the source of truth.
10. Before handoff, run repository hygiene, full tests, frontend build, clean-install validation, and update docs if an operational contract changed.
