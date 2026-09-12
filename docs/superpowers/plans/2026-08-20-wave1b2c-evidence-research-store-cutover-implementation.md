# Wave 1B2C Evidence and Research Store Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Preserve evidence bytes/hashes and replay semantics; schema-only refactors must not alter payload construction.

**Goal:** Remove constructor-owned SQLite schema mutation from production evidence, feature replay, and historical-outcome stores and switch them to the B2A canonical schema contract.

**Depends on:** B2A + B2B green.

**Model impact:** `SEMANTIC_INFRA`; no evidence-generation, replay, outcome, scoring, or promotion semantic change is permitted.

## Files

**Modify**

- `backend/src/waterfallhunter/core/production_evidence.py`
- `backend/src/waterfallhunter/core/feature_replay.py`
- `backend/src/waterfallhunter/core/historical_outcome_store.py`
- `backend/src/waterfallhunter/main.py`
- `backend/src/waterfallhunter/import_historical_outcomes.py` only if its direct store construction needs explicit migrated-schema error handling/documentation; do not auto-migrate there.
- `backend/tests/test_production_evidence.py`
- `backend/tests/test_routes_production_evidence.py`
- `backend/tests/test_feature_replay.py`
- `backend/tests/test_routes_feature_replay.py`
- `backend/tests/test_historical_outcome_store.py`
- `backend/tests/test_routes_historical_outcomes.py`

**Use**

- `schema_contract.require_managed_schema`
- `schema_contract.CURRENT_RUNTIME_SCHEMA_VERSION`
- `backend/tests/conftest.py:migrated_db_path`

---

### Task 1: Cut over `ProductionEvidenceRecorder`

**Required table subset:** `production_evidence_snapshots`.

- [ ] **Step 1: Add RED no-bootstrap tests**

Add tests:

```python
def test_production_evidence_constructor_never_creates_schema(tmp_path):
    path = tmp_path / "registry.db"
    with pytest.raises(SchemaContractError):
        ProductionEvidenceRecorder(str(path))
    assert not path.exists()


def test_production_evidence_verify_false_is_non_mutating(tmp_path):
    path = tmp_path / "registry.db"
    recorder = ProductionEvidenceRecorder(str(path), verify_schema=False)
    assert recorder.db_path == str(path)
    assert not path.exists()
```

Add a migrated-DB test that deliberately removes one evolved v5 column in a separate malformed fixture and requires typed schema failure without repair.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=backend/src:. pytest -q \
  backend/tests/test_production_evidence.py \
  backend/tests/test_routes_production_evidence.py
```

Expected: new ownership tests fail because `_init_db()` still creates/alters schema.

- [ ] **Step 3: Remove all schema mutation from `_init_db` path**

Constructor becomes:

```python
def __init__(
    self,
    db_path="/app/data/waterfall_registry.db",
    *,
    bucket_seconds=300,
    verify_schema=True,
):
    self.db_path = db_path
    self.bucket_seconds = max(60, int(bucket_seconds))
    self._write_lock = threading.Lock()
    self.total_recorded = 0
    self.total_deduplicated = 0
    self.total_failed = 0
    if verify_schema:
        require_managed_schema(
            db_path,
            required_tables=frozenset({"production_evidence_snapshots"}),
            check_user_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        )
```

Delete from runtime source:

- `PRAGMA journal_mode=WAL`;
- `PRAGMA synchronous=NORMAL`;
- `CREATE TABLE`/indexes/triggers;
- `PRAGMA table_info`;
- all conditional `ALTER TABLE` evolution.

Do **not** change `_safe`, `_payload`, hashing/compression, bucket/dedup logic, capture limitations, counters, or immutable insert behavior.

Journal mode is owned by the explicit B2 migration boundary, not this constructor.

- [ ] **Step 4: Update `main.py`**

```python
production_evidence_recorder = ProductionEvidenceRecorder(
    db_path=db.db_path,
    bucket_seconds=900,
    verify_schema=False,
)
```

- [ ] **Step 5: Prove evidence bytes/hashes unchanged**

On a migrated DB, run existing recorder fixtures and assert the same:

- `evidence_sha256`;
- decompressed canonical payload;
- `schema_version` and `capture_mode`;
- dedup key behavior;
- raw-source readiness flags;
- observational-only/hard-gating fields.

- [ ] **Step 6: Commit**

```bash
git add backend/src/waterfallhunter/core/production_evidence.py \
  backend/src/waterfallhunter/main.py \
  backend/tests/test_production_evidence.py \
  backend/tests/test_routes_production_evidence.py
git commit -m "refactor: cut production evidence schema ownership to migrations"
```

---

### Task 2: Cut over `FeatureReplayStore`

**Required table subset:** `production_feature_replay_results_v2`, `production_evidence_snapshots`.

- [ ] **Step 1: Add RED non-mutation/FK tests**

Require missing replay table, wrong snapshot FK, wrong immutable trigger, and missing index to fail store construction. `verify_schema=False` must create nothing.

- [ ] **Step 2: Remove `FeatureReplayStore._init_db` schema DDL**

Constructor shape:

```python
def __init__(self, db_path="/app/data/waterfall_registry.db", *, verify_schema=True):
    self.db_path = db_path
    if verify_schema:
        require_managed_schema(
            db_path,
            required_tables=frozenset({
                "production_feature_replay_results_v2",
                "production_evidence_snapshots",
            }),
            check_user_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        )
```

Delete only `FeatureReplayStore` schema creation. Do not edit `FeatureReplayEngine` scoring/replay logic.

- [ ] **Step 3: Update `main.py`**

```python
feature_replay_store = FeatureReplayStore(
    db_path=db.db_path,
    verify_schema=False,
)
```

- [ ] **Step 4: Prove deterministic replay unchanged**

Run:

```bash
PYTHONPATH=backend/src:. pytest -q \
  backend/tests/test_feature_replay.py \
  backend/tests/test_routes_feature_replay.py
```

Require existing EQUIVALENT/MISMATCH/NOT_REPLAYABLE behavior, decision-path semantics, code-hash checks, unique `(snapshot_id, replay_version)`, and immutable results unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/src/waterfallhunter/core/feature_replay.py \
  backend/src/waterfallhunter/main.py \
  backend/tests/test_feature_replay.py \
  backend/tests/test_routes_feature_replay.py
git commit -m "refactor: cut feature replay schema ownership to migrations"
```

---

### Task 3: Cut over `HistoricalOutcomeStore`

**Required table subset:**

- `operational_historical_outcome_datasets`
- `operational_historical_signal_outcomes`

- [ ] **Step 1: Add RED immutable-schema tests**

Require constructor to reject:

- missing dataset/outcome table;
- wrong dataset→outcome FK;
- missing UNIQUE report/event identity;
- missing/non-aborting immutable trigger.

No constructor may create an empty schema.

- [ ] **Step 2: Replace `_init_db` with schema verification**

Preserve constructor parameters and cache setup:

```python
def __init__(
    self,
    db_path="/app/data/waterfall_registry.db",
    *,
    cache_ttl_seconds=60.0,
    verify_schema=True,
):
    ...
```

When enabled, verify both historical tables at runtime schema version 2.

Delete table/index/trigger creation only. Do not change:

- `normalize_symbol`;
- report validation;
- report SHA identity;
- import transaction;
- event-key calculation;
- `observational_only` / `hard_gating_allowed` semantics;
- cached report calculations.

- [ ] **Step 3: Update `main.py`**

```python
historical_outcome_store = HistoricalOutcomeStore(
    db_path=db.db_path,
    cache_ttl_seconds=60.0,
    verify_schema=False,
)
```

- [ ] **Step 4: Keep standalone importer explicit**

Inspect `backend/src/waterfallhunter/import_historical_outcomes.py`.

If it constructs `HistoricalOutcomeStore` directly, keep default verification enabled. Do not call migration automatically. A missing/unmigrated DB must fail with a clear schema error instructing operators to run the separate migration command first.

- [ ] **Step 5: Run historical suites**

```bash
PYTHONPATH=backend/src:. pytest -q \
  backend/tests/test_historical_outcome_store.py \
  backend/tests/test_routes_historical_outcomes.py
```

Require idempotent report import, immutable rows, source provenance, and metrics unchanged.

- [ ] **Step 6: Commit**

```bash
git add backend/src/waterfallhunter/core/historical_outcome_store.py \
  backend/src/waterfallhunter/import_historical_outcomes.py \
  backend/src/waterfallhunter/main.py \
  backend/tests/test_historical_outcome_store.py \
  backend/tests/test_routes_historical_outcomes.py
git commit -m "refactor: cut historical evidence schema ownership to migrations"
```

---

### Task 4: Evidence-family mutation audit

- [ ] **Step 1: Prove DDL is gone from these runtime modules**

```bash
rg -n "CREATE TABLE|CREATE INDEX|CREATE TRIGGER|ALTER TABLE|PRAGMA table_info" \
  backend/src/waterfallhunter/core/production_evidence.py \
  backend/src/waterfallhunter/core/feature_replay.py \
  backend/src/waterfallhunter/core/historical_outcome_store.py
```

Expected: no executable schema ownership remains.

- [ ] **Step 2: Full backend regression**

```bash
PYTHONPATH=backend/src:. LIVE_TRADING_ENABLED=false pytest -q backend/tests
```

- [ ] **Step 3: Golden Regression exactness**

Run canonical fixture replay and require exact hashes/ordering. Evidence/refactor changes may not alter any expected semantic packet.

- [ ] **Step 4: Inspect evidence DB rows from tests**

Compare pre-cutover expected fixture payloads to post-cutover rows for:

- production evidence compressed payload/hash;
- feature replay result packet;
- historical outcome import rows.

No field/value difference attributable to schema cutover is accepted.

- [ ] **Step 5: Independent review**

Reviewer scope must explicitly exclude model improvements and focus on accidental payload/replay/provenance changes. Fix valid findings via TDD.

B2C exit state: `EVIDENCE_STORES_CUTOVER_VERIFIED`. No Production operation and no merge.