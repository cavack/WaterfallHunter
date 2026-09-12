# WaterfallHunter Wave 1B1 — Migration & DB Readiness Foundation Design

**Status:** Approved implementation subset of Final Design v6.1

**Base:** Wave 1A branch head at `1b248606c788a9f772677f2a3a7168091d514c95`

**Model impact:** `SEMANTIC_INFRA`

**Production impact:** none. This workstream is development/test only. It does not authorize a Production backup, DB write, migration execution, readiness write-probe, service restart, deployment, Telegram delivery, live trading, or merge.

## Why B1 is separate from schema-ownership cutover

Fresh inventory confirms schema mutation is distributed across runtime constructors including `DBAdapter`, `LBankSignalLedger`, `LBankSignalOutcomeStore`, `StageLifecycleStore`, production evidence/replay stores, LBank execution stores, historical outcome stores, provider state, and additional runtime persistence modules. Several constructors create tables/indexes/triggers; `DBAdapter`, `LBankSignalLedger`, `ProductionEvidenceStore`, and `LBankExecutionStore` also contain migration-like schema evolution patterns.

Moving every existing schema definition in the same change that introduces the migration runner would create an unnecessarily large blast radius. Wave 1B is therefore split:

- **B1 (this PR):** first-party migration runner, immutable migration ledger/checksums, `PRAGMA user_version`, dedicated readiness-probe migration, and tested deep-readiness primitive. No active runtime cutover.
- **B2 (follow-up):** migrate legacy constructor-owned schema into versioned migrations and make runtime stores verify/use schema instead of mutating it.

W1-C unified signal metadata must depend on B2, not merely on B1, so new canonical signal schema is not added while runtime schema ownership remains ambiguous.

## Migration source layout

Canonical first-party migrations live inside the Python package:

`backend/src/waterfallhunter/migrations/`

Naming contract:

`NNNN_<name>.sql`

where `NNNN` is a positive, gap-free integer starting at 1.

B1 introduces:

`0001_db_readiness_probe.sql`

It creates only the dedicated readiness table. Existing business tables remain untouched in B1.

## Migration metadata

The migration runner itself owns the bootstrap metadata table:

```sql
schema_migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  checksum_sha256 TEXT NOT NULL,
  applied_at INTEGER NOT NULL,
  source_revision TEXT
)
```

The runner also creates immutable `BEFORE UPDATE` / `BEFORE DELETE` triggers for `schema_migrations`.

Migration SQL checksums use SHA-256 over the exact UTF-8 migration bytes. Once a version is applied, a different checksum for the same version is a hard failure.

`PRAGMA user_version` is the compact database schema-version mirror. After each successful migration it must equal the applied migration version.

## Discovery invariants

The runner fails closed when:

- migration filenames are invalid;
- version numbers are duplicated;
- versions are not contiguous from 1;
- an applied version is missing from the current migration set;
- an applied checksum does not match the current migration bytes;
- `schema_migrations` history and `PRAGMA user_version` disagree;
- a migration statement fails.

No automatic downgrade/reverse migration is provided. Production rollback remains backup/restore-based under its separate approval gate.

## Transaction semantics

Each pending migration is one atomic SQLite transaction:

1. `BEGIN IMMEDIATE`
2. execute every complete SQL statement in the migration file in order
3. insert the immutable `schema_migrations` row
4. set `PRAGMA user_version=<version>`
5. `COMMIT`

On any error:

`ROLLBACK`

and no migration row/version advancement may remain.

The implementation must not use an API pattern that silently commits between migration statements.

## SQLite connection policy for B1

Migration/readiness connections set explicitly:

- `PRAGMA foreign_keys=ON`
- bounded `PRAGMA busy_timeout`

B1 does not globally refactor every legacy connection site; shared connection-factory cutover belongs to B2.

## Readiness probe migration

`0001_db_readiness_probe.sql` creates:

```sql
CREATE TABLE db_readiness_probe (
  probe_id TEXT PRIMARY KEY,
  touched_at INTEGER NOT NULL
);
```

The table stores no business data and is reserved for deep-readiness rollback probes.

## Deep DB readiness contract

Deep readiness is distinct from liveness and from frequent API health checks.

The B1 primitive accepts:

- DB path
- expected schema version
- bounded busy timeout
- whether integrity/FK checks are requested

It verifies, in order:

1. database opens;
2. `PRAGMA user_version` equals expected schema version;
3. required `schema_migrations` and `db_readiness_probe` tables exist;
4. read probe succeeds;
5. when requested, `PRAGMA integrity_check` returns exactly `ok`;
6. when requested, `PRAGMA foreign_key_check` returns zero rows;
7. create random probe nonce;
8. `BEGIN DEFERRED`;
9. insert nonce into `db_readiness_probe`;
10. select/read the same nonce;
11. `ROLLBACK`;
12. verify the nonce has zero residue after rollback.

A lock timeout, schema mismatch, missing table, insert/read failure, integrity failure, FK failure, rollback failure, or residue makes readiness `NOT_READY`.

## Bounded lock behavior

The probe must not wait indefinitely. A test holds a conflicting SQLite lock and verifies the readiness call returns failure within a bounded interval.

This readiness primitive must not be run at high frequency. B1 does not wire it to `/livez` or every `/readyz` request. Operational integration belongs to a later low-frequency/startup/pre/post-migration workstream.

## Result contract

The readiness result is explicit and non-throwing for operational probe failures:

- `ready`
- `schema_version`
- `expected_schema_version`
- `read_ok`
- `write_rollback_ok`
- `integrity_ok` (nullable when not requested)
- `foreign_keys_ok` (nullable when not requested)
- `residue_count`
- `reason_codes`
- `checked_at`

A separate `require_ready()` helper may raise a typed `DatabaseNotReadyError` for fail-closed critical callers.

No SQL text, row contents, credentials, or secret configuration is returned in errors.

## B1 non-goals

B1 does **not**:

- run against Production;
- alter existing business tables;
- migrate signal metadata;
- backfill legacy signals;
- remove constructor schema initialization;
- change current health endpoint behavior;
- make DB readiness a frequent liveness probe;
- modify scoring, lifecycle, ranking, execution, outcomes, dashboard, Telegram, or AI behavior.

## Acceptance gates

B1 passes only when disposable-DB tests prove:

- clean migration install;
- upgrade/idempotent rerun;
- exact checksum recording;
- checksum mismatch fail-closed;
- version/history mismatch fail-closed;
- failed migration atomic rollback;
- readiness read + write/read/ROLLBACK + zero residue;
- schema mismatch fail-closed;
- bounded lock failure;
- integrity/FK behavior;
- full backend regression unchanged;
- Wave 0/1A deterministic/model regression unchanged;
- CI/dependency/container/hygiene/security review green.

B1 may be reviewed while B2 is planned, but W1-C remains blocked until B2 schema-ownership cutover is independently verified.
