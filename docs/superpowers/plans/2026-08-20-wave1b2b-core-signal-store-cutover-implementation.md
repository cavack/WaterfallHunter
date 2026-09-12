# Wave 1B2B Core Signal Store Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Use TDD for every store; do not batch-edit all stores before verification.

**Goal:** Remove runtime schema mutation from catalog, lifecycle, signal-ledger, and signal-outcome stores and make them consume the B2A canonical schema contract without changing business/model semantics.

**Depends on:** B2A schema contract, v2 baseline migration, migrated test helper, and preflight/migration CLI.

**Model impact:** `SEMANTIC_INFRA`; semantic model changes are forbidden.

## Files

**Modify**

- `backend/src/waterfallhunter/core/db.py`
- `backend/src/waterfallhunter/core/stage_lifecycle.py`
- `backend/src/waterfallhunter/core/lbank_signal_ledger.py`
- `backend/src/waterfallhunter/core/lbank_signal_outcome.py`
- `backend/src/waterfallhunter/main.py`
- `backend/tests/test_lbank_scanner.py`
- `backend/tests/test_db_state_lifecycle_race.py`
- `backend/tests/test_lbank_signal_ledger.py`
- `backend/tests/test_lbank_signal_outcome.py`
- `backend/tests/test_stage_lifecycle.py`
- any existing DBAdapter/execution-report tests that instantiate `DBAdapter` on a fresh temp path

**Use**

- `backend/tests/conftest.py:migrated_db_path`
- `backend/tests/schema_test_support.py:migrate_test_database`
- `backend/src/waterfallhunter/core/schema_contract.py:require_managed_schema`

## Constructor contract

All four stores receive a keyword-only `verify_schema: bool = True` argument.

Default standalone behavior:

```python
Store(db_path=path)  # read-only schema verification, no DDL
```

Application-module construction:

```python
Store(db_path=path, verify_schema=False)
```

`verify_schema=False` means only "defer verification to the application startup full-schema gate"; it never authorizes DDL or repair.

---

### Task 1: Cut over `DBAdapter`

**Files:**
- Modify: `backend/src/waterfallhunter/core/db.py`
- Modify: `backend/tests/test_lbank_scanner.py`
- Modify: `backend/tests/test_db_state_lifecycle_race.py`
- Modify: `backend/tests/test_lbank_signal_ledger.py`
- Modify DBAdapter-dependent tests discovered by full-suite RED.

**Required table subset:** `lbank_catalog`, `catalog_events`.

- [ ] **Step 1: Write RED constructor tests**

Add a focused test module or append to the most direct DBAdapter test:

```python
def test_db_adapter_does_not_create_schema(tmp_path):
    path = tmp_path / "registry.db"
    with pytest.raises(SchemaContractError):
        DBAdapter(str(path))
    assert not path.exists()


def test_db_adapter_verify_false_is_non_mutating(tmp_path):
    path = tmp_path / "registry.db"
    adapter = DBAdapter(str(path), verify_schema=False)
    assert adapter.db_path == str(path)
    assert not path.exists()
```

Also migrate a temp DB and prove normal catalog CRUD behavior remains unchanged.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=backend/src:. pytest -q \
  backend/tests/test_lbank_scanner.py \
  backend/tests/test_db_state_lifecycle_race.py \
  backend/tests/test_lbank_signal_ledger.py
```

Expected: new non-mutation tests fail because constructor still calls `_init_db()`.

- [ ] **Step 3: Replace `_init_db` with verification**

Implement constructor shape:

```python
def __init__(self, db_path="/app/data/waterfall_registry.db", *, verify_schema=True):
    self.db_path = db_path
    if verify_schema:
        require_managed_schema(
            db_path,
            required_tables=frozenset({"lbank_catalog", "catalog_events"}),
            check_user_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        )
```

Delete all `CREATE TABLE`, `PRAGMA table_info`, and `ALTER TABLE` code from `db.py`. Do not alter CRUD SQL or exception behavior in this task.

- [ ] **Step 4: Update fresh-temp tests**

Replace assumptions that constructing `DBAdapter(tmp_path)` bootstraps schema with `migrated_db_path` or `migrate_test_database(path)` before construction.

- [ ] **Step 5: Verify business semantics**

Run scanner/catalog tests and explicitly cover:

- scan-eligible vs catalog membership separation;
- removal after consecutive successful-missing snapshots;
- lifecycle_id increment on re-add/eligibility transition;
- stale state transition rejection.

- [ ] **Step 6: Commit**

```bash
git add backend/src/waterfallhunter/core/db.py backend/tests
git commit -m "refactor: cut catalog schema ownership to migrations"
```

---

### Task 2: Cut over `StageLifecycleStore`

**Files:**
- Modify: `backend/src/waterfallhunter/core/stage_lifecycle.py`
- Modify: `backend/tests/test_stage_lifecycle.py`
- Modify: `backend/src/waterfallhunter/main.py`

**Required table subset:** `lbank_stage_lifecycle` plus `lbank_catalog` because lifecycle operations read catalog identity/eligibility.

- [ ] **Step 1: Write RED non-mutation tests**

Require default construction on missing DB to raise `SchemaContractError` without file creation; require `verify_schema=False` to leave path absent.

- [ ] **Step 2: Replace `_init_db` DDL**

Remove WAL/synchronous/table/index creation from this constructor. Use `require_managed_schema` for the two-table subset when verification is enabled.

Do not modify:

- `VERSION = "stage_lifecycle_v1"`;
- TTL constants;
- stage transition ordering;
- stale/out-of-order semantics;
- UPSERT behavior.

- [ ] **Step 3: Update `main.py` call site**

Change only:

```python
stage_lifecycle_store = StageLifecycleStore(
    db_path=db.db_path,
    verify_schema=False,
)
```

No startup gate is added yet; B2D owns the final full gate.

- [ ] **Step 4: Run focused lifecycle suite**

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests/test_stage_lifecycle.py
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/waterfallhunter/core/stage_lifecycle.py \
  backend/src/waterfallhunter/main.py backend/tests/test_stage_lifecycle.py
git commit -m "refactor: cut lifecycle schema ownership to migrations"
```

---

### Task 3: Cut over immutable signal ledger

**Files:**
- Modify: `backend/src/waterfallhunter/core/lbank_signal_ledger.py`
- Modify: `backend/tests/test_lbank_signal_ledger.py`
- Modify: `backend/src/waterfallhunter/main.py`

**Required table subset:** `lbank_signal_ledger`, `lbank_catalog`.

- [ ] **Step 1: Add RED schema-ownership tests**

Require missing/malformed ledger schema to fail at construction without repair. Add a malformed-trigger test to prove store verification inherits canonical immutable-trigger validation.

- [ ] **Step 2: Remove ledger `_init_db` DDL/ALTER**

Remove:

- table creation;
- `PRAGMA table_info`;
- the three conditional `ALTER TABLE` columns;
- index creation;
- immutable-trigger creation.

Replace with subset verification only.

- [ ] **Step 3: Preserve `persist_trigger` transaction byte-for-byte where possible**

Do not change:

- symbol/expected-state normalization;
- finite-score validation;
- JSON serialization;
- catalog CAS UPDATE;
- `rowcount == 1` stale/ineligible rejection;
- inserted ledger fields;
- transaction boundary coupling catalog transition and immutable ledger append.

Any edit to `persist_trigger` requires explicit justification and an exact regression test; default is no edit.

- [ ] **Step 4: Update `main.py` call site**

```python
signal_ledger = LBankSignalLedger(
    db_path=db.db_path,
    verify_schema=False,
)
```

- [ ] **Step 5: Run ledger/race suites**

```bash
PYTHONPATH=backend/src:. pytest -q \
  backend/tests/test_lbank_signal_ledger.py \
  backend/tests/test_db_state_lifecycle_race.py
```

Require all existing CAS/immutability assertions unchanged.

- [ ] **Step 6: Commit**

```bash
git add backend/src/waterfallhunter/core/lbank_signal_ledger.py \
  backend/src/waterfallhunter/main.py backend/tests/test_lbank_signal_ledger.py \
  backend/tests/test_db_state_lifecycle_race.py
git commit -m "refactor: cut signal ledger schema ownership to migrations"
```

---

### Task 4: Cut over signal outcomes

**Files:**
- Modify: `backend/src/waterfallhunter/core/lbank_signal_outcome.py`
- Modify: `backend/tests/test_lbank_signal_outcome.py`
- Modify: `backend/tests/test_lbank_execution_outcome_report.py`
- Modify: `backend/src/waterfallhunter/main.py`

**Required table subset:** `lbank_signal_outcomes`, `lbank_signal_ledger`.

- [ ] **Step 1: Add RED non-mutation/FK tests**

Require constructor to reject missing/wrong FK schema and never create it. Use migrated fixture for normal behavior.

- [ ] **Step 2: Remove table/index/trigger creation**

Replace `_init_db` with manifest verification. Keep `append_outcome()`'s operational `PRAGMA foreign_keys=ON` because it is connection enforcement, not schema ownership.

- [ ] **Step 3: Update `main.py` call site**

```python
signal_outcome_store = LBankSignalOutcomeStore(
    db_path=db.db_path,
    verify_schema=False,
)
```

- [ ] **Step 4: Re-run natural settlement semantics**

Verify unchanged:

- pending mature signal selection;
- one outcome per signal;
- append-only triggers;
- outcome timing/classification;
- no retroactive trade eligibility.

Run:

```bash
PYTHONPATH=backend/src:. pytest -q \
  backend/tests/test_lbank_signal_outcome.py \
  backend/tests/test_lbank_execution_outcome_report.py
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/waterfallhunter/core/lbank_signal_outcome.py \
  backend/src/waterfallhunter/main.py \
  backend/tests/test_lbank_signal_outcome.py \
  backend/tests/test_lbank_execution_outcome_report.py
git commit -m "refactor: cut signal outcome schema ownership to migrations"
```

---

### Task 5: Core-family regression and DDL audit

- [ ] **Step 1: Search modified runtime files**

Run:

```bash
rg -n "CREATE TABLE|CREATE INDEX|CREATE TRIGGER|ALTER TABLE|PRAGMA table_info" \
  backend/src/waterfallhunter/core/db.py \
  backend/src/waterfallhunter/core/stage_lifecycle.py \
  backend/src/waterfallhunter/core/lbank_signal_ledger.py \
  backend/src/waterfallhunter/core/lbank_signal_outcome.py
```

Expected: no schema-owning DDL/ALTER/table-info migration code. `PRAGMA foreign_keys=ON` in outcome business connections is allowed.

- [ ] **Step 2: Run full backend suite**

```bash
PYTHONPATH=backend/src:. LIVE_TRADING_ENABLED=false pytest -q backend/tests
```

- [ ] **Step 3: Run canonical Golden Regression**

Use the existing canonical fixture replay command from Wave 0/1A. Expected: exact semantic hashes and ordering unchanged.

- [ ] **Step 4: Runtime parity**

```bash
PYTHONPATH=backend/src:. python scripts/verify_runtime_parity.py
```

- [ ] **Step 5: Independent review**

Review specifically for hidden model changes in `persist_trigger`, lifecycle logic, scanner eligibility, or settlement logic. Any substantive finding returns to RED/fix/re-review.

B2B exit state: `CORE_STORES_CUTOVER_VERIFIED`. No Production operation and no merge.