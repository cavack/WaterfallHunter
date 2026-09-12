# Automatic GitHub Production Deployment Design

## Goal
Deploy each fully validated `main` revision automatically to the Ubuntu WaterfallHunter runtime, including controlled database migration and Telegram signal delivery activation, while keeping the product strictly `SIGNAL_ONLY` and preventing exchange order execution.

## Product boundary
WaterfallHunter is `SIGNAL_ONLY`.

- `LIVE_TRADING_ENABLED=false` is mandatory before, during, and after deployment.
- No order placement, order cancellation, automatic position execution, or exchange-account trading action is introduced.
- Product/runtime copy uses `SIGNAL_ONLY` / `signal-only` consistently.
- Historical and research replay may model execution economics. Legacy signed v1 replay artifacts remain readable without redefining the current runtime boundary.

## Trusted deployment architecture
GitHub Actions is the deployment control plane. `.github/workflows/ci.yml` validates the exact revision through backend tests/runtime parity, frontend typecheck/build, dependency audit, repository hygiene, and production-image validation.

The final `deploy-production` CI job:

- depends on every validation job;
- runs only when `github.event_name == 'push'` and `github.ref == 'refs/heads/main'`;
- calls `.github/workflows/deploy-production.yml` through `workflow_call`;
- inherits Production Environment secrets only on that trusted path.

The reusable deployment workflow does not consume `workflow_run` event data and has no manual Production dispatch. `WFH_DEPLOY_SHA` is bound to `github.sha` from the trusted main-push CI run.

## GitHub environment contract
Use a GitHub Environment named `production` with:

- `WFH_PROD_HOST`
- `WFH_PROD_PORT`
- `WFH_PROD_USER`
- `WFH_PROD_SSH_KEY`
- `WFH_PROD_KNOWN_HOSTS`

The workflow uses `contents: read`, validates host/user/port syntax, writes the private key with mode 600, and uses pinned host identity with `StrictHostKeyChecking=yes` and an explicit known-hosts file.

## Exact-revision contract
Before any remote mutation:

1. GitHub checkout must equal `github.sha`.
2. `origin/main` is fetched.
3. The target SHA must equal the current `origin/main` tip, not merely be an ancestor.
4. The host repeats the same current-tip equality check after fetching `origin/main`.

This prevents a slow older CI run from deploying over a newer successful release.

## Host contract
Canonical application root is `/srv/waterfallhunter/app`.

The deploy identity needs only the permissions required to fetch this repository, operate its Docker Compose stack, update the checkout, access the managed application data volume, and read health/revision evidence. Deployment state and locking live below `${WFH_DEPLOY_ROOT}/.deploy`, avoiding a root-only `/var/lock` requirement.

The Production `.env` is host-owned. Deployment fails closed unless `LIVE_TRADING_ENABLED=false`, and Telegram credentials must already exist before release-scoped signal delivery can be enabled.

## Automatic deployment sequence

1. Acquire an exclusive deployment-owned lock.
2. Verify the target is exactly the current main tip.
3. Resolve the prior certified/running revision for rollback provenance.
4. Assert the signal-only runtime boundary.
5. Checkout and build exact revision-labelled backend, frontend, and watchdog artifacts.
6. Create an SQLite backup with integrity verification and SHA-256 evidence.
7. Run migration preflight.
8. Mark the database as potentially mutated before invoking migration apply, so partial failure is cleanup-aware.
9. Apply the managed migration with source-revision provenance.
10. Capture a fresh Telegram activation timestamp and enable only signal delivery controls.
11. Start/update the Compose stack without deleting persistent volumes.
12. Require backend `/api/livez` and `/api/readyz`.
13. Require healthy backend, frontend, and watchdog containers.
14. Verify all running OCI revision labels equal the target SHA.
15. Verify the effective backend configuration still has `LIVE_TRADING_ENABLED=false`.
16. Persist the successful deployment certificate.
17. Enforce bounded certified database-backup retention.

## Failure and rollback semantics
Explicit failures and `ERR`, `TERM`, `HUP`, and `INT` converge on a bounded cleanup path.

Before mutable Production steps, cleanup restores prior workspace/environment state where required. Once migration may have changed the database, the previous revision may be restarted only after its managed-schema preflight proves compatibility with the current schema. If compatibility cannot be certified, automatic source rollback stops and the backup/evidence is retained for operator recovery.

When rollback is permitted, the previous environment and revision are restored, artifacts are rebuilt, the Compose stack is restarted, backend readiness is checked, all three release containers must be healthy, OCI revisions must match the previous SHA, and the signal-only boundary is revalidated.

## Telegram semantics
Telegram delivery is part of the release path only for signal notifications.

- Credentials are never committed.
- Delivery activation occurs after build/migration prerequisites.
- `TELEGRAM_SIGNAL_DELIVERY_CUTOVER_AT` is captured at activation time, preventing historical queued events from becoming newly eligible merely because deployment enabled delivery.
- Telegram activation never authorizes exchange order execution.

## Replay compatibility
Current runtime/product contracts emit `SIGNAL_ONLY`. Historical v1 execution-plan contract identifiers are retained where changing them would break artifact lineage. Replay compatibility may recognize the legacy execution-mode value only when paired with the legacy v1 plan contract; the original plan material and hash are validated unchanged before the plan is applied. Replay output remains `SIGNAL_ONLY`.

## Verification
The PR must demonstrate RED→GREEN evidence for:

- trusted main-push-only deployment chaining;
- exact current-main-tip binding at both GitHub and host boundaries;
- pinned SSH host verification and least privilege;
- deployment locking;
- backup-before-migration ordering;
- migration-aware failure state;
- release-time Telegram cutover;
- `LIVE_TRADING_ENABLED=false` fail-closed behavior;
- health certification for backend, frontend, and watchdog;
- interruption-aware cleanup and bounded rollback;
- bounded backup retention;
- canonical `SIGNAL_ONLY` product/runtime wording;
- legacy signed v1 replay compatibility;
- absence of destructive volume removal and plaintext secrets.

## Non-goals
- No automatic or manual live order placement.
- No `LIVE_TRADING_ENABLED=true` path.
- No destructive volume recreation.
- No unauthenticated SSH host trust.
- No Production deployment from a pull request, arbitrary branch, caller-supplied revision, or stale main revision.
