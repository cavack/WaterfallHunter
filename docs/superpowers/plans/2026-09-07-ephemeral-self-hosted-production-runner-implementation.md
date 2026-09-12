# Ephemeral Self-Hosted Production Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the exact CI-tested WFH-ORG main revision through a one-job Production-host runner without storing an SSH private key in GitHub Secrets.

**Architecture:** GitHub-hosted runners continue to build and test. A one-job run-bound `wfh-production-<run-id>-<run-attempt>` runner executes only the deploy job as `wfh-deploy`, and a root-owned narrow sudo wrapper validates recovery, repository, and artifact identities before invoking the existing canonical deploy script.

**Tech Stack:** GitHub Actions, Bash, Linux users/sudoers, Git, Docker Compose, Python 3.13/pytest.

**Spec:** `docs/superpowers/specs/2026-09-07-ephemeral-self-hosted-production-runner-design.md`

## Global Constraints

- `LIVE_TRADING_ENABLED=false` remains invariant through development, CI, deploy, and soak.
- GitHub-hosted runners remain authoritative for CI and artifact creation.
- Production job requires protected `main` and exact CI-tested artifact digests.
- Self-hosted runner is ephemeral, labelled `wfh-production-${{ github.run_id }}-${{ github.run_attempt }}`, and runs as `wfh-deploy`, never root.
- Privilege escalation is limited to `/usr/local/sbin/wfh-production-deploy`.
- A fresh exact-SHA `READY_FOR_EXPLICIT_DISPATCH` report is required before mutable deployment.
- Existing backup, restore, migration, and rollback safety contracts are not weakened.

---
### Task 1: Encode the new deployment contract in RED tests

**Files:**
- Modify: `backend/tests/test_production_deployment_contract.py`
- Modify: `backend/tests/test_release_followup_regressions.py` only if a focused regression belongs there.

**Interfaces:**
- Consumes: existing workflow and deploy-script text contracts.
- Produces: tests requiring self-hosted labels, no SSH deployment, narrow sudo wrapper, recovery report fence, and ephemeral provisioning.

- [ ] Write failing tests requiring the dispatch-bound `runs-on: [self-hosted, linux, x64, wfh-production-${{ github.run_id }}-${{ github.run_attempt }}]`.
- [ ] Write failing tests rejecting SSH/SCP and deploy SSH secret plumbing.
- [ ] Write failing tests requiring one privileged wrapper invocation and the recovery report path.
- [ ] Write failing tests requiring `--ephemeral`, `--disableupdate`, `--unattended`, a dispatch-bound production label, and runner asset SHA verification.
- [ ] Run `pytest -q backend/tests/test_production_deployment_contract.py` and verify failures are limited to missing new behavior.

### Task 2: Add the root-owned deploy wrapper and host installer

**Files:**
- Create: `deploy/self-hosted/wfh-production-deploy`
- Create: `deploy/self-hosted/install-production-runner-host.sh`
- Test: `backend/tests/test_production_deployment_contract.py`

**Interfaces:**
- Consumes: deploy SHA, checkout path, CI bundle path/SHA, three tested image digests, fixed recovery report.
- Produces: validated invocation of `scripts/deploy_production.sh` under root.

- [ ] Implement strict argument parsing and canonical absolute-path validation.
- [ ] Verify root EUID, `SUDO_USER=wfh-deploy`, exact clean checkout SHA, expected GitHub origin, and `origin/main` equality.
- [ ] Verify the root-owned recovery report is READY, unblocked, exact-SHA, and has at least 300 seconds validity.
- [ ] Stage the artifact under `/srv/waterfallhunter/runtime/incoming/<sha>` and invoke the canonical deploy script with exact CI identities.
- [ ] Install `wfh-deploy`, root-owned wrapper, directories, and a sudoers file that allows only the wrapper command.
- [ ] Validate sudoers via `visudo -cf` before installation completes.
- [ ] Run focused tests and `bash -n` until GREEN; commit.
### Task 3: Add ephemeral runner provisioning

**Files:**
- Create: `deploy/self-hosted/provision-ephemeral-runner.sh`
- Modify: `backend/tests/test_production_deployment_contract.py`

**Interfaces:**
- Consumes: local authenticated `gh`, repository `cavack/WFH-ORG`, GitHub runner release metadata.
- Produces: one registered Linux x64 runner with label `wfh-production-<run-id>-<run-attempt>`, running as `wfh-deploy`.

- [ ] Query the official Actions runner release and select the Linux x64 tarball.
- [ ] Require a release-asset SHA-256 digest and verify the download before extraction.
- [ ] Obtain the short-lived repository registration token without logging it.
- [ ] Configure `--ephemeral --disableupdate --unattended --labels "$LABEL" --work _work`, where `$LABEL` is derived from the verified run ID and run attempt.
- [ ] Start one transient runner process as `wfh-deploy`; do not install a permanent runner service.
- [ ] Clean stale local runner state before provisioning the next one.
- [ ] Run focused tests and `bash -n` until GREEN; commit.

### Task 4: Convert the reusable deployment workflow

**Files:**
- Modify: `.github/workflows/deploy-production.yml`
- Modify: `.github/workflows/ci.yml` only to remove obsolete SSH secret plumbing.
- Test: `backend/tests/test_production_deployment_contract.py`

**Interfaces:**
- Consumes: five successful CI jobs and container-validation outputs.
- Produces: a Production-environment job queued for the ephemeral runner and a single narrow sudo-wrapper invocation.

- [ ] Change deploy `runs-on` to `[self-hosted, linux, x64, wfh-production-${{ github.run_id }}-${{ github.run_attempt }}]`.
- [ ] Remove host/user/key/known-host secret declarations, SSH setup, SSH validation, SSH, and SCP steps.
- [ ] Preserve exact-main, artifact download, bundle SHA, image digests, and OCI revision checks.
- [ ] Download artifacts under `${{ runner.temp }}` so the checkout remains clean.
- [ ] Invoke `sudo /usr/local/sbin/wfh-production-deploy` with exact CI identities.
- [ ] Run workflow contract tests and YAML parsing; commit.
### Task 5: Full regression and release documentation

**Files:**
- Modify: deployment/runbook documentation only where SSH is still described as canonical.

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: reviewable PR with complete deployment evidence and no stale SSH-deploy instructions.

- [ ] Run deployment/recovery focused tests.
- [ ] Run the full backend suite in the exact dependency image.
- [ ] Run repository hygiene, credential scan, workflow parse, and `git diff --check`.
- [ ] Verify no registration token, private key, host credential, or runner work data is tracked.
- [ ] Commit documentation if needed, push the branch, and open a PR to protected `main`.

### Task 6: Post-merge runner and recovery rebinding

**Files:**
- No source edits expected; host state and recovery evidence only.

**Interfaces:**
- Consumes: merged main SHA and the still-fresh certified backup.
- Produces: new-SHA CI, migration rehearsal, recovery gate, and an online ephemeral runner.

- [ ] Wait for post-merge main CI success and record exact artifact identities.
- [ ] Install/verify host wrapper and sudoers from exact main.
- [ ] Re-run migration/rollback rehearsal against the certified backup for the new SHA.
- [ ] Re-run the release recovery gate and require `READY_FOR_EXPLICIT_DISPATCH`.
- [ ] Atomically publish the root-owned recovery report to `/srv/waterfallhunter/runtime/state/release-recovery-gate.json`.
- [ ] After explicit dispatch, provision one ephemeral runner for the exact SHA/run ID and verify it is online with the run-attempt-specific production label.

### Task 7: Explicit deploy and Production verification

**Files:**
- No source edits expected.

**Interfaces:**
- Consumes: READY recovery report, online ephemeral runner, protected main, exact CI artifact.
- Produces: Production on the new revision or bounded rollback to the previous certified revision.

- [ ] Dispatch `CI` on `main` with `deploy_production=true`.
- [ ] Confirm deploy lands on the intended ephemeral runner and consumes the current-run artifact.
- [ ] Verify migration v9→v10, three OCI revisions, health/readiness, watchdog, dashboard/API/SSE, WebSocket ownership, backlog, latency, and memory.
- [ ] Keep `LIVE_TRADING_ENABLED=false` and Telegram signal delivery disabled.
- [ ] Run a bounded soak requiring no OOM/unclosed sessions, bounded memory, and backlog catch-up.
- [ ] Record `PRODUCTION_VERIFIED` only after all evidence is fresh.

## Plan Amendment: First WFH-ORG Cutover

Task 2 also owns the canonical remote transition: permit only the certified legacy `cavack/wfh` origin or `cavack/WFH-ORG`, switch legacy `origin` to WFH-ORG before canonical deployment fetch, restore the old origin on non-zero deployment exit, and retain WFH-ORG after success. The deployment contract tests must enforce this transaction.

## Review hardening addendum

- Production runner labels are per-dispatch: `wfh-production-${{ github.run_id }}-${{ github.run_attempt }}`; provisioning consumes the exact target SHA plus workflow run ID and rejects non-canonical/non-active dispatches.
- The runner unit has no `RuntimeMaxSec` hard stop. The 61-minute reaper retires only idle registrations; it observes but never terminates `busy=true` deployments.
- Actionlint accepts only the `wfh-production-*` custom-label family for this boundary.
