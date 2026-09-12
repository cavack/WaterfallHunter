# WaterfallHunter Repository & Host Canonicalization Design

**Status:** Approved design baseline; implementation requires the follow-up implementation plan.

**Goal:** Turn `cavack/wfh` and the Ubuntu production host into one clean, professional, self-documenting WaterfallHunter installation with no active legacy project debris, no unused AI runtime, one canonical database lineage, deterministic recovery, and a handoff surface that a new developer or new ChatGPT session can understand without historical chat context.

## 1. Non-negotiable boundaries

- Product boundary remains `SIGNAL_ONLY`; no order placement or cancellation path is authorized.
- `LIVE_TRADING_ENABLED=false` remains fail-closed and mandatory in production.
- The canonical repository remains `cavack/wfh`; do not create a replacement repository.
- `main` is the only production source branch after cutover.
- Preserve useful production market/signal/evidence history. Never discard the production SQLite database merely to simplify cleanup.
- Delete only legacy WaterfallHunter/project artifacts and unused AI runtimes/models. General host tools such as SSH, VS Code Server, Codex/agent tooling, Desktop Commander, Docker, nginx, and normal OS files remain.
- No destructive server cleanup occurs until the exact release SHA, certified database backup, migration rehearsal, clean-install validation, and isolated runtime smoke all pass.
- Git history remains available for forensics; obsolete files disappear from the active tree rather than rewriting repository history.
## 2. Current-state findings that drive this design

The audit found multiple active and historical WaterfallHunter copies on the host: `/srv/waterfallhunter`, `/srv/wfh-worktrees`, `/srv/wfh-loq-dev`, `/srv/wfh-releases`, several rollback/rehearsal directories under `/root`, old repository mirrors/checkouts, and numerous legacy Docker volumes/images. The production database currently lives in a project-SHA-prefixed Docker volume and is approximately 4.5 GiB.

GitHub also contains substantial active historical residue: many stale branches, stacked/draft PRs from previous waves, and temporary/one-shot patch workflows that are still registered as active. Repository metadata still uses deprecated pre-`SIGNAL_ONLY` terminology, while the current product boundary is `SIGNAL_ONLY`.

The host currently relies on Docker `restart: unless-stopped` and `docker.service`; there is no first-party `waterfallhunter.service` systemd unit that asserts the canonical stack after reboot. An exited Ollama container and Ollama volumes remain even though the current canonical AI path is remote advisory and Ollama is not part of the required runtime.

These findings justify a controlled re-baseline rather than incremental housekeeping.

## 3. Canonical repository shape

The active repository root should contain only maintained source, deployment, operations, research, tests, and handoff material:

```text
wfh/
├── .github/
├── backend/
├── frontend/
├── watchdog/
├── deploy/
├── scripts/
├── docs/
├── research/
├── skills/
├── AGENTS.md
├── README.md
├── CONTRIBUTING.md
├── SECURITY.md
├── CHANGELOG.md
├── LICENSE
├── Makefile
├── docker-compose.yml
└── .env.example
```

Local/generated state such as `.venv`, `node_modules`, `.next`, `.pytest_cache`, `__pycache__`, `*.tsbuildinfo`, databases, logs, backups, patch files, one-shot scripts, and temporary generated artifacts must remain ignored and absent from a clean checkout.
## 4. Authoritative documentation surface

The active documentation set must be small enough to navigate but complete enough for a cold handoff. The following files become authoritative:

- `README.md` — product purpose, safety boundary, quick start, architecture summary, status badges, deployment/readiness summary, and links to all canonical docs.
- `AGENTS.md` — coding-agent instructions, invariant list, repository map, test commands, forbidden shortcuts, and source-of-truth hierarchy.
- `docs/ARCHITECTURE.md` — runtime components, networks, persistence, data flow, and deployment topology.
- `docs/MODEL.md` — market universe, exchange roles, evidence families, cascade intelligence, fixed domain rules, and calibration boundaries.
- `docs/DECISION_ENGINE.md` — lifecycle vs entry decision, readiness scoring, hard invalidators, anti-chase, persistence, and notification semantics.
- `docs/DASHBOARD.md` — Decision Terminal semantics and exactly which states are actionable.
- `docs/DATA_AND_DATABASE.md` — SQLite ownership, schema/migrations, retention, evidence lineage, and database growth management.
- `docs/OPERATIONS.md` — service topology, health/readiness, monitoring, systemd, recovery, and routine maintenance.
- `docs/DEPLOYMENT.md` — exact GitHub-to-production release process and rollback boundaries.
- `docs/BACKUP_RESTORE.md` — certified backup, restore rehearsal, checksum, integrity, and disaster recovery.
- `docs/TELEGRAM.md` — canonical ENTRY_READY notification path, delivery gate, retries, probe, and troubleshooting.
- `docs/AI_ADVISORY.md` — advisory-only AI contract, input evidence, timeout/failure semantics, and statement that AI cannot mutate canonical decisions.
- `docs/TROUBLESHOOTING.md` — symptoms, probes, logs, common failure modes, and safe recovery procedures.
- `docs/DEVELOPER_ONBOARDING.md` — local setup, commands, architecture entry points, contribution workflow, and release expectations.
- `docs/PROJECT_HANDOFF.md` — single cold-start briefing for a new developer or a new AI chat.

Historical design/planning documents that are no longer authoritative are removed from the active tree after their final requirements are represented in the canonical docs. Their history remains retrievable through Git.
## 5. GitHub governance and repository presentation

The repository page must describe the current product rather than deprecated pre-`SIGNAL_ONLY` terminology. The final description should state that WaterfallHunter is a `SIGNAL_ONLY` USDT perpetual-futures cascade/short-signal system with evidence, replay, validation, monitoring, and no order execution. Topics should reflect the current stack and domain: perpetual futures, liquidation cascade, trading signals, market data, FastAPI, Next.js, observability, and SQLite.

Repository settings should converge on a simple production-safe policy:

- `main` remains the default protected branch.
- Required checks stay strict and include backend, frontend, dependency audit, container validation, and repository hygiene.
- Force pushes and branch deletion on `main` remain disabled.
- Conversation resolution remains required.
- Linear history remains required.
- Merge commits are disabled because they conflict with the linear-history policy; squash merge is the canonical merge method.
- Head branches are deleted automatically after merge.
- Branch update support may be enabled so PRs can be brought current without ad-hoc patch workflows.
- Automatic merge stays disabled unless separately authorized, because a merge to `main` can trigger production deployment.
- Default GitHub Actions token permissions become read-only; individual workflows explicitly request only the write permissions they need.
- Production deployment is limited to protected `main` and the production environment.
- Wiki and unused GitHub Projects surfaces are disabled if they contain no maintained project material.

Temporary, patch, one-shot, migration-assistant, wave-specific, and syntax-fix workflows are removed from the active branch. The maintained workflow set should be intentionally small: canonical CI, security scanning/dependency automation where supported, and production deployment.

Stale PRs whose content is already merged or superseded are closed with a clear superseded-by note. Corresponding stale branches are deleted only after confirming they contain no unique unmerged production requirement. Dependabot branches remain governed by normal dependency policy rather than being bulk-deleted blindly.
## 6. Canonical production host layout

Production should converge on one WaterfallHunter tree and one canonical runtime identity:

```text
/srv/waterfallhunter/
├── app/        # exact clean checkout of deployed main SHA
├── backups/    # bounded certified application DB backups only
├── runtime/    # deployment certificates, checksums, locks, and non-secret state
└── logs/       # only host-level operational logs that are not already container-managed
```

Secrets move to a host-owned location outside the Git checkout, preferably `/etc/waterfallhunter/waterfallhunter.env`, mode `0600`. The repository carries `.env.example` only. Production compose/deploy tooling must consume the host-owned environment file without copying secret-bearing backups into historical release directories.

The existing forest of old worktrees, release directories, rehearsal directories, local rollback trees, duplicate checkouts, project-specific scratch files, old zip/tar bundles, stale patch files, and duplicate WaterfallHunter roots is eligible for removal only after cutover certification. General user tooling and OS-managed files are not part of cleanup.

The final host must have one canonical Compose project name, one set of canonical service names, no orphan WaterfallHunter containers, and no legacy SHA-prefixed runtime identity.

## 7. Database and persistent data strategy

The current production SQLite database is valuable production evidence and remains the primary data lineage unless validation proves it unusable. Cleanup must not replace it with an empty database.

Required migration sequence:

1. Quiesce only as required for a consistent certified backup.
2. Run SQLite integrity and schema-contract checks on the live source.
3. Produce a certified backup with SHA-256 checksum, file size, source schema version, source release SHA, and timestamp.
4. Rehearse the exact target migrations on a copy.
5. Validate target schema, canonical views, decision-event/outbox tables, and database integrity on the rehearsed copy.
6. Cut over to a canonical persistent volume name, e.g. `waterfallhunter_data`, copying data only if a volume rename/re-home is required.
7. Verify source and destination checksums where byte identity is expected, or verified schema/data invariants where migration changes bytes.
8. Start the target release and verify read/write progress, lifecycle/decision persistence, and no migration loop.
9. Retain a small bounded set of certified backups; delete scattered historical backups only after recovery evidence is complete.

Observability data is not business-critical. Grafana/Prometheus/Alertmanager state may be recreated from repository provisioning unless a specific historical metric set is intentionally retained.
## 8. Runtime supervision, boot recovery, and self-healing

Docker Compose remains the application runtime because the services already use non-root users, read-only filesystems, dropped capabilities, bounded tmpfs, health checks, and restart policies. Systemd becomes the host-level assertion layer rather than a second process supervisor fighting Docker.

Repository-managed unit templates are added under `deploy/systemd/` and installed to `/etc/systemd/system/`:

- `waterfallhunter.service` — `After=docker.service network-online.target`, `Requires=docker.service`, starts the canonical Compose stack with `docker compose up -d --remove-orphans`, remains active after successful assertion, and stops the canonical stack in an orderly manner when explicitly stopped.
- `waterfallhunter-healthcheck.service` — oneshot bounded recovery probe that checks container health and application liveness/readiness without modifying strategy state.
- `waterfallhunter-healthcheck.timer` — periodic invocation with a conservative cadence and startup delay.

Docker `restart: unless-stopped` handles container process exits. The healthcheck timer handles the different case where a container is still running but remains unhealthy. Recovery is bounded: a service must fail multiple consecutive checks before restart, restarts have cooldowns and a capped attempt budget, and persistent failures are surfaced through watchdog/Prometheus rather than causing an infinite restart storm.

External market/API degradation must not trigger blind full-stack restarts. Recovery decisions distinguish process failure, local liveness failure, local readiness failure, and expected upstream-data degradation.

After a host reboot, `docker.service` starts first and `waterfallhunter.service` asserts the canonical stack. A reboot acceptance test must prove the stack returns without an interactive shell or manual command.

## 9. AI runtime policy

The canonical AI layer is advisory-only. AI output may summarize or challenge evidence but cannot create, suppress, upgrade, downgrade, or invalidate the canonical entry decision.

An AI runtime/model remains installed only when all of the following are true:

- it is referenced by the canonical production configuration;
- the application has a maintained health/probe path for it;
- its role is documented in `docs/AI_ADVISORY.md`;
- it is exercised by current tests or runtime validation.

The current stopped Ollama runtime does not meet this bar if the final canonical path uses remote advisory only. In that case the old Ollama container, WaterfallHunter-specific Ollama volumes, model blobs, compose entries, and stale configuration are removed after a host-wide dependency check confirms no other active project uses them. Other unused WaterfallHunter-specific model caches follow the same rule.
## 10. Legacy cleanup policy

Cleanup is allowlist-driven, not `rm -rf` by pattern alone. Before deletion, produce a machine-readable inventory containing path/name, size, type, last modification time, relationship to the canonical release, and disposition (`KEEP`, `MIGRATE`, `DELETE_AFTER_CERTIFICATION`). The final cleanup certificate records what was removed without retaining the removed payloads.

Eligible WaterfallHunter legacy categories include:

- old `/srv/wfh-*` worktrees, release trees, operator scratch, old project copies, and superseded bundles;
- old `/root/wfh-*` and `/root/codex-wfh-*` rollback/rehearsal/checkouts created specifically for WaterfallHunter;
- duplicate WaterfallHunter Git mirrors and quarantined copies after confirming no unique commit is required;
- stale project patch scripts, exit-marker files, temporary Dockerfiles, deployment logs, and one-off cutover artifacts;
- obsolete WaterfallHunter Docker containers, images, networks, and volumes not referenced by the canonical Compose project;
- legacy tiny/duplicate databases after proving they are not the canonical production database or required certified backup;
- unused WaterfallHunter Ollama/model artifacts after the dependency check described above.

Do not remove `/root/.codex`, VS Code Server, SSH material, general agent installations, Docker itself, nginx, normal package-manager data, or unrelated system/user files.

## 11. Canonical operator command surface

A new `Makefile` becomes the human/developer command index. It should wrap existing first-party scripts rather than duplicating logic. Expected targets include:

- `make bootstrap` — local dependency/bootstrap guidance without secrets;
- `make test` — maintained backend and frontend verification;
- `make validate` — contracts, repository hygiene, generated types, and runtime parity;
- `make clean-install` — exact clean-SHA artifact validation;
- `make smoke` — isolated runtime smoke, never Production mutation;
- `make docs-check` — link/source-of-truth and stale terminology validation;
- `make help` — concise explanation of supported commands.

Production deployment remains a guarded GitHub/first-party deployment operation, not an unreviewed `make deploy` shortcut.

## 12. Nginx and public surface

Nginx remains the host edge process. A repository-managed template under `deploy/nginx/` documents the canonical proxy configuration. Only the dashboard frontend is exposed publicly; backend, Prometheus, Grafana, and internal alerting remain private or loopback-scoped according to the existing operational requirement.

The production configuration must not depend on anonymous files created manually in `/etc/nginx/conf.d`. Installation copies or links the versioned template into the host configuration and validates nginx syntax before reload. The public dashboard must expose the deployed revision and signal-only boundary through application/runtime metadata without exposing secrets.
## 13. Repository hygiene and code-quality rules

The cleanup must not become another strategy rewrite. Preserve verified model/decision behavior while removing proven dead, duplicate, generated, stale, or superseded material. Large working modules are not refactored merely for aesthetics; any split must have a concrete ownership/reliability benefit and full regression coverage.

Repository hygiene CI must fail on:

- tracked caches/build artifacts/virtual environments/databases/logs/backups;
- temporary or one-shot workflow filenames;
- conflict markers, patch leftovers, backup suffixes, or known deprecated product terminology in active docs/UI;
- secret-like files or credentials committed to Git;
- stale generated dashboard types;
- unsupported runtime version drift;
- references from canonical docs to removed/superseded files;
- duplicate authoritative documentation claims.

Every maintained script requires `--help` or a clear module docstring, fail-closed defaults for production-sensitive actions, and a test or validation path appropriate to its risk.

## 14. Verification and cutover sequence

The final cutover is accepted only in this order:

1. Resolve and merge the canonical Decision Terminal/model branch with current `main`.
2. Run the full backend suite in the exact production Python image and frontend typecheck/build.
3. Run repository hygiene, dependency/security checks, generated-contract validation, and clean-install validation on the exact candidate SHA.
4. Produce and verify the certified production database backup.
5. Rehearse schema migration on the backup copy.
6. Build and start an isolated canonical stack with no production DB or production Telegram send.
7. Validate liveness, readiness, revision labels, Decision Terminal contract, Telegram read-only `getMe`, and signal-only invariants.
8. Install host configuration/systemd/nginx artifacts in a staged manner and validate syntax.
9. Cut production to the canonical release while preserving the certified database lineage.
10. Verify real production persistence, dashboard, health, watchdog/monitoring, Telegram delivery path, and restart semantics.
11. Exercise a controlled service restart and host boot/recovery acceptance test.
12. Only after successful certification, delete legacy WaterfallHunter host artifacts, orphan Docker resources, and unused AI runtime/models.
13. Run post-cleanup host inventory proving only the canonical WaterfallHunter runtime remains.
14. Push/merge GitHub cleanup, close superseded PRs/issues, delete stale branches, and apply final repository settings.
15. Create a final release/handoff certificate containing repository SHA, database schema, service inventory, systemd state, Docker inventory, GitHub governance summary, and operator commands.

Any failure before step 12 aborts destructive cleanup and leaves certified recovery material intact.
## 15. Definition of Done

The repository/host canonicalization is complete only when all of the following are simultaneously true:

- `main` contains the canonical Decision Terminal/model stack and no unresolved conflict or superseded active implementation path.
- A fresh clone has no tracked cache/build/runtime debris and can be understood from README + `docs/PROJECT_HANDOFF.md` without historical chat context.
- Canonical documentation has one source of truth per subject and no obsolete v5/v6/v7 operational instructions competing with current instructions.
- CI, security/dependency automation, and deployment workflows are maintained and minimal; temporary/one-shot patch workflows are gone.
- GitHub metadata, topics, branch protection, merge policy, branch cleanup, and production deployment policy match this design.
- All stale superseded PRs/branches/issues are closed/deleted/resolved or explicitly retained as current actionable work with a clear reason.
- The production database passes integrity/schema validation, useful history is preserved, and bounded certified recovery backups exist.
- Production runs from one exact clean `main` checkout and one canonical Compose project identity.
- `waterfallhunter.service` is enabled and a reboot/recovery acceptance test proves unattended return to service.
- The bounded health recovery timer is enabled and cannot create an unbounded restart loop.
- Docker shows no orphan WaterfallHunter containers/networks/volumes/images outside the canonical runtime and retained data.
- Unused Ollama/WaterfallHunter AI models and runtime artifacts are absent when not referenced by the final production path.
- Legacy WFH worktrees, releases, rehearsals, rollback trees, duplicate checkouts, patch files, and stale project bundles are absent from `/srv` and `/root`.
- Nginx points only to the canonical frontend and configuration is represented by a versioned repository template.
- Telegram read-only credential probe and canonical notification delivery verification pass without exposing credentials.
- Full backend, frontend, contract, clean-install, migration, isolated smoke, production health, and post-cleanup inventory checks are green.
- A final `PROJECT_HANDOFF.md` and release certificate contain enough information for a competent developer or a new AI session to operate the project safely.

## 16. Explicit non-goals

- Do not add automatic exchange order execution.
- Do not redesign the trading strategy merely to make the repository prettier.
- Do not purge Git history.
- Do not delete general development/agent tooling from the host.
- Do not keep dead infrastructure only because it was expensive to build historically.
- Do not preserve old files in an `archive/` directory merely to avoid making deletion decisions; Git history is the archive.

This design supersedes earlier repository-cleanup or host-layout assumptions wherever they conflict with the canonical rules above.