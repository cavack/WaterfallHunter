# Wave 1B2 — Runtime Schema Ownership Cutover Design

**Status:** implementation design derived from approved Final Design v6.1
**Branch:** `feat/wave1b2-schema-ownership-cutover-v1`
**Parent:** reviewed Wave 1B1 head `d3dedfa338c5402323ce876cd8321bb5758a5962`
**Model impact:** `SEMANTIC_INFRA`
**Production execution:** NOT AUTHORIZED

## 1. Goal

Move every current runtime-owned SQLite schema mutation into the first-party migration system introduced by Wave 1B1, while preserving all current signal, lifecycle, evidence, replay, execution-observation, and historical-outcome semantics.

After Wave 1B2:

1. runtime stores do not `CREATE TABLE`, `CREATE INDEX`, `CREATE TRIGGER`, or `ALTER TABLE`;
2. runtime stores verify the schema they consume and otherwise fail closed;
3. schema migration is an explicit operational command, never an import-time or startup side effect;
4. a clean database and the current legacy runtime database shape can converge to the same canonical schema;
5. incompatible or partially corrupted legacy schemas fail before any migration write;
6. no Production migration is executed by this workstream.

## 2. Binding safety constraints

- `LIVE_TRADING_ENABLED=false` remains invariant.
- No scoring, threshold, ranking, lifecycle, Entry/TP/SL, leverage, execution-suitability, Telegram, or trading semantic may change.
- No Production backup, DB write, migration execution, readiness write probe, package install, Docker build/restart, service restart, deployment, Telegram send, live trade, or merge to `main` is authorized by B2 development work.
- `MIGRATION_APPROVAL` and `DEPLOYMENT_APPROVAL` remain independent gates.
- Runtime schema verification is read-only. The rollback-write deep-readiness primitive from B1 remains a separate low-frequency operational probe and is not required for ordinary application import.
- Unknown legacy user tables are never dropped or rewritten by B2.
- Existing business/evidence rows are never rewritten by the adoption baseline.

## 3. Fresh source reconciliation

Immediately before the B2 branch was created, canonical `main` still equalled the Design Baseline:

`652f99446ed523c0a602798dde4457bab7983373`

`main` remains protected with required checks:

- `backend`
- `frontend`
- `dependency-audit`
- `container-validation`
- `repository-hygiene`

No upstream source reconciliation was required.

## 4. Corrected schema-owner inventory

Fresh direct-file inspection supersedes broad text-search false positives.

### 4.1 Actual runtime schema owners

| Module | Managed objects | Current mutation |
| --- | --- | --- |
| `core/db.py` | `lbank_catalog`, `catalog_events` | create tables; legacy `ALTER TABLE` evolution for catalog columns |
| `core/lbank_signal_ledger.py` | `lbank_signal_ledger`, index, immutable triggers | create table/index/triggers; legacy `ALTER TABLE` evolution |
| `core/lbank_signal_outcome.py` | `lbank_signal_outcomes`, index, immutable triggers | create table/index/triggers |
| `core/stage_lifecycle.py` | `lbank_stage_lifecycle`, index | create table/index |
| `core/production_evidence.py` | `production_evidence_snapshots`, indexes, immutable triggers | create table/index/triggers; legacy `ALTER TABLE` evolution |
| `core/feature_replay.py` | `production_feature_replay_results_v2`, index, immutable triggers | create table/index/triggers |
| `core/lbank_execution_store.py` | `lbank_execution_observations`, `lbank_execution_observation_history`, indexes | create tables/indexes |
| `core/lbank_execution_decision.py` | `lbank_execution_decision_log`, indexes | create table/indexes |
| `core/historical_outcome_store.py` | two operational historical-outcome tables, index, four immutable triggers | create tables/index/triggers |
| `core/provider_registry.py` | `provider_states` | create table |

### 4.2 Not schema owners in the current branch

- `core/ws_streamer.py` — no SQLite schema mutation in the actual B2 source.
- `core/lbank_execution.py` — read-only exchange observer; no SQLite schema mutation.
- `main.py` — no direct DDL; its B2 concern is construction/startup ordering because DB-backed objects are currently created at module import.
- `scripts/historical_backtest.py` — research/disposable schema remains outside the Production runtime migration contract.

The program inventory document must be corrected to reflect this direct-file result.

## 5. Canonical runtime schema version

Wave 1B1 owns migration version `1` (`db_readiness_probe`).

Wave 1B2 introduces migration version `2`:

`0002_runtime_schema_baseline.sql`

The expected runtime `PRAGMA user_version` after B2 migration is therefore `2`.

The version-2 baseline contains the complete current schema for every B2-managed runtime object. Historical constructor-level `ALTER TABLE` evolution is collapsed into the full current table definitions; B2 does not replay old ad-hoc ALTER history against a current Production database.

## 6. Canonical managed objects

The version-2 schema contract covers these tables:

1. `lbank_catalog`
2. `catalog_events`
3. `lbank_signal_ledger`
4. `lbank_signal_outcomes`
5. `lbank_stage_lifecycle`
6. `production_evidence_snapshots`
7. `production_feature_replay_results_v2`
8. `lbank_execution_observations`
9. `lbank_execution_observation_history`
10. `lbank_execution_decision_log`
11. `operational_historical_outcome_datasets`
12. `operational_historical_signal_outcomes`
13. `provider_states`

Migration-owned infrastructure remains:

14. `schema_migrations`
15. `db_readiness_probe`

The manifest also covers all named indexes, foreign keys, primary-key/unique structure, critical CHECK constraints, and immutable triggers currently defined by the source owners.

### 6.1 Required legacy objects

For adoption of the current pre-migration application database (`user_version=0`, no `schema_migrations`), these objects are required because the current application constructs their owners during ordinary runtime initialization:

- `lbank_catalog`
- `catalog_events`
- `lbank_signal_ledger`
- `lbank_signal_outcomes`
- `lbank_stage_lifecycle`
- `production_evidence_snapshots`
- `production_feature_replay_results_v2`
- `lbank_execution_decision_log`
- `operational_historical_outcome_datasets`
- `operational_historical_signal_outcomes`

The two `lbank_execution_*observation*` tables are optional in a legacy database because their shadow worker is configuration-dependent. `provider_states` is optional because the provider registry is not part of the current `main.py` construction path. Version 2 creates optional missing objects.

A required legacy object that is missing is not silently recreated during adoption; the database is classified incompatible.

## 7. Schema manifest and verification

Create a first-party declarative schema manifest in `core/schema_contract.py`.

The manifest must describe, per table:

- exact expected column names;
- SQLite declared type;
- `NOT NULL` requirement;
- default expression where behavior depends on it;
- primary-key ordinal;
- required UNIQUE keys;
- required named indexes and ordered columns;
- required foreign keys;
- critical CHECK-expression fragments;
- immutable trigger name, event, target, timing, and abort action where applicable.

Verification uses SQLite metadata, not fragile full-string equality:

- `PRAGMA table_info`
- `PRAGMA index_list`
- `PRAGMA index_info`
- `PRAGMA foreign_key_list`
- `sqlite_master` for table SQL and trigger SQL

Whitespace/case are normalized only for targeted CHECK/trigger semantic checks. The verifier rejects:

- missing required columns;
- unknown extra columns on a managed table;
- wrong type/null/default/PK structure;
- wrong/missing required unique/index structure;
- wrong/missing FK targets;
- missing critical CHECKs;
- same-named index/trigger with the wrong definition;
- missing or non-aborting immutability triggers.

Unknown tables outside the B2 managed-name set are reported but never modified. They do not by themselves invalidate an otherwise canonical legacy runtime database.

## 8. Preflight classification

Create `core/migration_preflight.py` with a read-only classifier.

Canonical states:

- `CLEAN_NEW` — DB path does not yet exist and its parent exists.
- `CLEAN_EMPTY` — existing SQLite file contains no application user objects.
- `LEGACY_CANONICAL` — no migration metadata, `user_version=0`, all required legacy objects are present and canonical, any present optional managed objects are canonical.
- `MIGRATED_COMPATIBLE` — canonical `schema_migrations` exists and its applied history is valid against the packaged migration set; pending migrations may remain.
- `PARTIAL_OR_INCOMPATIBLE` — every other state.

Preflight is read-only and opens an existing DB using URI `mode=ro`.

Before any call to `MigrationRunner.apply()`:

1. classify the target;
2. reject `PARTIAL_OR_INCOMPATIBLE`;
3. only then permit migration execution.

This guarantees a malformed legacy runtime schema fails before B1's migration-history bootstrap can write to the database.

## 9. Legacy adoption strategy

The supported B2 legacy upgrade target is the **current canonical pre-migration runtime shape**, not arbitrary historical partially evolved schemas.

For `LEGACY_CANONICAL`:

1. preflight proves every required existing managed object already has the accepted current structure;
2. `MigrationRunner` applies v1 metadata/readiness infrastructure and v2 baseline in version order;
3. v2 uses `CREATE ... IF NOT EXISTS` for canonical current objects;
4. existing canonical required objects are no-ops;
5. allowed missing optional objects are created;
6. no business/evidence rows are copied, updated, deleted, or rebuilt;
7. postflight verifies the complete version-2 manifest and migration history.

Known older/partial schemas with missing required tables, missing evolved required columns, conflicting indexes/triggers, or weakened constraints are intentionally rejected. They require an explicit future compatibility migration, never silent repair during B2 adoption.

This is safer than table rebuilds or treating duplicate-column errors as successful migration state.

## 10. Migration command boundary

Create a dedicated module executable as:

`python -m waterfallhunter.migrate_database`

It must never apply migrations by default.

Required operational interface:

```text
python -m waterfallhunter.migrate_database \
  --db-path <path> \
  --source-revision <revision> \
  --preflight
```

Read-only preflight exits zero only for an allowed state.

Actual mutation requires explicit `--apply`:

```text
python -m waterfallhunter.migrate_database \
  --db-path <path> \
  --source-revision <verified-revision> \
  --apply
```

`--preflight` and `--apply` are mutually exclusive. `--apply` requires a non-empty source revision.

The command prints a bounded JSON result containing classification, applied versions, final `user_version`, and reason codes. It must not print environment secrets or database contents.

Production use of `--apply` is prohibited until a separate `MIGRATION_APPROVAL` after verified backup/restore evidence.

## 11. MigrationRunner read-only verification

Extend B1's `MigrationRunner` with a read-only history verification entrypoint so preflight can validate migrated databases without executing bootstrap DDL.

Conceptual interface:

```python
runner.verify() -> tuple[int, ...]
```

It opens an existing DB read-only, validates migration-history schema/triggers/checksums and `user_version`, and returns the applied versions. It performs no transaction that can mutate state.

`apply()` retains B1's concurrency and checksum guarantees.

## 12. Runtime behavior after cutover

### 12.1 Store constructors

Each actual schema-owning store loses constructor DDL/ALTER behavior.

Default standalone behavior is fail-closed verification:

```python
Store(db_path=..., verify_schema=True)
```

With `verify_schema=True`, the constructor validates only the subset of the canonical manifest it consumes and raises a typed schema error on mismatch. It never repairs schema.

### 12.2 Main application import/startup

`main.py` currently constructs DB-backed objects at module import. To avoid an import-time migration or an import-time failure on a not-yet-prepared DB:

- `main.py` constructs its DB-backed objects with `verify_schema=False`;
- no constructor mutates the DB;
- `startup_event()` performs one read-only **full runtime schema gate** before starting any scanner, replay, Telegram bot, settlement, or execution-shadow background task;
- if the gate fails, startup raises and no background worker starts.

This preserves the current object graph with minimal refactoring and prevents application activity against an unprepared schema.

The runtime startup gate does not call `MigrationRunner.apply()` and does not run the rollback-write deep readiness probe.

## 13. Deep readiness integration

B1's `probe_database()` remains rollback-only and low-frequency. B2 extends its read phase to require the full current runtime schema contract, not only `schema_migrations` and `db_readiness_probe`.

Deep readiness is still not liveness and is not run continuously.

## 14. Store-family cutover order

The implementation is sequential because the canonical manifest and migration baseline are shared state.

### B2A — Schema contract, preflight, baseline migration, explicit migration command

Produces the only authoritative manifest and migration execution boundary.

### B2B — Core signal state stores

Cut over:

- `DBAdapter`
- `StageLifecycleStore`
- `LBankSignalLedger`
- `LBankSignalOutcomeStore`

Preserve catalogue lifecycle, CAS trigger persistence, immutable signal/outcome semantics, and all existing SQL business operations.

### B2C — Evidence and research stores

Cut over:

- `ProductionEvidenceRecorder`
- `FeatureReplayStore`
- `HistoricalOutcomeStore`

Preserve immutable evidence/history, replay hashes/results, and historical-vs-live provenance.

### B2D — Execution/provider residual owners and application gate

Cut over:

- `LBankExecutionStore`
- `LBankExecutionDecisionLogger`
- provider `StorageAdapter`
- `main.py` startup schema gate
- B1 deep readiness full-schema read validation

Finish with a repository-wide runtime DDL guard proving Production runtime source contains no schema mutation outside the migration package.

## 15. TDD acceptance matrix

At minimum B2 must prove:

### Clean install

- nonexistent path + existing parent classifies `CLEAN_NEW` without creating a file during preflight;
- explicit apply creates v1+v2 schema;
- final `user_version=2`;
- all managed tables/indexes/triggers/FKs/checks validate;
- second apply is idempotent and applies no versions.

### Current legacy adoption

- a fixture created from the current pre-migration runtime schema with representative rows classifies `LEGACY_CANONICAL`;
- preflight does not change file bytes/schema/user_version;
- apply preserves row counts and row hashes for business/evidence tables;
- v1/v2 migration history is appended correctly;
- optional absent tables are created;
- immutable triggers/FKs remain valid;
- final full schema verifies.

### Fail-before-write

- missing required legacy table;
- missing required evolved column;
- wrong PK/UNIQUE/FK;
- wrong same-named index;
- wrong same-named/non-aborting trigger;
- nonzero legacy `user_version` without migration history;
- partial `schema_migrations` infrastructure;
- forged migration checksum.

Every incompatible preflight case must leave DB bytes/schema/user_version unchanged.

### Runtime cutover

- every migrated store can read/write its existing business operations with unchanged semantics;
- constructor with missing/incompatible schema raises typed error and creates nothing;
- `verify_schema=False` creates nothing;
- application startup rejects an unprepared DB before scheduling any background task;
- application startup accepts migrated schema;
- no runtime source file outside `waterfallhunter/migrations/` contains executable `CREATE TABLE`, `CREATE INDEX`, `CREATE TRIGGER`, or `ALTER TABLE`.

### Model preservation

- full backend suite;
- Golden Regression canonical fixtures exact;
- no score/lifecycle/ranking/execution semantic diff;
- frontend typecheck/build;
- dependency audit;
- container artifact tests;
- repository hygiene;
- Sonar/CodeRabbit review.

## 16. Test support

Introduce a single test helper for schema-dependent tests rather than duplicating migration setup.

Conceptual helpers:

```python
migrate_test_database(path: Path) -> Path
build_legacy_runtime_database(path: Path, *, optional_tables: bool = True) -> Path
```

`migrate_test_database` invokes the same first-party preflight + migration runner path used by the migration command with source revision `test`.

`build_legacy_runtime_database` exists only under tests and creates the current pre-migration fixture needed to prove adoption; Production code never contains a legacy-schema creator.

## 17. Documentation and operational runbook

B2 updates:

- `docs/program/SCHEMA_OWNERSHIP_INVENTORY.md`
- `README.md` local-development database preparation instructions
- a migration runbook under `docs/` documenting preflight, backup/restore gate, apply, postflight, and rollback decision points
- `docs/program/EXECUTION_LEDGER.md`

The runbook must state that a successful development migration test is not `MIGRATION_APPROVAL` and that a successful migration is not `DEPLOYMENT_APPROVAL`.

## 18. Out of scope

B2 does not implement:

- `signal_metadata` / cohort backfill (W1-C);
- probability cleanup (W1-D);
- Lifecycle V2;
- dashboard/Telegram redesign;
- execution/leverage/portfolio model changes;
- Production backup execution;
- Production migration execution;
- Production deployment;
- merge to `main`.

## 19. Completion gate

Wave 1B2 may be marked `MERGE_READY_PENDING_MERGE_APPROVAL` only after:

1. all four B2 subplans are complete;
2. clean-install and current-legacy adoption tests pass;
3. incompatible legacy cases prove fail-before-write;
4. all runtime schema owners are verify/use only;
5. repository DDL guard passes;
6. full regression + canonical Golden Regression is exact;
7. all five first-party CI jobs are green;
8. Sonar has no unresolved new issue/security hotspot;
9. CodeRabbit has no unresolved substantive finding;
10. controller records final evidence in the execution ledger.

Even then, B2 remains unmerged until separate `MERGE_APPROVAL`.