# Wave 1B2A Schema Contract, Preflight, and Migration Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the authoritative runtime SQLite schema contract, fail-before-write legacy preflight, version-2 baseline migration, explicit migration CLI, and shared test database support.

**Architecture:** B2A adds no runtime-store cutover yet. It establishes a declarative schema verifier and an explicit migration boundary. Existing databases are classified read-only before `MigrationRunner.apply()` can run; current canonical legacy databases are adopted without rewriting business/evidence rows, while partial/incompatible schemas fail before any migration write.

**Tech Stack:** Python 3.13, stdlib `sqlite3`, dataclasses/enums, existing first-party `MigrationRunner`, pytest, SQLite PRAGMAs.

**Spec:** `docs/superpowers/specs/2026-08-20-wave1b2-schema-ownership-cutover-design.md`

## Global Constraints

- `LIVE_TRADING_ENABLED=false` remains invariant.
- No Production backup, DB write, migration execution, readiness write probe, package install, build/restart/deployment, Telegram send, live trading, or merge to `main`.
- Runtime must never auto-apply migrations.
- `0001_db_readiness_probe.sql` remains immutable.
- New schema version is exactly `2` via `0002_runtime_schema_baseline.sql`.
- Current signal/lifecycle/ranking/execution semantics must not change.
- Legacy adoption may not rewrite existing business/evidence rows.
- Partial/incompatible legacy schemas fail before any write.

---

## File Structure

**Create**

- `backend/src/waterfallhunter/core/schema_contract.py` — declarative manifest + read-only structural verification.
- `backend/src/waterfallhunter/core/migration_preflight.py` — read-only DB classification.
- `backend/src/waterfallhunter/migrate_database.py` — explicit CLI; mutation only with `--apply`.
- `backend/src/waterfallhunter/migrations/0002_runtime_schema_baseline.sql` — complete current runtime schema.
- `backend/tests/schema_test_support.py` — first-party migrated DB helper + frozen legacy-v0 fixture loader/hash helpers.
- `backend/tests/fixtures/legacy_runtime_schema_v0.sql` — current pre-migration runtime schema fixture with no migration metadata.
- `backend/tests/conftest.py` — prepare only the test process's global `REGISTRY_DB_PATH` before collection and provide migrated temp DB fixture.
- `backend/tests/test_schema_contract.py`
- `backend/tests/test_migration_preflight.py`
- `backend/tests/test_migrate_database_cli.py`

**Modify**

- `backend/src/waterfallhunter/core/migrations.py` — add read-only `verify()`.
- `backend/tests/test_migrations.py` — verify-only history coverage.
- `backend/tests/test_migration_hardening.py` — verify malformed-history read-only failure.

---

### Task 1: Add read-only migration-history verification

**Files:**
- Modify: `backend/src/waterfallhunter/core/migrations.py`
- Modify: `backend/tests/test_migrations.py`
- Modify: `backend/tests/test_migration_hardening.py`

**Interfaces:**
- Consumes: existing `MigrationRunner._verify_state(conn) -> set[int]`.
- Produces: `MigrationRunner.verify() -> tuple[int, ...]`.
- Guarantee: opens existing DB in SQLite URI `mode=ro`; never calls `_bootstrap_history`; never creates a DB.

- [ ] **Step 1: Write failing verify-only tests**

Add tests with these exact assertions:

```python
def test_verify_reads_valid_history_without_mutation(tmp_path):
    db_path = tmp_path / "registry.db"
    runner = MigrationRunner(db_path=db_path, source_revision="test")
    assert runner.apply() == (1, 2)
    before = db_path.read_bytes()
    assert runner.verify() == (1, 2)
    assert db_path.read_bytes() == before


def test_verify_does_not_create_missing_database(tmp_path):
    db_path = tmp_path / "missing.db"
    runner = MigrationRunner(db_path=db_path)
    with pytest.raises(MigrationError, match="does not exist"):
        runner.verify()
    assert not db_path.exists()
```

In the hardening test, create malformed `schema_migrations`, call `verify()`, and require `MigrationStateError` while file bytes remain unchanged.

- [ ] **Step 2: Run focused RED**

Run:

```bash
PYTHONPATH=backend/src:. pytest -q \
  backend/tests/test_migrations.py \
  backend/tests/test_migration_hardening.py
```

Expected: only new tests fail because `MigrationRunner.verify` does not exist.

- [ ] **Step 3: Implement read-only `verify()`**

Implement:

```python
def verify(self) -> tuple[int, ...]:
    if not self._db_path.is_file():
        raise MigrationError("database does not exist")
    timeout_seconds = max(self._busy_timeout_ms / 1_000.0, 0.001)
    uri = f"{self._db_path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(
            uri,
            uri=True,
            timeout=timeout_seconds,
            isolation_level=None,
        )
    except sqlite3.Error as exc:
        raise MigrationError("database open failed") from exc
    try:
        conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        return tuple(sorted(self._verify_state(conn)))
    finally:
        conn.close()
```

Do not call `_bootstrap_history` and do not alter `apply()` semantics.

- [ ] **Step 4: Run focused GREEN**

Run the same focused command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/waterfallhunter/core/migrations.py \
  backend/tests/test_migrations.py backend/tests/test_migration_hardening.py
git commit -m "feat: add read-only migration history verification"
```

---

### Task 2: Define the canonical runtime schema manifest

**Files:**
- Create: `backend/src/waterfallhunter/core/schema_contract.py`
- Create: `backend/tests/test_schema_contract.py`

**Interfaces:**

```python
CURRENT_RUNTIME_SCHEMA_VERSION = 2

class SchemaContractError(RuntimeError): ...

@dataclass(frozen=True, slots=True)
class SchemaIssue:
    code: str
    object_name: str
    detail: str

@dataclass(frozen=True, slots=True)
class SchemaVerificationResult:
    valid: bool
    user_version: int | None
    issues: tuple[SchemaIssue, ...]
    unknown_user_objects: tuple[str, ...]

verify_managed_schema_connection(
    conn: sqlite3.Connection,
    *,
    required_tables: frozenset[str] | None = None,
    allow_missing_tables: frozenset[str] = frozenset(),
    check_user_version: int | None = None,
) -> SchemaVerificationResult

verify_managed_schema(
    db_path: str | Path,
    *,
    required_tables: frozenset[str] | None = None,
    allow_missing_tables: frozenset[str] = frozenset(),
    check_user_version: int | None = None,
) -> SchemaVerificationResult

require_managed_schema(...) -> SchemaVerificationResult
```

Define manifest dataclasses internally for columns, indexes, FKs, CHECK fragments, and triggers. Managed table names are exactly the 13 names listed in the B2 design spec.

- [ ] **Step 1: Write RED tests for structural drift**

Tests must cover all verifier dimensions independently:

```python
def test_schema_verifier_rejects_extra_managed_column(...): ...
def test_schema_verifier_rejects_wrong_primary_key(...): ...
def test_schema_verifier_rejects_wrong_named_index_columns(...): ...
def test_schema_verifier_rejects_wrong_foreign_key_target(...): ...
def test_schema_verifier_rejects_missing_critical_check(...): ...
def test_schema_verifier_rejects_non_aborting_immutable_trigger(...): ...
def test_schema_verifier_reports_but_preserves_unknown_table(...): ...
def test_schema_verifier_can_allow_absent_optional_legacy_tables(...): ...
```

Each test creates only the smallest SQLite object needed to isolate its failure code. Stable issue codes:

- `TABLE_MISSING`
- `COLUMN_SET_MISMATCH`
- `COLUMN_CONSTRAINT_MISMATCH`
- `INDEX_MISMATCH`
- `FOREIGN_KEY_MISMATCH`
- `CHECK_MISSING`
- `TRIGGER_MISMATCH`
- `USER_VERSION_MISMATCH`

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests/test_schema_contract.py
```

Expected: import/module missing.

- [ ] **Step 3: Implement manifest primitives and introspection**

Use `PRAGMA table_info`, `index_list`, `index_xinfo`, `foreign_key_list`, and `sqlite_master`. Named indexes must match uniqueness, ordered key columns, direction, collation, and non-partial status. Non-primary UNIQUE constraints must match both their exact ordered columns and SQLite's canonical `ABORT` conflict action; `REPLACE`, `IGNORE`, `FAIL`, `ROLLBACK`, duplicate constraints, and replacement unique indexes are rejected from executable table DDL. Reject explicit managed-column `COLLATE` clauses because the canonical schema uses SQLite's default `BINARY` collation. Tokenize executable SQL separately from comments, string literals, and quoted identifiers. Quoted trigger/table identifiers using SQLite's double-quote, backtick, or bracket forms are accepted only at their canonical positions, while exact decoded identity remains enforced by `sqlite_master.name` and `sqlite_master.tbl_name`. The complete executable CHECK-constraint multiset must exactly match the manifest, including for tables with no canonical CHECKs; missing, weakened, additional, and duplicate CHECKs are rejected. Trigger guards must match the complete canonical trigger DDL with no `WHEN` clause or additional statements, and its executable `RAISE(ABORT, <literal>)` call must use a decoded literal equal to the canonical message:

```python
def _normalized_sql(sql: str | None) -> str:
    # Scanner tracks comments, literals, identifiers, and executable SQL.
    # Structural checks never accept required expressions from quoted text.
    ...
```

Do not compare full table SQL strings. Add regressions proving
`CHECK(status = 'PENDING')` remains distinct from `CHECK(status = 'pending')`
and that CHECK/RAISE text inside comments or string literals cannot satisfy the
schema contract.

- [ ] **Step 4: Encode all 13 current table contracts**

Populate the manifest from the direct-file owner inventory. Required named indexes/triggers are:

```text
idx_lbank_signal_ledger_symbol_triggered
idx_lbank_signal_outcomes_status
idx_lbank_stage_lifecycle_updated
idx_production_evidence_time
idx_production_evidence_symbol
idx_feature_replay_v2_status
idx_lbank_execution_queue
idx_lbank_execution_status
idx_lbank_execution_history_symbol_time
idx_lbank_execution_history_status_time
idx_lbank_execution_history_observed_at
idx_lbank_execution_decision_time
idx_lbank_execution_decision_comparison
idx_operational_historical_symbol
```

Required immutable trigger names:

```text
lbank_signal_ledger_no_update
lbank_signal_ledger_no_delete
lbank_signal_outcomes_no_update
lbank_signal_outcomes_no_delete
production_evidence_no_update
production_evidence_no_delete
production_feature_replay_v2_no_update
production_feature_replay_v2_no_delete
operational_historical_datasets_no_update
operational_historical_datasets_no_delete
operational_historical_outcomes_no_update
operational_historical_outcomes_no_delete
```

Required FKs:

```text
lbank_signal_outcomes.signal_id -> lbank_signal_ledger.id
production_feature_replay_results_v2.snapshot_id -> production_evidence_snapshots.id
operational_historical_signal_outcomes.dataset_id -> operational_historical_outcome_datasets.id
```

Include all current evolved catalog, ledger, and production-evidence columns listed in the design spec/inventory.

- [ ] **Step 5: Run GREEN**

Run `test_schema_contract.py`; expected PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/waterfallhunter/core/schema_contract.py \
  backend/tests/test_schema_contract.py
git commit -m "feat: define canonical runtime schema contract"
```

---

### Task 3: Add the version-2 baseline migration and frozen legacy fixture

**Files:**
- Create: `backend/src/waterfallhunter/migrations/0002_runtime_schema_baseline.sql`
- Create: `backend/tests/fixtures/legacy_runtime_schema_v0.sql`
- Create: `backend/tests/schema_test_support.py`
- Modify: `backend/tests/test_schema_contract.py`
- Modify: `backend/tests/test_migrations.py`

**Interfaces:**

```python
migrate_test_database(path: Path) -> Path
build_legacy_runtime_database(path: Path, *, include_optional: bool = False) -> Path
business_row_hashes(path: Path, tables: tuple[str, ...]) -> dict[str, str]
```

- [ ] **Step 1: Write RED clean-install test expecting migration v2**

```python
def test_packaged_migrations_include_runtime_baseline():
    migrations = discover_migrations()
    assert [item.version for item in migrations] == [1, 2]
    assert migrations[1].filename == "0002_runtime_schema_baseline.sql"
```

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests/test_migrations.py
```

Expected: current package exposes only v1.

- [ ] **Step 3: Write `0002_runtime_schema_baseline.sql`**

Use complete current DDL, not historical `ALTER TABLE` steps. Every managed table uses `CREATE TABLE IF NOT EXISTS`; every named index/trigger uses `IF NOT EXISTS`.

The baseline must include all 13 managed tables and all objects listed in Task 2. In particular, `production_evidence_snapshots` must include these evolved columns directly:

```text
source_ohlcv_captured INTEGER NOT NULL DEFAULT 0 CHECK(... IN (0,1))
source_trades_captured INTEGER NOT NULL DEFAULT 0 CHECK(... IN (0,1))
source_replay_ready_v2 INTEGER NOT NULL DEFAULT 0 CHECK(... IN (0,1))
feature_replay_ready_v3 INTEGER NOT NULL DEFAULT 0 CHECK(... IN (0,1))
triggered_path_replay_ready_v4 INTEGER NOT NULL DEFAULT 0 CHECK(... IN (0,1))
decision_provenance_ready_v5 INTEGER NOT NULL DEFAULT 0 CHECK(... IN (0,1))
raw_derivatives_captured_v5 INTEGER NOT NULL DEFAULT 0 CHECK(... IN (0,1))
production_evidence_complete_v5 INTEGER NOT NULL DEFAULT 0 CHECK(... IN (0,1))
confirmation_ohlcv_captured_v5 INTEGER NOT NULL DEFAULT 0 CHECK(... IN (0,1))
code_sha256_v5 TEXT NOT NULL DEFAULT ''
```

And `lbank_signal_ledger` must directly include `quote_volume_at_trigger`, `volume_gate_passed`, and `proxy_execution_disagreement`.

- [ ] **Step 4: Freeze `legacy_runtime_schema_v0.sql` independently**

Copy the current pre-migration constructor-owned schema as a test-only fixture with `PRAGMA user_version=0` and without `schema_migrations` / `db_readiness_probe`. Include all required legacy tables and omit the three allowed optional tables by default:

```text
lbank_execution_observations
lbank_execution_observation_history
provider_states
```

The helper may append those optional table definitions when `include_optional=True`.

- [ ] **Step 5: Implement test helpers**

The committed `migrate_test_database` always invokes
`run_migration_command(..., apply=True)` so production preflight runs before any
apply. It then requires successful postflight and verified migration history.
A direct `MigrationRunner.apply()` call is allowed only as temporary RED-state
scaffolding before Task 5 and must not be committed. `business_row_hashes`
hashes ordered row JSON from `SELECT * ORDER BY rowid` using SHA-256 and is
test-only.

- [ ] **Step 6: Verify clean schema against manifest**

Add:

```python
def test_runtime_baseline_clean_install_matches_manifest(tmp_path):
    path = tmp_path / "clean.db"
    path = migrate_test_database(path)
    result = verify_managed_schema(path, check_user_version=2)
    assert result.valid, result.issues
```

- [ ] **Step 7: Run focused GREEN**

```bash
PYTHONPATH=backend/src:. pytest -q \
  backend/tests/test_migrations.py \
  backend/tests/test_schema_contract.py
```

- [ ] **Step 8: Commit**

```bash
git add backend/src/waterfallhunter/migrations/0002_runtime_schema_baseline.sql \
  backend/tests/fixtures/legacy_runtime_schema_v0.sql \
  backend/tests/schema_test_support.py \
  backend/tests/test_migrations.py backend/tests/test_schema_contract.py
git commit -m "feat: add canonical runtime schema baseline migration"
```

---

### Task 4: Implement fail-before-write migration preflight

**Files:**
- Create: `backend/src/waterfallhunter/core/migration_preflight.py`
- Create: `backend/tests/test_migration_preflight.py`

**Interfaces:**

```python
class DatabaseClassification(str, Enum):
    CLEAN_NEW = "CLEAN_NEW"
    CLEAN_EMPTY = "CLEAN_EMPTY"
    LEGACY_CANONICAL = "LEGACY_CANONICAL"
    MIGRATED_COMPATIBLE = "MIGRATED_COMPATIBLE"
    PARTIAL_OR_INCOMPATIBLE = "PARTIAL_OR_INCOMPATIBLE"

@dataclass(frozen=True, slots=True)
class MigrationPreflightResult:
    classification: DatabaseClassification
    allowed: bool
    user_version: int | None
    reason_codes: tuple[str, ...]
    unknown_user_objects: tuple[str, ...]

classify_database(
    *,
    db_path: str | Path,
    migrations: Iterable[Migration] | None = None,
) -> MigrationPreflightResult
```

Required legacy table sets come from `schema_contract.py` constants, not duplicated strings.

- [ ] **Step 1: Write classification RED tests**

Test exactly:

```text
missing path + existing parent -> CLEAN_NEW and path remains absent
empty SQLite file -> CLEAN_EMPTY
current frozen legacy fixture -> LEGACY_CANONICAL
legacy fixture with optional tables absent -> LEGACY_CANONICAL
legacy fixture with wrong required PK -> PARTIAL_OR_INCOMPATIBLE
legacy fixture missing required evolved column -> PARTIAL_OR_INCOMPATIBLE
legacy user_version=1 without schema_migrations -> PARTIAL_OR_INCOMPATIBLE
valid v1-only migrated DB -> MIGRATED_COMPATIBLE
valid v2 DB -> MIGRATED_COMPATIBLE
partial schema_migrations -> PARTIAL_OR_INCOMPATIBLE
```

For every incompatible existing-file test, capture `before = path.read_bytes()` and assert `path.read_bytes() == before` after classification.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests/test_migration_preflight.py
```

- [ ] **Step 3: Implement read-only classifier**

Rules:

1. missing path: parent must exist; classify `CLEAN_NEW` without `touch()`;
2. existing DB opens with `mode=ro`;
3. collect user tables/indexes/triggers from `sqlite_master`;
4. no application objects -> `CLEAN_EMPTY`;
5. `schema_migrations` present -> `MigrationRunner.verify()`; success means `MIGRATED_COMPATIBLE`, any migration-state error means incompatible;
6. no migration metadata -> require `user_version == 0`, validate required legacy tables and any present optional tables via schema manifest;
7. unknown non-managed objects are reported but not fatal when all required legacy objects are canonical;
8. any partial migration infrastructure, missing required legacy table, malformed managed object, or unexpected legacy `user_version` -> incompatible;
9. before history bootstrap, every case-insensitive global name owned by migrations (`schema_migrations`, `db_readiness_probe`, and both `schema_migrations_no_*` triggers) must be absent, even when an extension object uses the name or when the database contains no user tables;
10. before the `CLEAN_EMPTY` path, every managed table, index, and trigger name found in `sqlite_master` must have its canonical object type, exact table name, and owner, so a reserved view cannot be adopted or bypass preflight;
11. `MigrationRunner` repeats the reserved migration-infrastructure check under its write lock and either validates existing history without repair or creates the complete history table/trigger set atomically.

- [ ] **Step 4: Run GREEN and byte-preservation tests**

Run test file twice to catch accidental state leakage.

- [ ] **Step 5: Commit**

```bash
git add backend/src/waterfallhunter/core/migration_preflight.py \
  backend/tests/test_migration_preflight.py
git commit -m "feat: add fail-before-write database preflight"
```

---

### Task 5: Add explicit migration CLI and adoption-preservation tests

**Files:**
- Create: `backend/src/waterfallhunter/migrate_database.py`
- Create: `backend/tests/test_migrate_database_cli.py`
- Modify: `backend/tests/schema_test_support.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class MigrationCommandResult:
    classification: str
    applied_versions: tuple[int, ...]
    final_user_version: int | None
    reason_codes: tuple[str, ...]

run_migration_command(
    *,
    db_path: str | Path,
    source_revision: str,
    apply: bool,
) -> MigrationCommandResult

main(argv: Sequence[str] | None = None) -> int
```

CLI requires exactly one of `--preflight` or `--apply`; `--apply` requires non-empty `--source-revision`.

- [ ] **Step 1: Write RED CLI tests**

Cover:

```text
--preflight on CLEAN_NEW does not create DB
--apply on CLEAN_NEW creates v1+v2 and final user_version=2
--apply on LEGACY_CANONICAL preserves existing row hashes/counts
--apply on incompatible legacy returns nonzero and DB bytes unchanged
second --apply on v2 returns applied_versions=[]
missing source revision with --apply is rejected before DB mutation
stdout is bounded JSON and does not contain inserted fixture payload values
```

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests/test_migrate_database_cli.py
```

- [ ] **Step 3: Implement command**

Pseudocode must be followed exactly in ordering:

```python
preflight = classify_database(db_path=path)
if not preflight.allowed:
    return incompatible_result
if not apply:
    return preflight_result
runner = MigrationRunner(
    db_path=path,
    source_revision=source_revision,
)
applied = runner.apply()
postflight = verify_managed_schema(path, check_user_version=2)
if not postflight.valid:
    raise MigrationError("postflight runtime schema verification failed")
runner.verify()
return success_result
```

Never call `apply()` before the preflight allowed check.

- [ ] **Step 4: Finalize `migrate_test_database`**

Use `run_migration_command(..., source_revision="test", apply=True)` so tests exercise the same boundary as operations.

- [ ] **Step 5: Run adoption preservation GREEN**

For the frozen legacy fixture, insert representative rows in catalog, ledger, outcome, evidence, replay, execution decision, lifecycle, and historical tables with FK order respected. Capture table row counts + hashes before apply and require identical values after apply. Only migration metadata/readiness and allowed missing optional tables may be new.

- [ ] **Step 6: Commit**

```bash
git add backend/src/waterfallhunter/migrate_database.py \
  backend/tests/test_migrate_database_cli.py backend/tests/schema_test_support.py
git commit -m "feat: add explicit guarded database migration command"
```

---

### Task 6: Prepare pytest global DB before test-module import

**Files:**
- Create: `backend/tests/conftest.py`
- Modify: `backend/tests/schema_test_support.py`

**Interfaces:**
- Fixture: `migrated_db_path(tmp_path) -> Path`.
- Collection-time global DB: a temporary migrated DB assigned to `REGISTRY_DB_PATH` before modules import `waterfallhunter.main`.

- [ ] **Step 1: Add a test proving `main` can be imported after test pre-bootstrap**

Create a focused assertion in an existing dashboard/main import test or a new `test_test_database_bootstrap.py`:

```python
def test_global_test_registry_database_is_migrated():
    path = Path(os.environ["REGISTRY_DB_PATH"])
    assert verify_managed_schema(path, check_user_version=2).valid
```

- [ ] **Step 2: Implement `conftest.py`**

At conftest import time:

1. create a `tempfile.TemporaryDirectory(prefix="wfh-pytest-")`;
2. set `REGISTRY_DB_PATH` to `<temp>/waterfall_registry.db` before application modules are collected;
3. call `migrate_test_database` once;
4. register cleanup at process exit;
5. expose `migrated_db_path` fixture that creates an isolated v2 DB under each test's `tmp_path`.

Never read `.env` or Production paths.

- [ ] **Step 3: Run backend regression**

```bash
PYTHONPATH=backend/src:. LIVE_TRADING_ENABLED=false pytest -q backend/tests
```

At this B2A point existing store constructors still run `CREATE IF NOT EXISTS`; they should now operate as no-ops on the migrated test DB. No existing semantic test may change expected output.

- [ ] **Step 4: Run runtime parity**

```bash
PYTHONPATH=backend/src:. python scripts/verify_runtime_parity.py
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/conftest.py backend/tests/schema_test_support.py \
  backend/tests/test_test_database_bootstrap.py
git commit -m "test: bootstrap migrated registry database before collection"
```

---

## B2A Review Gate

After Task 6:

1. run full backend suite twice;
2. run canonical Golden Regression exact replay command already used by Wave 0/1A;
3. run runtime parity;
4. inspect `git diff` to confirm no runtime store constructor has yet been cut over;
5. request independent spec review then quality/security review;
6. fix every valid finding through RED → minimal fix → regression;
7. update `docs/program/EXECUTION_LEDGER.md` only after source review stabilizes.

B2A exit state is **FOUNDATION_READY_FOR_B2B**, not `MERGE_READY`. No Production command is executed.
