# Repository and Host Canonicalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert `cavack/wfh` and the Ubuntu production host into one clean, professional, self-documenting WaterfallHunter installation with one canonical `main`, one certified database lineage, deterministic restart/recovery, minimal GitHub automation, and no active legacy WFH or unused AI debris.

**Architecture:** Keep Docker Compose as the application runtime and add systemd only as the host-level assertion/recovery layer. Preserve the current production SQLite lineage through certified backup and migration rehearsal, then cut production to the exact validated release SHA before any destructive cleanup. Repository, GitHub, and host cleanup are allowlist/inventory driven and are certified after removal.

**Tech Stack:** Git/GitHub, GitHub Actions, Ubuntu 24.04, Docker Compose, systemd, nginx, SQLite, FastAPI/Python 3.13, Next.js/Node, Prometheus/Grafana/Alertmanager, Telegram signal delivery.

**Spec:** `docs/superpowers/specs/2026-08-27-repository-host-canonicalization-design.md`

## Global Constraints

- Product boundary remains `SIGNAL_ONLY`; no order placement or cancellation path is authorized.
- `LIVE_TRADING_ENABLED=false` remains fail-closed and mandatory in production.
- `cavack/wfh` remains the only canonical repository and `main` the only production branch.
- Preserve useful production SQLite history; never replace the 4.5 GiB production DB with an empty DB for convenience.
- Delete only legacy WaterfallHunter/project artifacts and unused WaterfallHunter AI runtimes/models; do not delete SSH, VS Code Server, Codex/agent tooling, Desktop Commander, Docker, nginx, or OS-managed files.
- No destructive host cleanup before exact-SHA CI, clean-install validation, certified DB backup, migration rehearsal, isolated runtime smoke, and successful production cutover certification.
- Git history is preserved; cleanup removes obsolete files from the active tree without history rewriting.
- Production secrets live outside the Git checkout and must never be committed or printed.

---

## File Structure Locked by This Plan

**Create:**
- `CHANGELOG.md` — release-oriented change history.
- `Makefile` — stable developer/operator commands.
- `docs/ARCHITECTURE.md`
- `docs/MODEL.md`
- `docs/DECISION_ENGINE.md`
- `docs/DASHBOARD.md`
- `docs/DATA_AND_DATABASE.md`
- `docs/OPERATIONS.md`
- `docs/DEPLOYMENT.md`
- `docs/BACKUP_RESTORE.md`
- `docs/TELEGRAM.md`
- `docs/AI_ADVISORY.md`
- `docs/TROUBLESHOOTING.md`
- `docs/DEVELOPER_ONBOARDING.md`
- `docs/PROJECT_HANDOFF.md`
- `deploy/systemd/waterfallhunter.service`
- `deploy/systemd/waterfallhunter-healthcheck.service`
- `deploy/systemd/waterfallhunter-healthcheck.timer`
- `deploy/nginx/waterfallhunter.conf`
- `scripts/audit_host_inventory.py`
- `scripts/cleanup_legacy_wfh.py`
- `scripts/verify_production_cutover.py`
- `scripts/verify_repository_hygiene.py`
- `backend/tests/test_repository_canonicalization.py`
- `backend/tests/test_host_cleanup_contract.py`
- `backend/tests/test_systemd_artifacts.py`

**Modify:**
- `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `.gitignore`, `.env.example`
- `.github/workflows/ci.yml`, `.github/workflows/deploy-production.yml`
- `.github/CODEOWNERS`, `.github/dependabot.yml`, `.github/pull_request_template.md`
- `docker-compose.yml`, `scripts/deploy_production.sh`, `scripts/validate_clean_install.sh`

**Remove from active tree when superseded:** historical duplicate recorder docs, obsolete superpowers plans/specs that are fully represented in canonical docs, one-shot/temporary patch workflows, tracked generated/cache artifacts if any.

---

### Task 1: Repository Hygiene Contract and Canonical Documentation Surface

**Files:**
- Create: `scripts/verify_repository_hygiene.py`
- Create: `backend/tests/test_repository_canonicalization.py`
- Modify: `.gitignore`
- Create/Modify: root/canonical docs listed above

**Interfaces:**
- Produces `scripts/verify_repository_hygiene.py --root PATH`, exit 0 only when active-tree rules pass.
- Later CI and release certification call this verifier unchanged.

- [ ] **Step 1: Write failing hygiene tests**

```python
def test_canonical_handoff_docs_exist(repo_root: Path) -> None:
    required = {
        "docs/ARCHITECTURE.md", "docs/MODEL.md", "docs/DECISION_ENGINE.md",
        "docs/DASHBOARD.md", "docs/DATA_AND_DATABASE.md", "docs/OPERATIONS.md",
        "docs/DEPLOYMENT.md", "docs/BACKUP_RESTORE.md", "docs/TELEGRAM.md",
        "docs/AI_ADVISORY.md", "docs/TROUBLESHOOTING.md",
        "docs/DEVELOPER_ONBOARDING.md", "docs/PROJECT_HANDOFF.md",
    }
    assert required <= {p.as_posix() for p in repo_root.rglob("*.md")}


def test_active_tree_has_no_generated_or_backup_debris(repo_root: Path) -> None:
    forbidden_parts = {"node_modules", ".next", ".pytest_cache", "__pycache__", ".venv"}
    assert not [p for p in repo_root.rglob("*") if forbidden_parts.intersection(p.parts)]
```

- [ ] **Step 2: Run tests and confirm RED**

Run inside the production dependency image or configured project environment:
`pytest backend/tests/test_repository_canonicalization.py -q`
Expected: missing canonical docs/verifier failures.

- [ ] **Step 3: Implement hygiene verifier and canonical docs**

Verifier rules must reject tracked/generated caches, backup suffixes, conflict markers, active one-shot workflow names, deprecated actionable terminology, missing handoff docs, and references from canonical docs to removed files.

- [ ] **Step 4: Replace README with cold-start entrypoint**

README must contain: purpose, `SIGNAL_ONLY` boundary, 5-minute quick start, runtime topology, canonical decision semantics, links to handoff/architecture/model/operations, test commands, clean-install command, and production safety boundary.

- [ ] **Step 5: Add stable `Makefile` commands**

Required targets: `help`, `setup`, `test`, `typecheck`, `build`, `validate`, `clean-install-check`, `status`, `logs`, `backup-check`, `migration-rehearsal`.
No `make deploy` shortcut that bypasses guarded deployment.

- [ ] **Step 6: Run targeted tests/verifier and commit**

Expected: targeted tests PASS and `python scripts/verify_repository_hygiene.py --root .` PASS.
Commit: `chore(repo): establish canonical handoff and hygiene contract`.

---

### Task 2: GitHub Workflow and Repository Governance Cleanup

**Files:**
- Modify: `.github/workflows/ci.yml`, `.github/workflows/deploy-production.yml`
- Remove: temporary/one-shot/wave patch workflow files from active tree
- Modify: `.github/CODEOWNERS`, `.github/dependabot.yml`, `.github/pull_request_template.md`
- Test: `backend/tests/test_repository_canonicalization.py`

**Interfaces:**
- Maintained workflows are limited to canonical CI, production deploy, security/dependency automation where GitHub-managed.
- Required CI contexts remain `backend`, `frontend`, `dependency-audit`, `container-validation`, `repository-hygiene`.

- [ ] **Step 1: Add failing workflow allowlist tests**

```python
def test_repository_workflow_allowlist(repo_root: Path) -> None:
    workflows = {p.name for p in (repo_root / ".github/workflows").glob("*.yml")}
    assert workflows == {"ci.yml", "deploy-production.yml"}
```

- [ ] **Step 2: Confirm RED with historical workflow debris**
- [ ] **Step 3: Remove active temporary/one-shot workflow files and keep the canonical two first-party workflows**
- [ ] **Step 4: Extend CI `repository-hygiene` to run the new verifier and fail on deprecated workflow/artifact patterns**
- [ ] **Step 5: Run workflow contract tests and YAML parse checks**
- [ ] **Step 6: Commit**

Commit: `chore(github): reduce automation to maintained workflows`.

---

### Task 3: Production Runtime Artifacts, systemd, and nginx Contract

**Files:**
- Create: `deploy/systemd/waterfallhunter.service`
- Create: `deploy/systemd/waterfallhunter-healthcheck.service`
- Create: `deploy/systemd/waterfallhunter-healthcheck.timer`
- Create: `deploy/nginx/waterfallhunter.conf`
- Create: `backend/tests/test_systemd_artifacts.py`
- Modify: `docker-compose.yml`

**Interfaces:**
- `waterfallhunter.service` starts `/usr/bin/docker compose --project-name waterfallhunter --env-file /etc/waterfallhunter/waterfallhunter.env -f /srv/waterfallhunter/app/docker-compose.yml up -d --remove-orphans`.
- Healthcheck service invokes `scripts/verify_production_cutover.py --health-only` and may restart only the canonical Compose service set after bounded consecutive failures/cooldown.

- [ ] **Step 1: Write failing unit-file contract tests**

Tests must assert `After=docker.service network-online.target`, `Requires=docker.service`, `RemainAfterExit=yes`, exact canonical working directory, no secret in unit files, health timer enabled interval >=60s, and no infinite restart loop.

- [ ] **Step 2: Confirm RED**
- [ ] **Step 3: Add systemd unit templates and bounded healthcheck implementation**
- [ ] **Step 4: Add canonical nginx template with only frontend exposed publicly**
- [ ] **Step 5: Validate with `systemd-analyze verify` and `nginx -t -c` against staged paths where supported**
- [ ] **Step 6: Commit**

Commit: `feat(ops): add canonical systemd and nginx runtime contract`.

---

### Task 4: Host Inventory and Safe Cleanup Engine

**Files:**
- Create: `scripts/audit_host_inventory.py`
- Create: `scripts/cleanup_legacy_wfh.py`
- Create: `backend/tests/test_host_cleanup_contract.py`

**Interfaces:**
- `audit_host_inventory.py --output FILE.json` emits entries `{path_or_resource,type,size_bytes,mtime,disposition,reason}`.
- `cleanup_legacy_wfh.py --inventory FILE.json --release-certificate RELEASE.json --db-certificate DB.json --certificate /srv/waterfallhunter/runtime/CLEANUP-OUTPUT.json --execute` deletes only entries explicitly marked `DELETE_AFTER_CERTIFICATION`; the release and database certificates authorize execution, while `--certificate` is an optional cleanup-result output restricted to the canonical runtime directory.

- [ ] **Step 1: Write fail-closed cleanup tests**

```python
def test_cleanup_refuses_unknown_path(tmp_path):
    inventory = {"entries": [{"path_or_resource": "/root/.codex", "disposition": "DELETE_AFTER_CERTIFICATION"}]}
    result = run_cleanup(inventory, execute=True)
    assert result.returncode != 0


def test_cleanup_requires_release_and_db_certificates(tmp_path):
    result = run_cleanup({"entries": []}, execute=True)
    assert "release certificate" in result.stderr.lower()
    assert result.returncode != 0
```

- [ ] **Step 2: Confirm RED**
- [ ] **Step 3: Implement strict allowlist classification for known WFH roots, worktrees, rollback/rehearsal trees, old WFH Docker resources, and WFH-specific unused AI artifacts**
- [ ] **Step 4: Explicitly protect `/root/.codex`, VS Code servers, SSH, general agent directories, Docker/nginx/system files, and unrelated containers/volumes**
- [ ] **Step 5: Add dry-run human-readable and JSON reports**
- [ ] **Step 6: Run synthetic tests and commit**

Commit: `feat(ops): add certified legacy cleanup engine`.

---

### Task 5: Database Certification, Migration Rehearsal, and Bounded Retention

**Files:**
- Modify: `scripts/certify_sqlite_backup.py`, `scripts/rehearse_sqlite_migration.py`, `scripts/deploy_production.sh`
- Create: `docs/DATA_AND_DATABASE.md`, `docs/BACKUP_RESTORE.md`
- Test: existing migration/deployment tests plus new assertions in `backend/tests/test_host_cleanup_contract.py`

**Interfaces:**
- Backup certificate records SHA-256, bytes, schema/user_version, source release SHA, UTC timestamp, SQLite integrity result.
- Cleanup may not remove old DB backups until at least one certified pre-cutover backup and one post-cutover integrity certificate exist.

- [ ] **Step 1: Add failing tests for certificate metadata and retention rules**
- [ ] **Step 2: Confirm RED**
- [ ] **Step 3: Extend backup certificate and migration rehearsal outputs**
- [ ] **Step 4: Add bounded retention policy: keep current canonical DB plus two certified recovery backups unless a smaller set would remove the only pre-migration recovery point**
- [ ] **Step 5: Rehearse migrations against a copy of the current production DB without mutating production**
- [ ] **Step 6: Run integrity/schema/canonical-view checks and commit**

Commit: `feat(data): certify production database lineage and recovery`.

---

### Task 6: Unused AI Runtime Removal Contract

**Files:**
- Modify: `docker-compose.yml`, `.env.example`, `docs/AI_ADVISORY.md`, `docs/ARCHITECTURE.md`
- Test: `backend/tests/test_repository_canonicalization.py`, `backend/tests/test_host_cleanup_contract.py`

**Interfaces:**
- Production runtime declares only AI providers actually referenced by canonical advisory code.
- Ollama resources are eligible for removal only after repository and host dependency scans prove no active WFH path uses them.

- [ ] **Step 1: Add failing test rejecting unused `ollama` production compose service/model references**
- [ ] **Step 2: Search active source/config/host processes for Ollama dependencies and record evidence**
- [ ] **Step 3: Remove stale WFH Ollama compose/config references if scan is clean**
- [ ] **Step 4: Keep AI advisory optional, asynchronous, and non-authoritative**
- [ ] **Step 5: Run focused tests and commit**

Commit: `chore(ai): remove unused WaterfallHunter local model runtime`.

---

### Task 7: Final Repository Regression and Exact-SHA Release Candidate

**Files:** all modified release files

**Interfaces:**
- Produces one clean release-candidate SHA accepted by CI and clean-install validator.

- [ ] **Step 1: Run full backend suite in production Python 3.13 image**
- [ ] **Step 2: Run frontend typecheck and production build**
- [ ] **Step 3: Run repository hygiene verifier, runtime parity, workflow validation, `git diff --check`, credential-pattern scan**
- [ ] **Step 4: Run `scripts/validate_clean_install.sh` on a clean commit SHA**
- [ ] **Step 5: Run isolated fresh-DB runtime smoke and verify `/livez`, `/api/health`, `/api/candidates`, frontend dashboard, OCI revision labels, non-root/read-only runtime**
- [ ] **Step 6: Push exact branch head and update PR #73 verification evidence**

No production mutation in this task.

---

### Task 8: Production Database Backup, Migration Rehearsal, and Cutover

**Files/host:** `/srv/waterfallhunter`, `/etc/waterfallhunter`, Docker project, certified backup path

**Interfaces:**
- Consumes exact validated release SHA from Task 7.
- Produces production release certificate consumed by destructive cleanup.

- [ ] **Step 1: Freeze exact target SHA and verify GitHub required checks are green**
- [ ] **Step 2: Inventory running production revision, Compose project, DB volume, service health, Telegram configuration names, and disk headroom without printing secrets**
- [ ] **Step 3: Create certified DB backup and verify SQLite integrity/checksum**
- [ ] **Step 4: Run target migration rehearsal on backup copy and validate schema 1–target, canonical views, decision events, outbox, and representative row counts**
- [ ] **Step 5: Stage `/etc/waterfallhunter/waterfallhunter.env`, canonical systemd/nginx artifacts, and exact clean `/srv/waterfallhunter/app` checkout**
- [ ] **Step 6: Deploy exact SHA through guarded deployment path using existing certified DB lineage**
- [ ] **Step 7: Verify backend/frontend/watchdog/Prometheus/Grafana/Alertmanager health, dashboard contract, DB progress, decision persistence, Telegram `getMe`, and no live order execution**
- [ ] **Step 8: Enable/reload `waterfallhunter.service` and healthcheck timer, then verify restart assertion without rebooting the host**
- [ ] **Step 9: Produce release certificate containing SHA, schema, DB certificate, containers, systemd units, nginx config hash, health results, and safety invariants**

Any failure stops before cleanup and preserves recovery artifacts.

---

### Task 9: Destructive Legacy WFH Cleanup After Certification

**Files/host:** inventory-driven only

**Interfaces:**
- Requires Task 8 release certificate and DB certificate.
- Produces cleanup certificate and post-cleanup inventory.

- [ ] **Step 1: Generate final pre-delete host/Docker inventory and manually assert protected-path count is unchanged**
- [ ] **Step 2: Mark only proven legacy WFH directories/checkouts/worktrees/releases/rollback trees/patch files and orphan WFH Docker resources `DELETE_AFTER_CERTIFICATION`**
- [ ] **Step 3: Execute cleanup engine and remove unused WFH Ollama/model resources if dependency scan remains clean**
- [ ] **Step 4: Prune only unreferenced WFH Docker images/build cache; never delete canonical persistent DB volume**
- [ ] **Step 5: Verify `/srv` and project-related `/root` state contain only canonical WFH runtime plus general non-WFH tooling**
- [ ] **Step 6: Verify no orphan WFH containers/volumes/networks, and re-run production health checks**
- [ ] **Step 7: Save cleanup certificate under canonical runtime state and commit only non-secret generic cleanup evidence format/docs**

---

### Task 10: GitHub Final Governance and Historical Surface Cleanup

**GitHub:** `cavack/wfh`

**Interfaces:**
- Production `main` points to the certified release or its reviewed cleanup successor.
- Stale branches/PRs are removed only after unique-commit comparison.

- [ ] **Step 1: Merge PR #73 only after exact-head required checks and review threads are green**
- [ ] **Step 2: Verify post-merge `main` CI and production deployment/certificate**
- [ ] **Step 3: Close superseded open PRs with explicit replacement note; preserve only genuinely current dependency/action work**
- [ ] **Step 4: Delete stale merged/superseded branches after `git cherry main branch` confirms no unique required change**
- [ ] **Step 5: Update repository description/topics to current `SIGNAL_ONLY` cascade-short system; remove the deprecated legacy trading-mode topic**
- [ ] **Step 6: Set merge policy to linear/squash-compatible standard, auto-delete merged branches, protected `main`, strict required checks, no force push, required conversation resolution; keep auto-merge disabled because main deploys production**
- [ ] **Step 7: Disable unused wiki/project surfaces if empty, retain Issues for real debt, and resolve/close obsolete cleanup issues**
- [ ] **Step 8: Audit GitHub Actions list and confirm no temporary/one-shot workflow remains active**

---

### Task 11: Final Handoff and Cold-Start Verification

**Files:**
- Finalize: `docs/PROJECT_HANDOFF.md`, `docs/DEVELOPER_ONBOARDING.md`, `CHANGELOG.md`, `README.md`
- Create runtime-only release/cleanup certificate outside Git

**Interfaces:**
- A new developer or new AI session should need only README + PROJECT_HANDOFF to operate safely.

- [ ] **Step 1: Perform cold-start review using only README and PROJECT_HANDOFF, verifying repo purpose, architecture, model, signal semantics, local setup, tests, deploy path, rollback, DB location, systemd, logs, Telegram, AI, and troubleshooting are discoverable**
- [ ] **Step 2: Run final `make validate`, clean-install check, production health/status, systemd status/timer, Docker inventory, DB integrity, nginx check, Telegram read-only probe**
- [ ] **Step 3: Assert host inventory contains no legacy WFH worktree/release/rollback/Ollama resource and no protected general tool was removed**
- [ ] **Step 4: Assert GitHub active workflow/branch/PR surface matches governance design**
- [ ] **Step 5: Add final CHANGELOG release entry and commit documentation-only handoff corrections if necessary**
- [ ] **Step 6: Record final canonical SHA and release certificate location**

---

## Plan Self-Review Checklist

- Spec coverage: repository structure/docs, GitHub governance, host layout, DB preservation, systemd recovery, nginx, AI/Ollama cleanup, Docker cleanup, secrets, CI, handoff, and destructive ordering each map to explicit tasks.
- Destructive ordering: Task 9 cannot run without Task 8 certification; Task 8 cannot run without Task 7 exact-SHA validation.
- Protected host tooling is explicitly excluded from deletion in Tasks 4 and 9.
- Production DB is preserved and certified before cutover; empty-DB replacement is never an allowed shortcut.
- No task authorizes live trading or order execution.
- No placeholder steps are present; every operation has explicit input/output or validation criteria.
