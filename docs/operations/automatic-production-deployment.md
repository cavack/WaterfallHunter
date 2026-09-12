# Explicit Production Deployment

WaterfallHunter Production is `SIGNAL_ONLY`. The supported runtime must keep `LIVE_TRADING_ENABLED=false` and Telegram signal delivery disabled; deployment never authorizes exchange orders or Telegram signal sends.

## Trigger

A normal push to protected `main` runs backend, frontend, dependency-audit, container-validation, and repository-hygiene verification only. After release recovery evidence is fresh, an operator explicitly dispatches the `CI` workflow on `main` with `deploy_production=true`. The dispatch reruns all required CI jobs and only then makes the Production deployment job eligible.

`.github/workflows/deploy-production.yml` is a reusable `workflow_call` child. There is no `workflow_run` trust boundary and no caller-supplied revision: deployment uses the dispatched workflow's exact `github.sha`, which must equal protected `origin/main` at both workflow and host boundaries.

## Production runner boundary

The final job uses `[self-hosted, linux, x64, wfh-production-${{ github.run_id }}-${{ github.run_attempt }}]`. The custom label is unique to the explicit workflow run, preventing an older queued deployment from consuming a freshly registered runner. CI and artifact creation remain on GitHub-hosted runners; only the final deployment job runs on the Production host.

The runner process uses the dedicated `wfh-deploy` account and is ephemeral. It accepts one job and unregisters; there is no permanent Actions runner service. The account is not in the Docker group and has no general sudo policy.

The only privileged command allowed to `wfh-deploy` is:

```text
/usr/local/sbin/wfh-production-deploy
```

The root-owned wrapper validates exact main SHA, clean checkout, CI bundle SHA and image-digest manifest, root-owned recovery evidence, canonical deploy root, and the Production Git origin. It also uses root's GitHub CLI boundary to require that the supplied run ID is the in-progress explicit `CI` dispatch on WFH-ORG `main`, that all five prerequisite jobs succeeded, and that all three image digests plus the bundle SHA occur exactly once in that run's `container-validation` log. It never executes a script from the runner-writable checkout. After transactionally selecting the canonical WFH-ORG origin, it fetches the exact target commit into the root-owned Production repository, extracts that commit's canonical `scripts/deploy_production.sh` blob into a root-only temporary file, verifies the Git blob identity, and executes that copy.

## Host preparation

Canonical checkout:

```text
/srv/waterfallhunter/app
```

Install the host boundary from a clean checkout of the exact reviewed WFH-ORG `origin/main` revision:

```bash
sudo deploy/self-hosted/install-production-runner-host.sh
```

The installer refuses a non-canonical origin, a dirty checkout, or a checkout whose `HEAD` differs from current `origin/main`. It creates or verifies `wfh-deploy`, `/var/lib/wfh-github-runner`, the root-owned wrapper, the root-only `/usr/local/sbin/wfh-provision-production-runner`, the exact-SHA marker `/etc/waterfallhunter/runner-boundary-revision`, and `/etc/sudoers.d/wfh-production-deploy`. The installer runs `visudo -cf` before accepting the sudoers file. Only the deploy wrapper is granted through sudo; provisioning remains a root operator action.

For each Production dispatch, first start the canonical `CI` workflow and capture its numeric run ID. Root then provisions exactly one runner with `/usr/local/sbin/wfh-provision-production-runner <sha> <run-id>`. GitHub CLI authentication remains only in root's existing host configuration; it is not copied to `wfh-deploy`. The root provisioner verifies that the requested SHA is still current `main` and that the supplied run ID is an active explicit `workflow_dispatch` of `.github/workflows/ci.yml` on WFH-ORG `main`, obtains a short-lived registration token, and passes only that token to the runner's one-time configuration step.

The runner package is pinned to official `actions/runner` release `v2.337.0`, asset `actions-runner-linux-x64-2.337.0.tar.gz`, and SHA-256 `70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613`. Provisioning requires GitHub release metadata to agree with the pinned name, URL, and digest before downloading and verifying the archive. It then configures `--ephemeral --disableupdate --unattended --labels wfh-production-<run-id>-<run-attempt>` and starts one transient process as `wfh-deploy`. The runner service itself has no hard lifetime cap that could terminate an active migration or rollback. A root reaper starts after 61 minutes: it stops/deletes the exact runner only when idle; if the runner is busy, it waits for the deployment to finish and never kills the active job. Failed partial provisioning is likewise removed by exact runner ID.

The host-owned environment remains `/etc/waterfallhunter/waterfallhunter.env`. It must keep at least:

```text
LIVE_TRADING_ENABLED=false
TELEGRAM_SIGNAL_DELIVERY_ENABLED=false
```

Secrets and deployment state are never stored in the Git checkout.

## Pre-dispatch recovery fence

The certified backup and independent restore may be reused only while the canonical freshness policy accepts them. Migration/rollback rehearsal must be regenerated for the exact target main SHA, and `scripts/evaluate_release_recovery_gate.py` must return `READY_FOR_EXPLICIT_DISPATCH` with `blocking_reasons=[]`. Publish that exact report atomically as root to `/srv/waterfallhunter/runtime/state/release-recovery-gate.json` with owner `root:root` and mode `0640`.

The wrapper rejects a report for another revision, a report with less than five minutes of validity remaining, or any authority other than `EXPLICIT_WORKFLOW_DISPATCH`. Recovery evidence is bound to revision, backup, restore, and migration rehearsal; artifact identities are independently bound to the exact current dispatch because a clean rebuild is not assumed to reproduce a prior image digest. The report authorizes only the explicit workflow dispatch; it does not itself authorize deployment, migration, Telegram delivery, or live trading.

## Deployment sequence

1. Install the host boundary from the clean exact current WFH-ORG main checkout and verify its revision marker.
2. Publish the exact-SHA READY recovery report.
3. Explicitly dispatch the `CI` workflow on protected `main` with `deploy_production=true` and record its numeric GitHub Actions run ID.
4. Provision `/usr/local/sbin/wfh-provision-production-runner <sha> <run-id>`; it registers the unique label `wfh-production-<run-id>-<run-attempt>` only for that active dispatch.
5. GitHub-hosted jobs rerun backend, frontend, dependency, container, and repository gates and create the three-image bundle. The Production job can only land on the runner carrying its own run-specific label.
6. The Production job downloads the artifact from that same workflow run into `RUNNER_TEMP`, verifies the bundle SHA plus all four manifest identities, and calls only the narrow sudo wrapper. The wrapper snapshots runner-owned manifest and bundle bytes through a process running as `wfh-deploy`; root never reopens those mutable paths with elevated read authority.
7. The wrapper rechecks current main, recovery evidence, root-owned Production checkout/origin, the installed boundary revision, and the copied bundle before invoking the Git-verified canonical deploy script.
8. The canonical deploy script takes the deploy lock, pins the rollback images, keeps both safety settings disabled, loads the CI bundle without rebuilding, creates a fresh local DB backup, runs migration preflight and apply, and replaces the three release containers without deleting volumes.
9. The deploy script requires live/readiness, backend/frontend/watchdog and monitoring health, exact OCI revisions, signal-only safety, and public dashboard reachability before writing `last-successful-deploy.txt`.

## Failure and rollback

Wrapper failure before the canonical script begins leaves runtime and database state untouched and restores the legacy origin if the first-cutover switch had occurred. Failures after a mutable deploy step use the canonical bounded cleanup path. The previous runtime is restored only when its schema preflight accepts the current database; otherwise the release containers remain quarantined for evidence-backed recovery.

Never use `docker compose down -v`, delete the Production database volume, rebuild a supposedly equivalent artifact on the host, or run an older runtime against a newer schema without positive compatibility evidence. Preserve the newest backup, recovery report, workflow logs, and deployment certificate on failure.

## Post-cutover certification and cleanup

A successful workflow is only `DEPLOYED_UNVERIFIED` until direct checks confirm exact backend/frontend/watchdog revision labels, WFH-ORG origin, schema v10, SQLite integrity and foreign keys, signal-only and Telegram safety, API/dashboard/SSE/watchdog health, controlled scanner backlog, bounded WebSocket ownership, and a meaningful memory soak.

Only after `PRODUCTION_VERIFIED`: remove the abandoned dedicated SSH deployment public key and obsolete host/port/user GitHub deployment values, verify that no private deploy key or known-host secret was stored, remove leftover ephemeral runner registration and local runner work data, and retain the certified recovery chain and rollback artifacts for their required windows.
