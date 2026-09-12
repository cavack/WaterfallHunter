# WaterfallHunter v7 Production Release

Release source: `feat/wave7-deployment-certification`.

This release consolidates the stacked Wave 1C through Wave 7 implementation onto `main`, including the new dashboard, typed/streamed dashboard contracts, Backtest Lab, Lifecycle V2 shadow monitoring, strict scientific validation, risk-first simulated execution/replay, and deployment certification tooling.

Production safety boundary:

- `LIVE_TRADING_ENABLED=false` remains mandatory.
- This release is SIGNAL_ONLY; order execution is disabled.
- No live order path is authorized.
- Telegram delivery remains separately controlled.
- Database migration is explicit and must be run before application restart.
- Deployment must use the exact merged `main` revision.

Permanent runtime requirements:

- Docker Compose services use a non-empty restart policy (`unless-stopped` or equivalent).
- Backend/frontend/watchdog are rebuilt from the exact merged revision.
- Database migration completes successfully before service cutover.
- Post-deploy health and dashboard smoke checks must pass.

Rollback: retain the previous application images and a certified database backup before migration/cutover.
