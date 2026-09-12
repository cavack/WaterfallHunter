# Automatic Signal-Only Production Deployment Implementation Plan

**Goal:** Automatically deploy each fully validated `main` revision to the Ubuntu WaterfallHunter runtime, including controlled SQLite migration and Telegram signal delivery, while preserving the hard product boundary `SIGNAL_ONLY` and `LIVE_TRADING_ENABLED=false`.

**Canonical contract:** `ExecutionMode.SIGNAL_ONLY = "SIGNAL_ONLY"`. Historical replay may model execution economics, but runtime/product behavior never places, changes, or cancels exchange orders.

## Implemented architecture

- `.github/workflows/ci.yml` is the validation and orchestration workflow.
- The `deploy-production` job depends on backend, frontend, dependency audit, container validation, and repository-hygiene jobs.
- The deploy job runs only for `push` events on `refs/heads/main`; pull requests never enter the Production environment.
- `.github/workflows/deploy-production.yml` is a reusable `workflow_call` child workflow, not an independently privileged `workflow_run` consumer.
- The deployment SHA is `github.sha`; both GitHub Actions and the host require that SHA to equal the current `origin/main` tip before mutation begins.
- Production SSH uses pinned `WFH_PROD_KNOWN_HOSTS`, `StrictHostKeyChecking=yes`, least-privilege `contents: read`, and the `production` GitHub Environment.
- There is no manual Production dispatch and no dry-run branch.

## Host deployment sequence

1. Validate the exact 40-character target SHA, Production `.env`, backup-retention configuration, and required commands.
2. Create the deployment-owned state directory and acquire its exclusive `flock`.
3. Fetch `origin/main` and require exact equality between `WFH_DEPLOY_SHA` and the current main tip.
4. Resolve the previous certified/running revision for bounded rollback provenance.
5. Assert `LIVE_TRADING_ENABLED=false` before changing runtime state.
6. Checkout the exact target revision and validate Compose configuration.
7. Build revision-labelled backend, frontend, and watchdog images.
8. Create and checksum an SQLite backup before migration.
9. Run migration preflight; mark migration as potentially mutable before `--apply` so partial failures remain rollback-aware.
10. Activate Telegram signal delivery only after prerequisites pass and capture a fresh release cutover timestamp.
11. Start/update the Compose stack without deleting persistent volumes.
12. Require backend `/api/livez` and `/api/readyz`, plus healthy backend, frontend, and watchdog containers.
13. Require all running OCI revision labels to equal the exact target SHA and verify effective runtime configuration still has live trading disabled.
14. Persist the successful deployment certificate, then enforce bounded database-backup retention.

## Failure semantics

- Explicit failures and `ERR`, `TERM`, `HUP`, and `INT` paths enter the same bounded cleanup path.
- Before mutable Production steps, failure restores the prior workspace/environment state where necessary.
- After a migration may have mutated data, rollback to the previous revision is allowed only when that revision passes managed-schema compatibility preflight.
- After runtime replacement, rollback must restore the previous environment, rebuild/start the previous revision, and re-certify backend readiness, all container health, OCI revision identity, and the signal-only boundary.
- If rollback cannot be certified, the deployment fails loudly and preserves backup/evidence for operator recovery.

## Telegram semantics

- `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` remain host-owned secrets.
- Deployment sets only release delivery controls, including `TELEGRAM_SIGNAL_DELIVERY_ENABLED=true` and the release-time `TELEGRAM_SIGNAL_DELIVERY_CUTOVER_AT`.
- Pre-cutover queued events remain suppressed.
- Telegram delivery sends signals only and never authorizes exchange order execution.

## Verification gates

- [x] TDD contract tests for SIGNAL_ONLY terminology and deploy boundaries.
- [x] Exact-main-tip deployment gate at GitHub and host layers.
- [x] Pinned SSH host identity and least privilege.
- [x] Backup-before-migration ordering and migration-aware rollback state.
- [x] Release-time Telegram cutover.
- [x] Health certification for backend, frontend, and watchdog.
- [x] Signal-aware interruption cleanup.
- [x] Bounded backup retention.
- [x] Full backend tests, runtime parity, frontend typecheck/build, dependency audit, repository hygiene, and exact production-image validation run in CI.
- [ ] Final review-thread closure and exact-head re-review.
- [ ] Merge only after explicit owner approval and all required gates are green.
- [ ] After merge, verify the `main` CI run invokes `deploy-production` automatically and inspect the resulting Production deployment certificate.
