# Wave 1B2 Runtime Schema Ownership Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all current runtime-owned SQLite schema mutation into the first-party migration system while preserving current WaterfallHunter behavior and keeping Production untouched.

**Architecture:** Build one canonical declarative runtime schema contract and one read-only preflight classifier first, then add migration v2 and an explicit migration CLI. Cut runtime stores over sequentially to verify-only constructors and finish with a startup full-schema gate plus a repository-wide executable-DDL guard. All mutation remains development/test-only unless a separate Production `MIGRATION_APPROVAL` is granted.

**Tech Stack:** Python 3.13, SQLite, pytest, FastAPI startup lifecycle, existing first-party `MigrationRunner`, GitHub Actions, SonarQube Cloud, CodeRabbit.

**Spec:** `docs/superpowers/specs/2026-08-20-wave1b2-schema-ownership-cutover-design.md`

## Global Constraints

- `LIVE_TRADING_ENABLED=false` remains invariant.
- No scoring, threshold, ranking, lifecycle, Entry/TP/SL, leverage, execution-suitability, Telegram, or trading semantic may change.
- No Production backup, DB write, migration execution, readiness write probe, package install, Docker build/restart, service restart, deployment, Telegram send, live trade, or merge to `main` is authorized.
- `MIGRATION_APPROVAL`, `DEPLOYMENT_APPROVAL`, and `MERGE_APPROVAL` remain independent gates.
- Runtime schema verification is read-only and fail-closed.
- Unknown legacy user tables are never dropped or rewritten.
- Existing business/evidence rows are never rewritten by adoption.
- Runtime code outside `waterfallhunter/migrations/` must contain no executable `CREATE TABLE`, `CREATE INDEX`, `CREATE TRIGGER`, or `ALTER TABLE` after B2.
- Model preservation requires exact canonical Golden Regression replay and no expected score/lifecycle/ranking/execution semantic diff.

---

## File Structure

**Create**
- `backend/src/waterfallhunter/core/schema_contract.py` — canonical managed-table/index/FK/check/trigger manifest and read-only verifier.
- `backend/src/waterfallhunter/core/migration_preflight.py` — read-only database classification and fail-before-write gate.
- `backend/src/waterfallhunter/migrations/0002_runtime_schema_baseline.sql` — complete current runtime schema baseline.
- `backend/src/waterfallhunter/migrate_database.py` — explicit `--preflight` / `--apply` operational CLI.
- `backend/tests/schema_test_support.py` — canonical migrated DB and current legacy-runtime fixture helpers.
- `backend/tests/test_schema_contract.py` — manifest/verifier tests.
- `backend/tests/test_migration_preflight.py` — classification and no-write tests.
- `backend/tests/test_runtime_schema_migration.py` — clean install, legacy adoption, row-preservation, idempotency.
- `backend/tests/test_runtime_schema_cutover.py` — verify-only constructors and repository-wide DDL guard.

**Modify**
- `backend/src/waterfallhunter/core/migrations.py` — add read-only `verify()` and preserve existing apply/concurrency/checksum behavior.
- `backend/src/waterfallhunter/core/db_readiness.py` — require the full runtime schema contract in deep-readiness read phase.
- `backend/src/waterfallhunter/core/db.py`
- `backend/src/waterfallhunter/core/lbank_signal_ledger.py`
- `backend/src/waterfallhunter/core/lbank_signal_outcome.py`
- `backend/src/waterfallhunter/core/stage_lifecycle.py`
- `backend/src/waterfallhunter/core/production_evidence.py`
- `backend/src/waterfallhunter/core/feature_replay.py`
- `backend/src/waterfallhunter/core/lbank_execution_store.py`
- `backend/src/waterfallhunter/core/lbank_execution_decision.py`
- `backend/src/waterfallhunter/core/historical_outcome_store.py`
- `backend/src/waterfallhunter/core/provider_registry.py`
- `backend/src/waterfallhunter/main.py`
- existing schema-dependent tests — replace constructor-created schema assumptions with the shared test migration helper.
- `docs/program/SCHEMA_OWNERSHIP_INVENTORY.md` — correct false-positive owners and record final ownership.
- `docs/program/EXECUTION_LEDGER.md` — record RED/GREEN/review evidence and final B2 controller state.

---

### Task 1: Canonical Runtime Schema Contract

**Files:**
- Create: `backend/src/waterfallhunter/core/schema_contract.py`
- Create: `backend/tests/test_schema_contract.py`
- Modify: `docs/program/SCHEMA_OWNERSHIP_INVENTORY.md`

**Interfaces:**
- Consumes: SQLite metadata from `PRAGMA table_info`, `PRAGMA index_list`, `PRAGMA index_info`, `PRAGMA foreign_key_list`, and `sqlite_master`.
- Produces: `SchemaContractError`, `ManagedTableSpec`, `verify_managed_schema_connection(...)`, `verify_managed_schema(...)`, `require_managed_schema(...)`, and `managed_runtime_table_names() -> frozenset[str]`. The manifest itself remains private implementation state.

- [ ] **Step 1: Write failing manifest coverage tests**

Create tests asserting the exact 13 managed runtime table names from the spec and that every immutable source-owned table has its required trigger definitions represented.

- [ ] **Step 2: Write failing verifier tests**

Use in-memory SQLite fixtures to prove rejection of missing/extra columns, wrong declared type/null/default/PK, wrong/missing named index, wrong FK, missing critical CHECK, wrong same-named index/trigger, and non-aborting immutability trigger. Prove unknown tables outside the managed set are tolerated.

- [ ] **Step 3: Run focused tests and record RED**

Run: `pytest -q backend/tests/test_schema_contract.py`
Expected: FAIL because `schema_contract` does not exist.

- [ ] **Step 4: Implement minimal declarative contract and verifier**

Represent columns/indexes/FKs/checks/triggers with frozen dataclasses. Compare metadata structurally; normalize SQL only for targeted CHECK/trigger semantic fragments. Do not compare complete CREATE SQL strings.

- [ ] **Step 5: Run focused tests and full backend regression**

Run: `pytest -q backend/tests/test_schema_contract.py && pytest -q backend/tests`
Expected: PASS; existing behavior unchanged.

- [ ] **Step 6: Commit**

`git commit -m "feat: add canonical runtime schema contract"`

---

### Task 2: Read-Only Migration Verification and Preflight

**Files:**
- Modify: `backend/src/waterfallhunter/core/migrations.py`
- Create: `backend/src/waterfallhunter/core/migration_preflight.py`
- Create: `backend/tests/test_migration_preflight.py`

**Interfaces:**
- Consumes: `MigrationRunner`, packaged migration identities, and `verify_managed_schema_connection(...)`.
- Produces: `MigrationRunner.verify() -> tuple[int, ...]`, `PreflightState` enum (`CLEAN_NEW`, `CLEAN_EMPTY`, `LEGACY_CANONICAL`, `MIGRATED_COMPATIBLE`, `PARTIAL_OR_INCOMPATIBLE`), `PreflightResult`, `classify_database(*, db_path: str | Path) -> PreflightResult`, and `require_migration_compatible(*, db_path: str | Path) -> PreflightResult`.

- [ ] **Step 1: Write failing `MigrationRunner.verify()` tests**

Prove read-only verification accepts valid migration history, rejects malformed history/checksum/user_version, never bootstraps metadata, and cannot create a missing DB.

- [ ] **Step 2: Write failing preflight state tests**

Cover nonexistent path, empty SQLite file, canonical current legacy schema, valid migrated schema with pending versions, and all incompatible cases from the spec.

- [ ] **Step 3: Prove fail-before-write**

For each incompatible existing DB, hash file bytes and snapshot `sqlite_master`/`user_version` before and after classification; assert exact equality.

- [ ] **Step 4: Run focused tests and record RED**

Run: `pytest -q backend/tests/test_migration_preflight.py`
Expected: FAIL on missing interfaces.

- [ ] **Step 5: Implement read-only verification and preflight**

Use URI `mode=ro` for existing files. `CLEAN_NEW` must not create the path. Never call `MigrationRunner.apply()` from preflight.

- [ ] **Step 6: Run focused/full regression and commit**

Run: `pytest -q backend/tests/test_migration_preflight.py backend/tests/test_migrations.py backend/tests/test_migration_review_hardening.py && pytest -q backend/tests`
Commit: `feat: add read-only migration preflight`

---

### Task 3: Runtime Baseline Migration and Explicit Migration CLI

**Files:**
- Create: `backend/src/waterfallhunter/migrations/0002_runtime_schema_baseline.sql`
- Create: `backend/src/waterfallhunter/migrate_database.py`
- Create: `backend/tests/schema_test_support.py`
- Create: `backend/tests/test_runtime_schema_migration.py`

**Interfaces:**
- Consumes: preflight classifier, `MigrationRunner.apply()`, `RUNTIME_SCHEMA` verifier.
- Produces: migration version 2; test helpers `migrate_test_database(path: Path) -> Path` and `build_legacy_runtime_database(path: Path, *, optional_tables: bool = True) -> Path`; CLI `main(argv: Sequence[str] | None = None) -> int`.

- [ ] **Step 1: Build a test-only current legacy fixture**

Copy exact current constructor DDL/constraints into test support only, including representative rows and optional execution/provider tables. Production code must not gain a legacy-schema creator.

- [ ] **Step 2: Write clean-install RED tests**

Assert explicit apply produces v1+v2, `user_version=2`, complete manifest, and second apply returns no new versions.

- [ ] **Step 3: Write legacy-adoption RED tests**

Assert canonical legacy fixture classifies `LEGACY_CANONICAL`, apply preserves row counts and deterministic row hashes, appends migration history, creates allowed missing optional objects, and ends manifest-valid.

- [ ] **Step 4: Write CLI safety RED tests**

Assert no default apply; `--preflight` and `--apply` mutually exclusive; `--apply` requires non-empty source revision; output JSON is bounded and contains no DB row values or environment secrets.

- [ ] **Step 5: Implement v2 SQL and CLI**

Use `CREATE ... IF NOT EXISTS` only after preflight has proven existing managed objects canonical. Do not use destructive rebuilds, data-copy migrations, or duplicate-column-success handling.

- [ ] **Step 6: Run migration-focused/full tests and commit**

Run: `pytest -q backend/tests/test_runtime_schema_migration.py backend/tests/test_migration_preflight.py && pytest -q backend/tests`
Commit: `feat: add runtime schema baseline migration`

---

### Task 4: Core Signal-State Store Cutover

**Files:**
- Modify: `backend/src/waterfallhunter/core/db.py`
- Modify: `backend/src/waterfallhunter/core/stage_lifecycle.py`
- Modify: `backend/src/waterfallhunter/core/lbank_signal_ledger.py`
- Modify: `backend/src/waterfallhunter/core/lbank_signal_outcome.py`
- Modify: affected tests

**Interfaces:**
- Consumes: `require_managed_schema(..., required_tables=...)`, migrated test DB helper.
- Produces: constructors accepting `verify_schema: bool = True`; zero DDL/ALTER behavior.

- [ ] **Step 1: Convert tests to migrated fixture setup and add constructor no-mutation RED tests**

Assert missing schema raises typed schema error and does not create files/tables; `verify_schema=False` performs no schema mutation.

- [ ] **Step 2: Preserve business-operation semantics**

Keep existing catalog updates, stage lifecycle persistence, ledger CAS/append behavior, indexes, and ledger/outcome immutability behavior unchanged.

- [ ] **Step 3: Remove constructor DDL/ALTER and add subset verification**

Each store verifies only its managed table family when enabled.

- [ ] **Step 4: Run focused and full regression**

Run all tests for these four modules, then `pytest -q backend/tests`.

- [ ] **Step 5: Commit**

`git commit -m "refactor: cut core stores over to migration-owned schema"`

---

### Task 5: Evidence and Research Store Cutover

**Files:**
- Modify: `backend/src/waterfallhunter/core/production_evidence.py`
- Modify: `backend/src/waterfallhunter/core/feature_replay.py`
- Modify: `backend/src/waterfallhunter/core/historical_outcome_store.py`
- Modify: affected tests

**Interfaces:**
- Consumes: canonical schema verifier and migrated test helper.
- Produces: verify-only constructors; no DDL/ALTER.

- [ ] **Step 1: Add no-mutation/fail-closed RED tests**
- [ ] **Step 2: Remove constructor schema mutation and retain all evidence/history/replay operations**
- [ ] **Step 3: Verify immutable trigger behavior and replay determinism**
- [ ] **Step 4: Run focused/full regression**
- [ ] **Step 5: Commit**

`git commit -m "refactor: cut evidence stores over to migration-owned schema"`

---

### Task 6: Execution/Provider Residual Owners

**Files:**
- Modify: `backend/src/waterfallhunter/core/lbank_execution_store.py`
- Modify: `backend/src/waterfallhunter/core/lbank_execution_decision.py`
- Modify: `backend/src/waterfallhunter/core/provider_registry.py`
- Modify: affected tests

**Interfaces:**
- Consumes: schema verifier and migrated test helper.
- Produces: verify-only constructors; SIGNAL_ONLY/read-only execution semantics unchanged.

- [ ] **Step 1: Add constructor no-mutation/fail-closed RED tests**
- [ ] **Step 2: Remove DDL/ALTER and add subset verification**
- [ ] **Step 3: Prove execution observation/decision/provider semantics unchanged**
- [ ] **Step 4: Run focused/full regression and commit**

`git commit -m "refactor: cut residual stores over to migration-owned schema"`

---

### Task 7: Application Startup Gate and Deep Readiness Integration

**Files:**
- Modify: `backend/src/waterfallhunter/main.py`
- Modify: `backend/src/waterfallhunter/core/db_readiness.py`
- Create/modify: startup/readiness tests

**Interfaces:**
- Consumes: `verify_managed_schema(...)`, constructors with `verify_schema=False`.
- Produces: one startup full-schema read-only gate before background task scheduling; deep readiness full-schema read validation.

- [ ] **Step 1: Write RED tests for unprepared startup**

Assert module object construction does not mutate/create schema and startup failure occurs before scanner/replay/Telegram/settlement/execution-shadow tasks are scheduled.

- [ ] **Step 2: Write migrated-startup acceptance tests**

Assert migrated schema passes startup gate without invoking migrations or rollback-write readiness.

- [ ] **Step 3: Wire `verify_schema=False` at import construction and full gate at startup**

Do not call `MigrationRunner.apply()` from `main.py`.

- [ ] **Step 4: Extend deep readiness read phase**

Require full runtime schema contract while preserving low-frequency rollback-only write probe semantics.

- [ ] **Step 5: Run focused/full regression and commit**

`git commit -m "refactor: gate runtime startup on migrated schema"`

---

### Task 8: Runtime DDL Guard and Model-Preservation Verification

**Files:**
- Create: `backend/tests/test_runtime_schema_cutover.py`
- Modify: `docs/program/SCHEMA_OWNERSHIP_INVENTORY.md`
- Modify: `docs/program/EXECUTION_LEDGER.md`

**Interfaces:**
- Consumes: complete B2 branch.
- Produces: executable-DDL guard, final B2 evidence ledger, corrected ownership inventory.

- [ ] **Step 1: Add AST/text guard**

Scan runtime Python source outside `waterfallhunter/migrations/` and fail on executable schema mutation tokens: `CREATE TABLE`, `CREATE INDEX`, `CREATE TRIGGER`, `ALTER TABLE`. Exclude tests, docs, comments, and research-only `scripts/historical_backtest.py` from the runtime ownership assertion.

- [ ] **Step 2: Run backend + canonical Golden Regression**

Require full suite and exact canonical fixture hashes/semantic outputs; any unexpected model-semantic diff blocks B2.

- [ ] **Step 3: Run frontend and artifact gates**

Require frontend typecheck/build, dependency audit, runtime parity, container artifact family tests, OCI revision labels, and repository hygiene.

- [ ] **Step 4: Static/review gates**

Inspect all Sonar new issues/security hotspots. Run CodeRabbit; regression-test and fix every valid functional/integrity/security finding. Do not accept a green quality gate as sufficient if actionable findings remain.

- [ ] **Step 5: Record evidence**

Update inventory and ledger with exact RED/GREEN counts, commit SHAs, CI run IDs, Sonar/CodeRabbit disposition, model-diff result, and explicit Production non-mutation statement.

- [ ] **Step 6: Open Draft stacked PR**

Base: `feat/wave1b-migration-readiness-v1`.
Head: `feat/wave1b2-schema-ownership-cutover-v1`.
Controller state may become only `MERGE_READY_PENDING_MERGE_APPROVAL`; never merge without explicit approval.
