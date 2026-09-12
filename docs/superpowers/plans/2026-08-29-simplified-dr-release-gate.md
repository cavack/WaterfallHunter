# Simplified DR Release Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a small authoritative pre-dispatch recovery gate that preserves encrypted independent backup, verified restore, trusted CI, and migration rollback proof while removing duplicate readiness/soak/operator-claim ceremony.

**Architecture:** Keep the existing strict deployment certification unchanged. Add a second request/report contract in `deployment_certification.py` that reuses existing private validators and resolves GitHub CI authoritatively. Add a thin CLI and switch the operational runbook to the new normal path.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, GitHub CLI/API, SQLite backup/rehearsal tooling.

**Spec:** `docs/superpowers/specs/2026-08-29-simplified-dr-release-gate-design.md`

## Global Constraints

- Independent off-host backup and independent restore proof remain mandatory.
- Same-disk backup must never satisfy the normal Production gate.
- Exact-source trusted CI remains mandatory.
- Migration/rollback rehearsal remains mandatory for the current v5→v7 release.
- Existing strict `deployment_certification_request_v1` remains backward compatible.
- `LIVE_TRADING_ENABLED=false`; no live order placement or real Telegram send.
- Deployment remains explicit `workflow_dispatch` on protected `main`.

---

### Task 1: Recovery gate contract and evaluator

**Files:**
- Modify: `backend/src/waterfallhunter/core/deployment_certification.py`
- Modify: `backend/tests/test_deployment_certification.py`

**Interfaces:**
- Produces: `ReleaseRecoveryGateRequest` and `evaluate_release_recovery_gate(request, *, now=None, github_repository=None, github_run_id=None) -> dict[str, Any]`.
- Reuses: `_backup_reasons`, `_independent_remote_restore_reasons`, `_rehearsal_reasons`, and `resolve_github_ci_verification`.

- [ ] Write failing tests proving a complete remote backup + independent restore + sequential rehearsal + trusted exact CI returns `READY_FOR_EXPLICIT_DISPATCH` without readiness/shadow-soak/operator verification fields.
- [ ] Run the focused test and verify RED because the new evaluator does not exist.
- [ ] Implement the minimal request model/evaluator, preserving fail-closed reason codes and one-hour report validity.
- [ ] Run focused tests and verify GREEN.
- [ ] Add negative tests for missing independent restore and CI trust failure; verify `NOT_READY` rather than exceptions.

### Task 2: Thin operational CLI

**Files:**
- Create: `scripts/evaluate_release_recovery_gate.py`

**Interfaces:**
- Consumes: source revision, live Production DB path, backup-certificate file, independent-restore file, optional migration-rehearsal file, output report path, GitHub repository, exact CI run ID. The evaluator decides from certified schema identity whether rehearsal is required.
- Produces: the minimal request internally plus an atomic JSON report; exit `0` only for `READY_FOR_EXPLICIT_DISPATCH`, otherwise exit `2`.

- [ ] Implement the CLI using the same canonical-path and atomic report-writing helpers as existing certification scripts; do not require a hand-built request JSON.
- [ ] Run `python scripts/evaluate_release_recovery_gate.py --help` and a unit-level invocation against synthetic evidence.

### Task 3: Make the simplified flow canonical operational guidance

**Files:**
- Modify: `docs/operations/deployment-certification-runbook.md`

**Interfaces:**
- Documents: exact-main CI → encrypted off-host backup → independent restore → rehearsal → recovery gate → explicit dispatch.

- [ ] Replace the normal pre-dispatch packet section with the simplified recovery gate request and command.
- [ ] Mark the old strict evaluator as optional extended/staging evidence, not a prerequisite for normal dispatch.
- [ ] Preserve post-deploy health/readiness/soak and all safety boundaries.

### Task 4: Regression and release review

**Files:**
- Verify all changed files above.

- [ ] Run focused DR/release tests.
- [ ] Run full backend tests with `.wfh-source-manifest` available when using the CI-equivalent container path.
- [ ] Run repository hygiene, runtime parity, `git diff --check`, and Compose config.
- [ ] Push branch, open PR, inspect exact-head CI/CodeQL/Sonar/CodeRabbit and resolve only current valid findings.
- [ ] Merge only when required contexts are green and review threads are resolved.
- [ ] Re-resolve final `main` SHA and exact successful push CI run.

### Task 5: Operationalize on the exact merged main

**Files/artifacts:**
- No Production source mutation until the gate is ready.
- Create a fresh unique directory under `/srv/wfh-release-backups/remote-dr-<utc>-<sha>/` for local transient restore/rehearsal evidence.
- Publish only encrypted chunks + authenticated manifest to private immutable `cavack/wfh-dr`.

- [ ] Verify Production remains on the old revision, DB v5, healthy, and `LIVE_TRADING_ENABLED=false`.
- [ ] Create the fresh encrypted off-host SQLite Online Backup and verify its remote immutable release.
- [ ] Dispatch the pinned private DR restore workflow with plaintext artifact emission disabled and verify the exact successful restore run.
- [ ] Run sequential v5→v7 migration/rollback rehearsal against the certified restore.
- [ ] Run the direct-evidence recovery-gate CLI against the exact successful main CI run; the CLI builds `release_recovery_gate_request_v1` internally.
- [ ] If `READY_FOR_EXPLICIT_DISPATCH`, explicitly dispatch `CI` on current `main` with `deploy_production=true`; otherwise stop on the returned reason code.
- [ ] Verify exact deployed SHA, DB v7, integrity/FK, container OCI revisions/health, dashboard/API, Telegram read-only state, `LIVE_TRADING_ENABLED=false`, then perform risk-proportional soak before cleanup.


## Exact-dispatch identity hardening

The simplified gate additionally binds evidence to the current protected `main` revision, the live Production DB device/inode, and the current migration executable fingerprint. Invalid schema-version types fail closed. These checks prevent stale or mislabeled evidence without restoring the removed duplicate ceremony.


Before dispatch, re-resolve protected `main` and require its SHA to equal the evaluated `source_revision`; any change invalidates the ready report and requires re-certification.
