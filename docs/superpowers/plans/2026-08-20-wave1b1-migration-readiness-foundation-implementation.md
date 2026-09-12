# Wave 1B1 Migration & DB Readiness Foundation — Implementation Plan

> Required workflow: isolated branch, TDD RED→GREEN, focused review, full regression, no Production execution.

**Goal:** Add a first-party, checksum-verified SQLite migration runner and a deep DB readiness primitive using a dedicated rollback-only probe table. Do not cut over existing runtime schema owners yet.

**Spec:** `docs/superpowers/specs/2026-08-20-wave1b1-migration-readiness-foundation-design.md`

**Base:** W1-A head `1b248606c788a9f772677f2a3a7168091d514c95`.

**Model impact:** `SEMANTIC_INFRA`.

## Global hard constraints

- Development/disposable DBs only.
- No Production DB connection or write-probe.
- No existing business-table mutation in B1.
- No runtime constructor cutover in B1.
- No signal metadata/backfill in B1.
- No health-endpoint behavior change in B1.
- No ScoreV2/lifecycle/ranking/execution behavior change.
- No backup, migration, deploy/restart, Telegram send, live trading, or merge to `main`.

---

## Task 1 — Schema-ownership inventory

**Create:** `docs/program/SCHEMA_OWNERSHIP_INVENTORY.md`

- [ ] Record every repository source file currently issuing `CREATE TABLE`, `CREATE INDEX`, `CREATE TRIGGER`, `ALTER TABLE`, or migration-like `PRAGMA table_info` logic.
- [ ] Classify each site as business schema, observational schema, operational schema, or test/script-only.
- [ ] Mark `DBAdapter`, `LBankSignalLedger`, `ProductionEvidenceStore`, and `LBankExecutionStore` as explicit B2 cutover targets where migration-like evolution is already present.
- [ ] State that the inventory is source-derived and must be refreshed before B2.

Acceptance: no runtime schema-mutating source site found by repository search is silently omitted.

---

## Task 2 — Migration discovery and validation

**Create:**
- `backend/src/waterfallhunter/migrations/__init__.py`
- `backend/src/waterfallhunter/migrations/0001_db_readiness_probe.sql`
- `backend/src/waterfallhunter/core/migrations.py`
- `backend/tests/test_migrations.py`

### RED tests first

- [ ] Package discovery returns version 1 / canonical filename.
- [ ] Invalid duplicate/gapped migration lists are rejected.
- [ ] Exact migration bytes produce stable SHA-256.

Run focused test and confirm RED because migration API/module is absent.

### GREEN implementation

Introduce frozen `Migration` value object:

- `version`
- `name`
- `sql_bytes`
- `checksum_sha256`

Production discovery uses `importlib.resources` against `waterfallhunter.migrations`.

Filename regex:

`^(\d{4})_([a-z0-9_]+)\.sql$`

Versions must be contiguous beginning at 1.

Commit only after focused tests pass.

---

## Task 3 — Migration runner and immutable migration history

**Modify:** `core/migrations.py`, `test_migrations.py`

### RED tests first

Use `tmp_path` SQLite DBs.

- [ ] Clean install creates `schema_migrations` and `db_readiness_probe`.
- [ ] Applied row records exact version/name/checksum.
- [ ] `PRAGMA user_version == 1` after apply.
- [ ] Rerun is idempotent.
- [ ] Tampered applied checksum raises typed `MigrationChecksumMismatch`.
- [ ] `schema_migrations` / `user_version` disagreement raises typed `MigrationStateError`.
- [ ] Invalid second migration rolls back its schema/data/version/history atomically while preserving previously committed version 1.
- [ ] Update/delete of `schema_migrations` is rejected by immutability triggers.

Confirm RED before runner exists.

### GREEN implementation

Create typed exceptions:

- `MigrationError`
- `MigrationDiscoveryError`
- `MigrationChecksumMismatch`
- `MigrationStateError`

`MigrationRunner` accepts:

- `db_path`
- optional injected migration sequence for tests
- `busy_timeout_ms`
- optional `source_revision`

Runner bootstrap owns only:

- `schema_migrations`
- its no-update/no-delete triggers

Every migration:

`BEGIN IMMEDIATE` → execute complete statements → insert history → set `PRAGMA user_version` → `COMMIT`; on exception → `ROLLBACK`.

Do not use implicit multi-statement auto-commit behavior.

Use a small deterministic SQL statement splitter based on `sqlite3.complete_statement`, supporting comments/whitespace and rejecting incomplete trailing SQL.

After apply, re-read history and `user_version` and verify exact consistency.

---

## Task 4 — Deep readiness primitive

**Create:**
- `backend/src/waterfallhunter/core/db_readiness.py`
- `backend/tests/test_db_readiness.py`

### RED tests first

After applying B1 migrations to a disposable DB:

- [ ] Healthy probe is ready.
- [ ] Probe insert can be read inside transaction.
- [ ] After rollback, nonce residue count is zero.
- [ ] Missing probe table is NOT_READY.
- [ ] Wrong expected schema version is NOT_READY.
- [ ] Held conflicting SQLite lock returns NOT_READY within bounded time.
- [ ] Integrity/FK checks when requested produce explicit booleans.
- [ ] `require_ready()` raises typed `DatabaseNotReadyError` when result is not ready.

Confirm RED before implementation.

### GREEN implementation

Frozen result model/dataclass:

- ready
- schema_version
- expected_schema_version
- read_ok
- write_rollback_ok
- integrity_ok (`None` when not requested)
- foreign_keys_ok (`None` when not requested)
- residue_count
- reason_codes tuple
- checked_at

Probe sequence matches design exactly and always attempts rollback if a transaction has begun.

Error output must expose only stable reason codes / exception class categories, never SQL text or DB contents.

No endpoint wiring in B1.

---

## Task 5 — Regression and security review

- [ ] Run focused migration/readiness tests.
- [ ] Run complete backend suite.
- [ ] Run runtime parity.
- [ ] Run Wave 0 Golden/model regression.
- [ ] Verify frontend typecheck/build remains green through CI.
- [ ] Verify dependency audit/container validation/repository hygiene.
- [ ] Review migration code specifically for SQL injection/path confusion/implicit commit/checksum bypass/lock wait/rollback residue.
- [ ] Inspect Sonar/CodeRabbit/CodeQL findings and fix/adjudicate before `MERGE_READY`.
- [ ] Update execution ledger with RED/GREEN evidence and exact head SHA.

---

## Task 6 — Stacked draft PR

Open W1-B1 PR with:

- base `feat/wave1a-canonical-contracts-v1`
- head `feat/wave1b-migration-readiness-v1`
- draft = true

Document explicitly:

- B1 is foundation only;
- runtime schema constructors remain unchanged;
- W1-C is blocked on B2 ownership cutover;
- no Production migration/readiness probe was executed.

Do not merge.
