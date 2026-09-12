# Deployment

Production deploys only from protected `main`, only after required CI jobs pass, and only from an explicit `workflow_dispatch` with `deploy_production=true`. A normal push to `main` verifies but never deploys.

## Trusted chain

`protected main -> exact-SHA GitHub-hosted CI -> certified off-host backup/restore + migration/rollback rehearsal -> READY_FOR_EXPLICIT_DISPATCH -> ephemeral run-bound wfh-production runner -> repeated CI checks -> exact bundle verification -> narrow root wrapper -> migration -> Compose replacement -> health/revision certification`.

The deployer refuses stale SHA, dirty checkout, stale/mismatched recovery evidence, invalid bundle/digests, missing host environment, failed migration preflight, unhealthy services, OCI revision mismatch, or safety-setting drift.

## Production trust boundary

Builds, tests, dependency audits, browser E2E, and container artifacts run on GitHub-hosted runners. Only the final deploy job runs on the Production host through a one-job self-hosted runner labelled `wfh-production-<github-run-id>-<run-attempt>`, unique to the explicit dispatch.

The runner account is `wfh-deploy` and is not root. Its parent runner directory is root-owned, it is not in the Docker group, and it has no general sudo grant. The only privileged command allowed is the root-owned `/usr/local/sbin/wfh-production-deploy`, which revalidates the exact main checkout, the installed host-boundary revision marker, CI bundle identities against the exact current dispatch through GitHub's API, the root-owned recovery report, and the canonical Production root. The privileged process executes only the target commit's Git-verified deploy-script blob extracted from the root-owned Production repository, never code from the runner-writable checkout. Runner provisioning uses the separately installed root-only `/usr/local/sbin/wfh-provision-production-runner` and is not a sudo capability of the runner user.

No SSH private deployment key is stored in GitHub Secrets. Root's existing GitHub CLI authentication is not copied to the runner account. A new short-lived repository registration token provisions each ephemeral runner; the runner unregisters after one job and is not installed as a permanent service. A 61-minute reaper retires an unconsumed idle runner by exact ID. It never applies a hard lifetime cap to the runner service and never stops a `busy=true` deployment; it waits for the one-job runner to finish/unregister. The runner release version, official asset URL, and SHA-256 are pinned and cross-checked against GitHub release metadata.

## Host-owned state

Secrets remain in `/etc/waterfallhunter/waterfallhunter.env` with restrictive permissions. Release state is under `/srv/waterfallhunter/runtime`; the dispatch recovery fence is `/srv/waterfallhunter/runtime/state/release-recovery-gate.json`. Certified DB recovery artifacts remain outside the Git checkout.

The canonical source remote is `https://github.com/cavack/WFH-ORG.git`. The first WFH-ORG cutover switches the legacy Production checkout origin transactionally: success keeps WFH-ORG; failure restores the prior origin while the existing bounded runtime rollback executes.

## Safety

`LIVE_TRADING_ENABLED=false` and Telegram signal delivery remain disabled without separate operator approval. Deployment does not authorize real orders or Telegram signal sends.

## Rollback

Rollback is permitted only when the previous runtime is schema-compatible with the current database. If compatibility cannot be proven, the application is quarantined and certified recovery evidence is preserved.

## Operator rule

There is no unreviewed local deploy shortcut. After a fresh exact-SHA `READY_FOR_EXPLICIT_DISPATCH` report exists, explicitly dispatch the `CI` workflow on protected `main` with `deploy_production=true`, record that workflow run ID, then provision exactly one ephemeral runner for `<sha> <run-id>`. The job must consume the artifact created by that same dispatch.

## Immutable image pinning

The deployer pins each exact CI-tested image to release-specific `wfh-release-*:<sha>` tags and composes Production through the host-owned image override. Running OCI revision labels must equal the dispatched main SHA before a release can be certified.
