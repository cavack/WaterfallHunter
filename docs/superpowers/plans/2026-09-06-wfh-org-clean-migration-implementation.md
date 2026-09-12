# WFH-ORG Clean Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a clean, defect-hardened, release-certified `WFH-ORG` repository and cut Production over only after independent DR and runtime verification.

**Architecture:** Treat `wfh`, `wfh-dr`, current Production, and open fix branches as evidence inputs. Admit files into a fresh-history repository only through a complete provenance/disposition manifest; integrate verified defect fixes before source import; preserve existing strategy semantics while rebuilding CI/DR/runtime provenance around one canonical repository.

**Tech Stack:** Python/FastAPI/SQLite, CCXT Pro, Next.js/TypeScript, Docker Compose, Nginx, Prometheus/Grafana/Alertmanager, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-06-wfh-org-clean-migration-design.md`

## Global Constraints

- `LIVE_TRADING_ENABLED=false` for every development, test, migration, deployment, and soak step.
- Automatic Telegram signal delivery remains disabled unless separately authorized.
- No ScoreV2, lifecycle, Anti-Chase, TP/SL, leverage, ranking, or execution-policy semantic change is allowed as incidental cleanup.
- No Production mutation before exact-revision backup/restore and rollback gates pass.
- No file enters the canonical runtime tree without a migration-manifest disposition.
- Production process health alone is not acceptance; memory, API latency, semantic freshness/backlog, WebSocket ownership, dashboard/SSE, and edge routing are mandatory gates.

---

### Task 1: Freeze and Inventory Every Source Input

**Files:**
- Create: `migration/source-manifest.json`
- Create: `migration/source-manifest.schema.json`
- Create: `migration/README.md`
- Test: `scripts/verify_migration_manifest.py`
- Test: `backend/tests/test_migration_manifest.py`

**Interfaces:**
- Consumes: Git trees from `cavack/wfh@<frozen-sha>` and `cavack/wfh-dr@<frozen-sha>` plus explicit open-fix heads.
- Produces: one manifest entry per tracked source file with `source_repo`, `source_sha`, `source_path`, `source_blob_sha`, `disposition`, `destination_path`, `rationale`, and `verification`.

- [ ] Freeze exact source refs and record them in `migration/README.md`.
- [ ] Enumerate both repository trees and generate the initial manifest with disposition `UNREVIEWED` only as a temporary generator state.
- [ ] Add a schema that rejects missing provenance fields, duplicate source identities, duplicate canonical destinations, and unknown final dispositions.
- [ ] Add a verifier that fails if any frozen source file is absent from the manifest or any final entry remains `UNREVIEWED`.
- [ ] Run verifier against an intentionally incomplete fixture and confirm RED.
- [ ] Complete the generator/verifier implementation and confirm GREEN.
- [ ] Commit the inventory layer independently.

### Task 2: Reconcile Active Fix Branches on One Integration Head

**Files:**
- Modify after import candidate exists: `backend/requirements.lock`
- Create after import candidate exists: `backend/tests/test_dependency_lock_consistency.py`
- Create after import candidate exists: `scripts/check_python_runtime.py`
- Modify after import candidate exists: `backend/tests/test_wfh_council_hooks.py`
- Modify after import candidate exists: WebSocket manager/metrics files changed by PR #128.
- Test: focused dependency/harness/WebSocket regression suites.

**Interfaces:**
- Consumes: baseline `wfh` SHA plus PR #127 head and PR #128 head.
- Produces: one combined, conflict-resolved candidate tree; no merge of legacy history is required.

- [ ] Fetch exact patches for #127 and #128 and map overlapping files.
- [ ] Reproduce the dependency-lock RED condition against the baseline.
- [ ] Reproduce timed-out close ownership loss against the baseline.
- [ ] Apply the smallest accepted changes from #127 and #128 to the clean candidate tree.
- [ ] Run both focused suites together, not separately.
- [ ] Run full backend regression and runtime-parity/hygiene checks.
- [ ] Record combined verification in the migration manifest.
- [ ] Commit combined accepted fixes as separate logical commits where they do not overlap.

### Task 3: Close Provider-Specific WebSocket Defects Before Import

**Files:**
- Modify: relevant WebSocket provider lifecycle code only after root-cause proof.
- Test: provider-specific tests under `backend/tests/`.
- Modify if needed: backend dependency declaration/lock for the selected CCXT release.

**Interfaces:**
- Consumes: Production evidence for MEXC coroutine warnings and Gate.io orderbook `KeyError` callbacks.
- Produces: causally isolated fixes or explicit `QUARANTINE` disposition for unsupported/unresolved paths.

- [ ] Reproduce MEXC `watch_swap_public was never awaited` on the frozen baseline/provider version.
- [ ] Re-run the same reproduction on the candidate CCXT version and prove whether the warning disappears without WFH source changes.
- [ ] Reproduce Gate.io `KeyError: orderbook:<symbol>` around retirement/unsubscribe timing.
- [ ] Trace ownership of the removed `client.subscriptions[messageHash]` entry before proposing a fix.
- [ ] Add RED tests for every reproduced race.
- [ ] Implement only root-cause fixes that have a single supported hypothesis.
- [ ] Run real-provider churn/retirement probes and assert no unclosed sessions, callback KeyErrors, idle-client retention, FD growth, or overlapping retired/new ownership.
- [ ] Record unresolved provider behavior as fail-closed `UNAVAILABLE` rather than masking errors.

### Task 4: Resolve Runtime Memory, Backlog, and API-Latency Failure

**Files:**
- Modify only after measurement: runtime/scheduler/API files identified by profiling.
- Test: new latency/backlog/resource regression tests.
- Modify: Prometheus alert/rule files if semantic gates are missing.

**Interfaces:**
- Consumes: current Production evidence including RSS, due backlog, evaluation duration histograms, and `/api/candidates` timing.
- Produces: measured service-capacity model and bounded runtime gates.

- [ ] Capture representative API latency, payload size, event-loop delay, queue wait, evaluation service time, GC, RSS, FD, and WebSocket ownership together.
- [ ] Identify whether `/api/candidates` latency is serialization, DB/report aggregation, lock contention, event-loop starvation, or another boundary.
- [ ] Add one RED regression around the proven dominant cause.
- [ ] Implement the minimal fix; do not change strategy thresholds to reduce load.
- [ ] Add semantic readiness metrics for backlog/freshness/API latency if existing health cannot detect the failure.
- [ ] Run a soak longer than the previous failure window and require memory/resource plateau plus bounded backlog.

### Task 5: Import Canonical Source File-by-File

**Files:**
- Create/modify: the canonical `backend/`, `frontend/`, `watchdog/`, `deploy/`, `scripts/`, `docs/`, `.github/`, `.agents/`, and `skills/` trees.
- Test: `scripts/verify_migration_manifest.py`.

**Interfaces:**
- Consumes: reviewed candidate source tree and final manifest dispositions.
- Produces: complete canonical source tree with zero unreviewed source inputs.

- [ ] Classify every source file `KEEP`, `FIX_FIRST`, `SUPERSEDED`, `DROP`, or `QUARANTINE`.
- [ ] Reject caches, runtime DBs, logs, generated outputs, secrets, backup payloads, obsolete snapshots, and duplicate/temporary engineering artifacts.
- [ ] Copy only accepted content into destination paths.
- [ ] Run duplicate-path, credential-pattern, absolute-host-path, executable-permission, generated-artifact, and dead-config scans.
- [ ] Make manifest completeness GREEN with no `UNREVIEWED` entry.

### Task 6: Fold DR Into the Canonical Repository

**Files:**
- Create/modify: `scripts/dr/` or canonical existing backup/restore locations.
- Create/modify: `.github/workflows/restore.yml` only if its responsibilities remain justified.
- Modify: `docs/BACKUP_RESTORE.md` and deployment certification docs.

**Interfaces:**
- Consumes: reviewed `wfh-dr` restore workflow and utility plus existing `wfh` backup/certification tooling.
- Produces: one provenance-aware DR implementation bound to `WFH-ORG` release identity.

- [ ] Compare `wfh-dr` restore behavior with current canonical backup/certification scripts.
- [ ] Remove duplicated restore logic and preserve only one authoritative implementation per operation.
- [ ] Bind backup manifest to exact repository SHA, image revision, DB schema identity, checksum, and failure-domain evidence.
- [ ] Restore to an isolated target and verify integrity/FKs/table counts/provenance.
- [ ] Rehearse rollback without overwriting current Production data.

### Task 7: Rebuild CI and Repository Governance From the Clean Tree

**Files:**
- Modify/create: `.github/workflows/*`, `.github/dependabot.yml`, `CODEOWNERS`, contribution/security docs.

**Interfaces:**
- Consumes: clean canonical tree and runtime-version declarations.
- Produces: deterministic CI from a fresh checkout with no reliance on old repository state.

- [ ] Verify every workflow action is pinned according to current repository policy.
- [ ] Gate dependency/lock parity, backend, frontend, E2E, container, migration, hygiene, credential scan, and manifest completeness.
- [ ] Validate exact revision labels on built images.
- [ ] Confirm no workflow depends on legacy repository names, old branch names, or stale secrets by name.
- [ ] Enable branch/ruleset protections when connector/admin capability allows; otherwise document the exact residual admin action.

### Task 8: Fix Public Routing and Browser/Edge Reliability

**Files:**
- Modify as proven necessary: `deploy/nginx/*`, frontend routing/config, proxy tests.
- Test: browser E2E for `/`, `/dashboard`, SSE reconnect, forwarded-proto/host behavior.

**Interfaces:**
- Consumes: public Cloudflare/Nginx/frontend behavior and the reported redirect-loop incident.
- Produces: deterministic canonical root and dashboard routing without redirect cycles.

- [ ] Define `/` contract explicitly: direct dashboard render or one canonical redirect to `/dashboard`.
- [ ] Reproduce requests with HTTP/HTTPS, forwarded proto, host, cache/cookie variants, and browser navigation.
- [ ] Add RED route/proxy tests for any reproduced loop or incorrect root behavior.
- [ ] Apply the minimal edge/app correction and verify no redirect chain exceeds the defined contract.
- [ ] Run browser E2E through the public edge before release certification.

### Task 9: Release Certification and Production Cutover

**Files:**
- Produce: immutable release-certification evidence under `docs/operations/` or existing canonical evidence path.

**Interfaces:**
- Consumes: exact final `WFH-ORG` SHA and independently certified data backup.
- Produces: deployed Production revision or rollback to the previous known-good release.

- [ ] Run clean-install full verification on exact final SHA.
- [ ] Build exact revision artifacts and validate labels/digests.
- [ ] Create and restore-check independent Production backup.
- [ ] Rehearse migration and rollback with exact artifact.
- [ ] Preserve current deployed revision as rollback target.
- [ ] Cut over only the WaterfallHunter stack.
- [ ] Verify revision identity, safety flags, DB integrity, API/SSE/dashboard/metrics/watchdog.
- [ ] Run sustained post-cutover soak with memory, FD, WebSocket, backlog, freshness, latency, and error-class gates.
- [ ] Declare `PRODUCTION_VERIFIED` only if every gate remains GREEN; otherwise rollback and open a new defect stream.

### Task 10: Retire the Legacy Repositories Safely

**Files:**
- Modify: migration documentation only.

**Interfaces:**
- Consumes: completed rollback observation window.
- Produces: `wfh` and `wfh-dr` retained as read-only historical sources with `WFH-ORG` canonical.

- [ ] Keep both old repositories intact during the rollback window.
- [ ] Verify no Production automation, deployment script, documentation link, or operator workflow still targets the old repos.
- [ ] Archive rather than delete old repositories after the observation window.
- [ ] Record final source-of-truth declaration in `WFH-ORG`.

## Self-review result

The plan covers source provenance, active defect reconciliation, provider-specific races, runtime performance, file-by-file admission, DR consolidation, CI/governance, public routing, Production certification, and legacy retirement. No task permits incidental model-semantic changes or Production mutation before the specified gates.
