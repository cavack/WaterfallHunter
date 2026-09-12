# Signal-only deployment certification runbook

This runbook prepares evidence. It does not authorize a Production backup,
migration, deployment, restart, Telegram send, feature promotion, or live order.
Every artifact is immutable, hash-bound, and created at a new path.

## 1. Independent backup gate

Prerequisites:

- a mounted destination on a device and failure domain independent from the
  Production database;
- enough capacity for the backup, isolated restore, migration clone, rollback
  clone, and reports;
- an operator acting under an authorized release/change window. The technical
  gate does not require a magic approval phrase; it fails closed on the actual
  source, destination, identity, restore, and hash evidence.

Run from the tested source revision:

```bash
PYTHONPATH=backend/src:. python scripts/certify_sqlite_backup.py \
  --source /absolute/production/registry.db \
  --backup /independent/wfh/registry-<utc>-<sha>.db \
  --restore-target /independent/wfh/restore-<utc>-<sha>.db \
  --report /independent/wfh/backup-certification-<utc>-<sha>.json \
  --source-failure-domain production-block-volume \
  --destination-failure-domain independent-backup-volume
```

The command uses SQLite Online Backup API, rejects same-device/same-domain
targets, never overwrites, runs full integrity and foreign-key checks, records
all table counts and schema identity, binds source identity to the opened backup
inode, hash-binds `backup_started_at`/`backup_completed_at`, performs an
isolated restore, and compares the restored snapshot. A local `/srv` copy is not
an independent backup. Deployment certification rejects backups older than seven
days (`MAXIMUM_BACKUP_AGE_SECONDS`) and re-audits the immutable backup file
read-only before accepting rollback evidence.

### Encrypted private-GitHub alternative

When no independent block device exists, a recorded owner authorization may
instead approve an encrypted off-host backup to the private `cavack/WFH-ORG-dr`
repository. If the active release handoff already records that exact approval,
do not request it again. This does not authorize migration or deployment.

Preconditions:

- `cavack/WFH-ORG-dr` is private and not archived;
- `/root/.wfh-dr/wfh-dr-aes256.key` is a root-owned `0600` regular file and its
  value is never printed;
- the `WFH_DR_AES256_KEY_B64` Actions secret exists in `cavack/WFH-ORG-dr`;
- SQLite Online Backup—not raw `cp`—creates the temporary snapshot;
- the release tag and staging paths are new and the destination failure domain
  names `github.com` truthfully.

Run from an isolated worktree at the exact tested `main` revision:

```bash
PYTHONPATH=backend/src:. python scripts/certify_remote_sqlite_backup.py \
  --source /absolute/production/waterfall_registry.db \
  --staging-dir /srv/wfh-release-backups/remote-dr-<utc>-<sha> \
  --restore-target /srv/wfh-release-backups/remote-dr-<utc>-<sha>/restored.db \
  --report /srv/wfh-release-backups/remote-dr-<utc>-<sha>/backup-certification.json \
  --key-file /root/.wfh-dr/wfh-dr-aes256.key \
  --remote-repository cavack/WFH-ORG-dr \
  --release-tag wfh-production-dr-<utc>-<sha> \
  --source-failure-domain production-root-vda1 \
  --destination-failure-domain github.com-private-release:cavack/WFH-ORG-dr
```

Only the authenticated manifest and encrypted chunks may be published. After
GitHub asset IDs, sizes, and SHA-256 digests are verified, the command removes
the plaintext staging snapshot before re-downloading, decrypting, restoring,
and auditing the release. The resulting certificate must say
`BACKUP_RESTORE_CERTIFIED` and keep both Production authorization flags false.

Prove key recovery and restoreability outside the Production host by dispatching
the private DR workflow with plaintext artifact emission disabled, then seal its
exact successful run and report artifact:

```bash
DR_WORKFLOW_REVISION=add3f01cf3b9f3e55d735294dae99d5a5792b5c2
test "$(gh api repos/cavack/WFH-ORG-dr/commits/main --jq .sha)" = "$DR_WORKFLOW_REVISION"

gh workflow run restore.yml -R cavack/WFH-ORG-dr --ref main \
  -f release_tag=wfh-production-dr-<utc>-<sha> \
  -f emit_plaintext_artifact=false

PYTHONPATH=backend/src:. python scripts/verify_github_remote_restore.py \
  --backup-certification /srv/wfh-release-backups/remote-dr-<utc>-<sha>/backup-certification.json \
  --github-repository cavack/WFH-ORG-dr \
  --github-run-id <exact-successful-restore-run-id> \
  --report /srv/wfh-release-backups/remote-dr-<utc>-<sha>/independent-restore-verification.json
```

The deployment request must include this
`github_actions_remote_restore_verification_v1` object as
`independent_restore_verification`. The authoritative verifier also requires the
GitHub Actions run `head_sha` to equal the reviewed `DR_WORKFLOW_REVISION` above;
a later or unreviewed DR workflow revision is rejected fail-closed. Remote backup
evidence cannot reach owner approval readiness without this independent proof.

## 2. Staging migration and rollback rehearsal

This is a write only to isolated clone files. It is part of the authorized
release-certification operation and does not require a separate chat approval.
Production is not a target of this rehearsal.

```bash
PYTHONPATH=backend/src:. python scripts/rehearse_sqlite_migration.py \
  --backup-certification /independent/wfh/backup-certification-<utc>-<sha>.json \
  --migration-target /independent/wfh/migration-stage-<utc>-<sha>.db \
  --rollback-target /independent/wfh/rollback-stage-<utc>-<sha>.db \
  --source-revision <40-character-tested-git-sha> \
  --report /independent/wfh/migration-rehearsal-<utc>-<sha>.json
```

The migration clone uses the canonical `waterfallhunter.migrate_database`
boundary and verifies the current schema. The rollback clone is freshly restored
from the same certified artifact and must match the pre-migration schema and all
table counts. Production is not a target of this rehearsal.

For the remote certificate, use the retained restored baseline and the
single-working-target mode to cap disk usage:

```bash
PYTHONPATH=backend/src:. python scripts/rehearse_sqlite_migration.py \
  --backup-certification /srv/wfh-release-backups/remote-dr-<utc>-<sha>/backup-certification.json \
  --sequential-working-target /srv/wfh-release-backups/remote-dr-<utc>-<sha>/sequential-rehearsal.db \
  --source-revision <40-character-tested-git-sha> \
  --report /srv/wfh-release-backups/remote-dr-<utc>-<sha>/migration-rehearsal.json
```

The migrated artifact is deleted before the same path is restored to the
baseline. Success requires `sqlite_migration_rollback_rehearsal_v2`, retained
rollback evidence matching the baseline, and both Production authorization
flags false.

## 3. Normal pre-dispatch recovery gate

The normal Production pre-dispatch gate is intentionally small. There is no
operator-assembled request packet. Give the evaluator the authoritative backup
and restore evidence plus the exact current `main` revision and live SQLite
path; include migration-rehearsal evidence only when the certified Production
snapshot schema differs from the runtime schema. The CLI constructs
`release_recovery_gate_request_v1` internally.

The evaluator independently resolves GitHub CI from the supplied repository and
run ID. Do not add caller-asserted CI booleans, staging readiness, or a 24-hour
shadow-soak packet to this normal gate. Those duplicated authoritative CI and
recovery evidence and delayed releases without improving recoverability.

Evaluate the minimal gate:

```bash
PYTHONPATH=backend/src:. python scripts/evaluate_release_recovery_gate.py \
  --source-revision <40-character-current-main-sha> \
  --production-database /absolute/production/waterfall_registry.db \
  --backup-certification /srv/wfh-release-backups/remote-dr-<utc>-<sha>/backup-certification.json \
  --independent-restore-verification /srv/wfh-release-backups/remote-dr-<utc>-<sha>/independent-restore-verification.json \
  --migration-rehearsal /srv/wfh-release-backups/remote-dr-<utc>-<sha>/migration-rehearsal.json \
  --report /srv/wfh-release-backups/remote-dr-<utc>-<sha>/release-recovery-gate.json \
  --github-repository cavack/WFH-ORG \
  --github-run-id <exact-successful-main-ci-run-id>
```

For the current v5→v7 release, `--migration-rehearsal` is mandatory because the
certified Production snapshot is below the runtime schema version. For a future
release whose certified snapshot already matches the runtime schema, omit that
argument; the evaluator derives this from certified `backup_audit.user_version`
rather than trusting an operator boolean.

Success is `READY_FOR_EXPLICIT_DISPATCH`. The report still keeps
`deployment_allowed=false`, `migration_allowed=false`,
`telegram_send_allowed=false`, and `live_trading_allowed=false`: it is
cryptographically and operationally bound evidence, not a reusable deployment
credential. A local or same-disk backup can never satisfy this gate.

### Optional extended/staging certification

`scripts/evaluate_deployment_certification.py` and
`deployment_certification_request_v1` remain supported for teams that want the
older extended staging/readiness/shadow-soak evidence packet. That strict mode is
backward compatible, but it is not a prerequisite for the normal Production
dispatch path once the recovery gate above is READY.

## 4. Explicit dispatch and post-deploy verification

After `READY_FOR_EXPLICIT_DISPATCH`, the deployment action boundary is the
protected GitHub Actions dispatch on the exact current `main` revision:

```text
workflow = CI
branch = main
deploy_production = true
```

The dispatch reruns the required CI jobs and deploys only the exact CI-tested
artifact family. Production migration and deployment must preserve the rollback
point, keep `LIVE_TRADING_ENABLED=false`, recheck schema/readiness after each
stage, and stop on any mismatch, OOM, readiness regression, or unexpected
write. No real Telegram message is sent by certification. Live trading remains
outside this release policy.

After cutover, verify exact deployed SHA, backend/frontend/watchdog OCI revision
labels, DB schema/integrity/foreign keys, `/livez`, `/readyz`, `/healthz`, key
Dashboard/API paths, Telegram configuration/outbox read-only state, restart
counts, resource usage, and a risk-proportional runtime soak. Cleanup remains
post-certification only.
