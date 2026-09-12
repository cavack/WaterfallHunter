# Simplified DR Release Gate Design

## Goal

Reduce WaterfallHunter's pre-deployment certification ceremony without weakening independent disaster recovery. The normal Production release gate must rely on authoritative evidence rather than operator-assembled duplicate packets.

## Required safety evidence

A normal Production dispatch is eligible only when all of the following are current and mutually consistent:

1. exact target `main` revision has a trusted successful GitHub `CI` run with the required backend, frontend, dependency-audit, container-validation, and repository-hygiene jobs;
2. the Production SQLite database has a fresh encrypted off-host backup in the private immutable `cavack/wfh-dr` release store;
3. that exact backup has a successful independent GitHub-hosted restore verification bound to its SHA-256, size, schema version, release tag, workflow revision, run ID, and artifact ID;
4. when the release changes the Production schema, a migration-and-rollback rehearsal against the certified backup succeeds and is bound to the exact source revision.

## Removed from the normal pre-dispatch gate

The normal gate no longer requires caller-supplied pass/fail booleans, a duplicate artifact-provenance packet, a pre-deployment staging readiness packet, or a 24-hour shadow-soak packet. These were duplicative or operationally expensive and did not strengthen independent recovery beyond evidence already provided by trusted CI, the off-host backup, restore proof, and migration rehearsal.

The existing strict `deployment_certification_request_v1` evaluator remains available for extended/staging evidence and backward compatibility. It is not deleted or weakened.

## New contract

Internally, add `release_recovery_gate_request_v1` with only:

- `source_revision` — exact lowercase 40-character Git SHA;
- `expected_production_database_path` — canonical absolute Production DB path;
- `backup_certification` — existing `sqlite_remote_backup_certification_v1` document;
- `independent_restore_verification` — existing `github_actions_remote_restore_verification_v1` document;
- `migration_rollback_rehearsal` — existing migration rehearsal document when the certified Production snapshot schema differs from the current runtime schema; otherwise `null`.

The operational CLI accepts the backup certificate and independent-restore report, plus a migration-rehearsal report when required, and constructs this request internally; operators do not hand-build JSON. The evaluator derives whether rehearsal is required from the certified backup `user_version` versus the packaged runtime schema version—never from an operator boolean. It receives `github_repository` and `github_run_id` separately and resolves the CI run authoritatively from GitHub. It does not trust caller claims about CI success.

## Gate semantics

The evaluator reuses the existing fail-closed backup, remote-restore, rehearsal, and GitHub-CI validation code. It returns `release_recovery_gate_report_v1` with status `READY_FOR_EXPLICIT_DISPATCH` only when every reason list is empty; otherwise it returns `NOT_READY` with stable blocking reason codes.

The report remains evidence, not an embedded capability grant: `deployment_allowed=false`, `migration_allowed=false`, `telegram_send_allowed=false`, and `live_trading_allowed=false`. The explicit GitHub workflow dispatch remains the deployment action boundary.

## Freshness and identity

The gate retains the existing seven-day maximum backup age and remote release revalidation. The report itself is valid for at most one hour. The report hash binds the exact source revision, trusted CI run/report hash and tested backend image digest, backup certificate hash, migration rehearsal hash, and independent restore verification identity.

## Production flow

The normal release sequence becomes:

`exact main + trusted CI` → `encrypted off-host backup` → `independent verified restore` → `migration/rollback rehearsal when schema changes` → `release recovery gate READY_FOR_EXPLICIT_DISPATCH` → `workflow_dispatch deploy_production=true` → `existing transactional deployment checks` → `post-deploy health/readiness/dashboard/API/Telegram-read-only verification` → `risk-proportional soak`.

No same-disk backup may satisfy the new gate. No real Telegram send, live order path, or `LIVE_TRADING_ENABLED=true` is introduced.

## Operational authorization

The owner has explicitly directed the project to simplify this certification flow and begin operationalization without further confirmation. This design does not encode or manufacture owner approvals; it only removes redundant pre-dispatch evidence. Operations still fail closed on missing or inconsistent technical evidence.


## Exact-dispatch identity hardening

The simplified gate additionally binds evidence to the current protected `main` revision, the live Production DB device/inode, and the current migration executable fingerprint. Invalid schema-version types fail closed. These checks prevent stale or mislabeled evidence without restoring the removed duplicate ceremony.


Before dispatch, re-resolve protected `main` and require its SHA to equal the evaluated `source_revision`; any change invalidates the ready report and requires re-certification.
