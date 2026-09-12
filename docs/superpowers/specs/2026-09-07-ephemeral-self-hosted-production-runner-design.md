# Ephemeral Self-Hosted Production Runner Design

## Objective

Replace the GitHub-hosted-plus-SSH Production deployment boundary with an ephemeral self-hosted runner on the Production host, while preserving exact-CI-artifact provenance, the explicit recovery gate, rollback, and `LIVE_TRADING_ENABLED=false`.

## Security Boundary

CI, tests, artifact creation, dependency audits, and browser E2E remain on GitHub-hosted runners. Only the final deployment job runs on a self-hosted runner whose custom label is dispatch-specific: `wfh-production-${{ github.run_id }}-${{ github.run_attempt }}`. The runner process runs as the dedicated unprivileged account `wfh-deploy`; it never runs as root and has no general passwordless sudo access.

A root-owned executable `/usr/local/sbin/wfh-production-deploy` is the only privileged command exposed through sudo. `/etc/sudoers.d/wfh-production-deploy` grants `wfh-deploy` `NOPASSWD` access only to that executable. The installer also installs the root-only provisioner and an exact-main host-boundary revision marker; the provisioner is never granted through sudo. The wrapper validates all arguments, that revision marker, exact Git SHA, protected `origin/main`, clean checkout, CI bundle digest inputs, the exact explicit-dispatch run and prerequisite jobs through root-owned GitHub authentication, the root-owned recovery-gate file, and the fixed Production deploy root. It does not execute runner-writable source: it fetches the target into the root-owned Production Git repository, extracts and verifies that commit's canonical `scripts/deploy_production.sh` blob into a root-only temporary file, and invokes that copy.

## Ephemeral Runner Lifecycle

The host-side root provisioner creates or repairs the `wfh-deploy` account and runner directories, keeps the runner root itself root-owned, and obtains a short-lived repository registration token through root's already-authorized local `gh` CLI without copying those credentials to the runner account. It pins the official Linux x64 GitHub Actions runner version, URL, and SHA-256, requires current GitHub release metadata to match all three, verifies the downloaded archive, accepts the exact active workflow-dispatch run ID, verifies that run is the canonical `CI` workflow on current WFH-ORG `main`, configures the runner with `--ephemeral --disableupdate --unattended` plus the unique label `wfh-production-<run-id>-<run-attempt>`, and starts one transient runner process. A failed partial registration is removed by its exact repository runner ID.

The runner accepts one job and unregisters automatically. Its work directory is isolated under `/var/lib/wfh-github-runner`. A new registration token and runner instance are required for every Production dispatch; there is no permanent GitHub runner service. A delayed root reaper runs after 61 minutes. If the runner is still online and idle, it stops that exact transient unit and deletes only that registration after it becomes offline. If `busy=true`, the reaper never stops the runner; it waits for the one-job deployment to finish/unregister within a bounded observation window.

## Workflow Contract

`.github/workflows/ci.yml` remains the only manual dispatch entrypoint. `deploy_production=true` still requires successful `backend`, `frontend`, `dependency-audit`, `container-validation`, and `repository-hygiene` jobs on protected `main`.

`.github/workflows/deploy-production.yml` changes only the execution boundary: `runs-on` becomes `[self-hosted, linux, x64, wfh-production-${{ github.run_id }}-${{ github.run_attempt }}]`, so a queued deployment from another workflow run cannot consume the newly provisioned runner. SSH host/user/key/known-host secrets and SSH/SCP steps are removed. The job checks exact main revision, downloads and verifies the exact CI-tested bundle, validates that it is on a self-hosted Linux x64 runner, and calls the privileged wrapper through `sudo`.

The deployment job receives no reusable SSH credential and does not persist a Production credential in GitHub Secrets.

## Recovery Gate Binding

Before dispatch, the operator must produce a fresh `READY_FOR_EXPLICIT_DISPATCH` report for the exact target SHA. The operator then dispatches `CI`, captures that exact GitHub Actions run ID, and provisions the runner with both the target SHA and run ID; the provisioner rejects a run that is not the active explicit canonical `CI` dispatch on WFH-ORG `main`. That report is atomically copied by root to `/srv/waterfallhunter/runtime/state/release-recovery-gate.json`, owner `root:root`, mode `0640`.

The privileged wrapper refuses deployment unless the file is a regular non-symlink root-owned file, not group/world writable, `status=READY_FOR_EXPLICIT_DISPATCH`, `blocking_reasons=[]`, `source_revision` equals the requested deploy SHA, `required_next_authority=EXPLICIT_WORKFLOW_DISPATCH`, and `valid_until` is still in the future with at least five minutes remaining. Recovery remains revision-bound; the wrapper separately verifies current-dispatch artifact identities and does not require a clean rebuild to reproduce an earlier CI image digest.

This is an additional local dispatch fence; it does not replace the existing backup, independent restore, migration rehearsal, protected-main, or CI trust checks.

## Deployment and Rollback

The wrapper stages the CI bundle into the existing root-owned incoming directory and exports the same `WFH_DEPLOY_SHA`, tested image digests, and bundle SHA consumed by `scripts/deploy_production.sh`. The canonical deploy script remains responsible for migration preflight/apply, image load, revision checks, host integration, health verification, bounded rollback, and `last-successful-deploy.txt`.

No deployment step enables live trading or Telegram signal delivery. Existing fail-closed checks remain unchanged.

## Failure Handling

If no ephemeral runner is online, the workflow remains queued and Production is untouched. If wrapper validation fails, sudo exits before any mutable deployment step. If canonical deploy fails after mutation, its existing bounded rollback path runs. Runner work directories and registration state are cleaned after the job, but release evidence and deploy certificates remain under the existing root-owned runtime/state directories.

## Verification

TDD must enforce: self-hosted runner labels; absence of SSH deployment secrets and SSH/SCP steps; narrow sudo invocation; root wrapper path and argument validation; recovery-report freshness and identity; ephemeral provisioning flags; runner asset digest verification; no persistent runner service; preservation of exact CI bundle verification; and unchanged signal-only safety gates.

After merge, all CI gates run on the new SHA. The certified backup may be reused while fresh, but migration rehearsal and the release recovery gate are regenerated for the new SHA before dispatch.

## First Clean-Repository Cutover

The first WFH-ORG Production deployment begins from a checkout whose `origin` still points at legacy `cavack/wfh`. The privileged wrapper accepts only that legacy origin or the canonical WFH-ORG origin, switches `origin` to `https://github.com/cavack/WFH-ORG.git` immediately before the canonical deploy script fetches `main`, and restores the previous origin automatically if deployment exits non-zero. A successful release keeps WFH-ORG as the canonical Production origin.
