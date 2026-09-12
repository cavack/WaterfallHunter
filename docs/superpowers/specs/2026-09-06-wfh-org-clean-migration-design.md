# WFH-ORG Clean Migration Design

## Mission

Build `cavack/WFH-ORG` as the new canonical WaterfallHunter repository from a clean Git history, using `cavack/wfh`, `cavack/wfh-dr`, current Production, open defect branches, and runtime evidence as migration inputs rather than blindly copying any source tree.

The migration is complete only when the new repository is independently reproducible, all accepted defects are closed or explicitly quarantined, DR is certified, Production is cut over to an exact `WFH-ORG` revision, and post-cutover soak proves runtime and semantic health.

## Non-negotiable invariants

- `LIVE_TRADING_ENABLED=false` throughout migration and cutover.
- Automatic Telegram signal delivery remains disabled unless separately authorized.
- Missing market evidence remains explicit `UNAVAILABLE`; no synthetic evidence is introduced.
- `ENTRY_READY` remains the only proactive/actionable decision surface.
- No ScoreV2, lifecycle, Anti-Chase, TP/SL, leverage, ranking, or execution-policy semantics are changed as a side effect of repository cleanup.
- No Production cutover occurs before independent backup/restore certification and rollback rehearsal.
- A green process health endpoint is insufficient; semantic freshness, API latency, backlog, memory, WebSocket ownership, and dashboard behavior are release gates.

## Source inputs and authority

### `cavack/wfh`
Primary source-code and documentation input. Current audited baseline at design time: `3e88df0014c30a8aae656a78cc949689174a631f`.

### `cavack/wfh-dr`
Disaster-recovery input only. Its restore workflow and restore utility are reviewed independently and incorporated only if they remain correct against the new repository/provenance model.

### Production host
Production is runtime evidence, not an authoring source. Any host-only source drift must be explained and either reproduced from Git or rejected before migration.

### Open fixes
Open PRs are evidence branches, not automatically accepted source. Each is independently reviewed, integrated on top of the selected baseline, and re-tested in combination.

## Clean-history policy

`WFH-ORG` uses a new Git history. Old commits, abandoned branches, historical ZIP/snapshot objects, temporary work products, caches, generated artifacts, runtime databases, logs, credentials, backup payloads, and obsolete experiments are not imported.

Provenance is preserved through a machine-readable migration manifest containing:

- source repository;
- source ref/SHA;
- source path;
- source blob SHA where available;
- disposition: `KEEP`, `FIX_FIRST`, `SUPERSEDED`, `DROP`, or `QUARANTINE`;
- destination path;
- rationale;
- verification evidence.

No file may enter the canonical source tree without a disposition record.

Manifest schema v2 also records the final canonical target tree independently from the frozen source inventory. Every final tracked file records its Git path, mode, final blob SHA (except the manifest's unavoidable self-reference), accepted disposition, origin class, rationale, and verification evidence. Strict verification compares that target manifest against `git ls-files -s`, so an unreviewed added file, missing file, mode change, or post-review content change fails closed.

## Repository target structure

The target keeps the current proven high-level product boundaries unless a verified defect requires a narrower change:

- `backend/` — deterministic decision engine, evidence acquisition, APIs, persistence, migrations, tests;
- `frontend/` — read-only Decision Terminal/dashboard and E2E tests;
- `watchdog/` — semantic/process liveness monitoring;
- `deploy/` — Nginx, Prometheus, Grafana, Alertmanager, systemd/runtime configuration;
- `scripts/` — bounded operational, validation, migration, backup, deployment, and scientific tooling;
- `docs/` — canonical architecture, operations, decision semantics, DR, migration records, specs/plans;
- `.github/` — CI, dependency management, repository governance;
- `.agents/` and `skills/` — only validated project-specific agent/skill material.

DR implementation is co-located under the canonical repository rather than maintained as a second competing source repository. Independent backup storage remains outside Git.

## Defect-first migration rule

Known active defects are not copied as accepted implementation. They are reproduced from the clean baseline, given RED regression tests where feasible, fixed at root cause, and only then admitted to the target tree.

Initial mandatory defect streams from the audit are:

1. WebSocket close ownership after timeout / `Unclosed client session` — reconcile PR #128.
2. Dependency declaration/lock drift and native test-harness interpreter mismatch — reconcile PR #127.
3. CCXT/MEXC `watch_swap_public was never awaited` behavior — evaluate the independently observed CCXT upgrade evidence before acceptance.
4. Gate.io orderbook retirement race causing `KeyError: orderbook:<symbol>` — independently reproduce and root-cause before any fix.
5. Runtime memory/backlog/API-latency degradation — current Production evidence includes RSS around 1.59 GiB, due backlog above 140, and `/api/candidates` taking about 20 seconds in a direct backend probe.
6. Public-root/dashboard routing — `/dashboard` currently returns 200 while `/` returns 404; the reported browser redirect-loop must be reproduced or bounded to client/edge state before closure.

No single WebSocket patch may be used to claim closure of unrelated provider races, memory growth, or scheduler capacity without causal evidence.

## File-by-file migration procedure

For every tracked source file in the selected candidate tree:

1. identify owner/responsibility;
2. compare against current baseline, accepted fixes, host runtime declarations, and tests;
3. scan for credentials, generated/runtime data, stale comments, dead configuration, duplicate implementation, absolute host paths, obsolete dependencies, and unsupported workflows;
4. classify the file;
5. if `KEEP`, copy content only after validation;
6. if `FIX_FIRST`, repair through RED -> GREEN before copy;
7. if `SUPERSEDED`, record the replacement;
8. if `DROP`, record why it is unnecessary;
9. if ambiguous, `QUARANTINE` outside the canonical runtime tree until resolved.

A manifest completeness test must prove every source tracked file is accounted for exactly once.

## Dependency and supply-chain contract

- Human-readable dependency declarations and hash locks must be machine-checked for consistency.
- Container builds install from the hash-locked graph.
- Canonical runtime versions are declared once and validated by native and CI workflows.
- `pip-audit`, `npm audit`, CodeQL/static checks, credential-pattern scans, and repository hygiene remain gating where applicable.
- Dependency upgrades that affect WebSocket providers require targeted real-provider regression, not only unit tests.

## Runtime and semantic acceptance gates

Before Production cutover, the exact candidate SHA must pass:

- full backend suite;
- frontend contract tests, typecheck, production build, and browser E2E;
- clean-install validation;
- schema/migration tests from supported historical versions;
- dependency-lock consistency and security audits;
- repository hygiene and credential scan;
- Docker Compose validation and exact revision labels;
- Prometheus rule validation;
- WebSocket ownership/race tests and real-provider probes for affected venues;
- backup, restore, migration rehearsal, and rollback proof;
- API contract tests and bounded `/api/candidates` latency under representative load;
- semantic freshness/backlog/cycle-progress validation;
- dashboard route, SSE/reconnect, proxy headers, and public edge behavior;
- memory/FD/client/subscription soak longer than the previously observed failure window.

## Production cutover strategy

Production is replaced only after the new repository is independently release-certified.

Cutover sequence:

1. create certified independent Production data backup;
2. restore and integrity-check it in an isolated location;
3. rehearse any schema transition and rollback using the exact candidate artifact;
4. build/pull exact `WFH-ORG` release artifacts;
5. preserve current release as rollback target;
6. stop only the WaterfallHunter application stack as required by the deployment procedure;
7. deploy exact candidate revision without changing unrelated host services;
8. verify source/artifact revision identity and safety flags;
9. verify health, API, SSE, dashboard, metrics, DB integrity, and watchdog;
10. run sustained soak; rollback automatically or manually on gate failure.

No unrelated Docker service, SSH configuration, VS Code state, system file, or host project is part of cleanup.

## Completion definition

The migration is not complete when files have been copied. It is complete only when:

- the migration manifest accounts for every source input;
- accepted active defects are closed with reproducible evidence;
- `WFH-ORG` CI is green from a clean checkout;
- DR restore is certified against the exact release;
- Production runs the exact `WFH-ORG` revision;
- post-cutover soak demonstrates bounded memory/resources, acceptable API latency, bounded backlog/freshness, healthy dashboard/edge routing, and no new error-class regression;
- old `wfh` and `wfh-dr` are retained read-only/archived until the rollback observation window is complete, then may be archived permanently rather than deleted.
