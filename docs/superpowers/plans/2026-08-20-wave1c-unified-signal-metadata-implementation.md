# Wave 1C — Unified Signal Metadata and Cohort Purity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make signal lineage first-class and fail-closed so STRICT and EXPERIMENTAL signals cannot silently mix, while preserving all ScoreV2, lifecycle, ranking, execution, and signal-only behavior.

**Architecture:** Introduce schema version 3 with immutable `signal_metadata` and an explicit `canonical_signal_view`, then require future signal persistence to atomically commit catalogue CAS + ledger + metadata. Legacy classification is deterministic and append-only; incomplete/conflicting rows remain unresolved and non-canonical. Consumers move to canonical reads with explicit cohort policy, and startup adds a read-only metadata-completeness gate before workers start.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLite/WAL, first-party `MigrationRunner`, RFC8785/JCS via `waterfallhunter.core.canonical_json`, pytest, GitHub Actions, Docker/Compose, SonarQube Cloud, CodeRabbit.

**Spec:** `docs/superpowers/specs/2026-08-20-wave1c-unified-signal-metadata-design.md`

## Global Constraints

- Canonical repository: `cavack/wfh`.
- Clean publication base is merged Wave 1B2 `main` at `e5a133718959187b694ef79aa550801228188231`; its tree is identical to the authorized PR #25 source head `6dc20b7edf3a880e2277f7b0dd0d429afb240ca6`.
- The clean design/plan branch is `docs/wave1c-unified-signal-metadata-design-v2`. Re-check `main` before each new implementation branch and rebuild every implementation slice from the preceding merged clean slice; do not reuse the superseded stacked branches.
- `LIVE_TRADING_ENABLED=false` remains invariant.
- Signal classes are exactly `STRICT` and `EXPERIMENTAL`; `UNRESOLVED` is not a third signal class.
- No default-to-STRICT and no canonical cohort fallback to `trigger_metrics_json`.
- No ScoreV2 weight/gate/version change, lifecycle threshold/transition change, ranking change, Entry/TP/SL/leverage change, or Telegram delivery-policy change.
- Runtime schema version target is exactly `3`.
- Runtime constructors remain verify-only; runtime startup does not auto-migrate or repair.
- Every writable connection to the managed SQLite database enables and verifies `PRAGMA foreign_keys=ON` before beginning a transaction; no production writer may rely on SQLite's disabled-by-default setting.
- Future signal persistence must atomically commit catalogue CAS + ledger row + metadata row or roll back all three.
- `canonical_signal_view` must use explicit fields and `INNER JOIN`.
- Legacy ledger rows are immutable and are never rewritten.
- Legacy rows with incomplete/conflicting evidence receive no `signal_metadata` row and remain non-canonical.
- Canonical hashing uses RFC8785/JCS bytes plus SHA-256; no `json.dumps(sort_keys=True)` substitute for provenance hashes.
- Production-facing report/calibration default cohort is STRICT; EXPERIMENTAL is opt-in research.
- No Production backup, Production DB write, Production migration/classification, deployment/restart/build, server package install, Telegram send, live trading, or merge to `main`.
- `DESIGN_APPROVAL != BACKUP_EXECUTION_APPROVAL != MIGRATION_APPROVAL != DEPLOYMENT_APPROVAL != MERGE_APPROVAL`.
- Any unexpected Golden Corpus difference in score, eligibility, lifecycle, reason codes, ordering, execution plan, or signal levels blocks the wave.

## PR / Review Decomposition

Keep Wave 1C sequential and reviewable rather than one oversized code PR:

1. **P1-C1 — Metadata foundation:** Tasks 1–3. Domain metadata contract, schema v3, immutable table/view verification. No active persistence/consumer cutover.
2. **P1-C2 — Persistence, classification, consumer/runtime cutover, and certification:** Tasks 4–10. Atomic future persistence, explicit producer lineage, deterministic legacy classifier, canonical readers, STRICT-default report policy, startup completeness gate, fallback guards, and Golden/full verification. Tasks 4–6 form a mandatory internal review checkpoint before Tasks 7–10 continue on the same slice.

Each implementation PR is draft until its focused/full tests, security/static review, and controller review pass. No PR is merged without separate `MERGE_APPROVAL`.

---

## File Structure Map

### New focused modules

- `backend/src/waterfallhunter/core/signal_metadata.py`
  - Owns `SignalMetadataInput`, metadata constants, lineage-pair validation, RFC8785/SHA-256 helper, and canonical metadata serialization.
  - No DB I/O.
- `backend/src/waterfallhunter/core/signal_metadata_store.py`
  - Owns read-only completeness checks and canonical metadata/view query helpers used by startup and consumers.
  - No classification and no auto-migration.
- `backend/src/waterfallhunter/core/legacy_signal_classifier.py`
  - Owns deterministic legacy-evidence extraction/classification and append-only metadata insertion for explicit classification runs on disposable/restored/dev DBs.
  - Never consults current application defaults to reinterpret history.
- `backend/src/waterfallhunter/core/managed_sqlite.py`
  - Owns the first-party writable connection factory, enables and verifies `PRAGMA foreign_keys=ON`, and closes/fails before mutation if enforcement cannot be enabled.
- `backend/src/waterfallhunter/migrations/0003_signal_metadata.sql`
  - Owns schema version 3 DDL for `signal_metadata`, immutable triggers, and `canonical_signal_view`.

### Existing files modified

- `backend/src/waterfallhunter/core/contracts.py`: reuse `SignalClass`; never add a third class.
- `backend/src/waterfallhunter/core/schema_contract.py`: set schema version 3 and verify table + managed view.
- `backend/src/waterfallhunter/core/lbank_signal_ledger.py`: atomically persist metadata inside existing catalogue-CAS transaction.
- `backend/src/waterfallhunter/core/decision_provenance.py`: add RFC8785/SHA-256 decision-contract helper.
- `backend/src/waterfallhunter/main.py`: produce explicit lineage, pass metadata, and add completeness gate before workers.
- `backend/src/waterfallhunter/core/lbank_signal_outcome.py`: read canonical signal rows and carry cohort fields.
- `backend/src/waterfallhunter/core/lbank_execution_outcome_report.py`: STRICT production default with explicit research cohorts.
- `backend/tests/schema_test_support.py`: continue using first-party migration command; packaged migrations naturally advance tests to v3.

### Tests

- Create `backend/tests/test_signal_metadata_contract.py`
- Create `backend/tests/test_signal_metadata_schema.py`
- Create `backend/tests/test_signal_metadata_persistence.py`
- Create `backend/tests/test_legacy_signal_classifier.py`
- Create `backend/tests/test_canonical_signal_consumers.py`
- Create `backend/tests/test_signal_metadata_startup_gate.py`
- Create `backend/tests/test_signal_metadata_fallback_guard.py`
- Modify `backend/tests/test_lbank_signal_ledger.py`
- Modify `backend/tests/test_lbank_signal_outcome.py`
- Modify `backend/tests/test_lbank_execution_outcome_report.py`
- Mechanically update migration/readiness tests that assert current packaged schema version.

---

### Task 1: Canonical Metadata Domain Contract and Provenance Hash

**Files:**
- Create: `backend/src/waterfallhunter/core/signal_metadata.py`
- Modify: `backend/src/waterfallhunter/core/decision_provenance.py`
- Test: `backend/tests/test_signal_metadata_contract.py`
- Modify test: `backend/tests/test_decision_provenance.py`

**Interfaces:**
- Consumes: `SignalClass`; `canonical_json_bytes(value) -> bytes`.
- Produces:
  - `METADATA_CONTRACT_VERSION = "signal_metadata_v1"`
  - `STRICT_STRATEGY_PROFILE = "strict_score_v2"`
  - `EXPERIMENTAL_STRATEGY_PROFILE = "experimental_pretrigger_v1"`
  - `MODEL_GENERATION = "waterfall_signal_model_v1"`
  - `ClassificationMethod.FUTURE_PIPELINE_EXPLICIT`
  - `ClassificationMethod.LEGACY_PROFILE_EXACT_MATCH`
  - immutable `SignalMetadataInput`
  - `validate_lineage_pair(signal_class, strategy_profile) -> None`
  - `canonical_sha256(value) -> str`
  - `decision_contract_sha256(contract) -> str`

- [ ] **Step 1: Write failing metadata-contract tests**

```python
from pydantic import ValidationError
import pytest

from waterfallhunter.core.contracts import SignalClass
from waterfallhunter.core.signal_metadata import (
    ClassificationMethod,
    EXPERIMENTAL_STRATEGY_PROFILE,
    METADATA_CONTRACT_VERSION,
    MODEL_GENERATION,
    STRICT_STRATEGY_PROFILE,
    SignalMetadataInput,
    canonical_sha256,
)


def _metadata(**overrides):
    values = {
        "signal_class": SignalClass.STRICT,
        "strategy_profile": STRICT_STRATEGY_PROFILE,
        "score_version": "score_v2",
        "model_generation": MODEL_GENERATION,
        "decision_contract_hash": "a" * 64,
        "analysis_observed_at": 1_700_000_000,
        "reference_observed_at": 1_699_999_990,
        "metadata_contract_version": METADATA_CONTRACT_VERSION,
        "classification_method": ClassificationMethod.FUTURE_PIPELINE_EXPLICIT,
        "classification_evidence_hash": None,
    }
    values.update(overrides)
    return SignalMetadataInput(**values)


def test_strict_and_experimental_profiles_are_explicit():
    assert _metadata().signal_class is SignalClass.STRICT
    experimental = _metadata(
        signal_class=SignalClass.EXPERIMENTAL,
        strategy_profile=EXPERIMENTAL_STRATEGY_PROFILE,
        score_version="score_v2_watch_v1",
    )
    assert experimental.signal_class is SignalClass.EXPERIMENTAL


@pytest.mark.parametrize(
    ("signal_class", "profile"),
    [
        (SignalClass.STRICT, EXPERIMENTAL_STRATEGY_PROFILE),
        (SignalClass.EXPERIMENTAL, STRICT_STRATEGY_PROFILE),
        (SignalClass.STRICT, ""),
    ],
)
def test_invalid_lineage_pairs_fail_closed(signal_class, profile):
    with pytest.raises(ValidationError):
        _metadata(signal_class=signal_class, strategy_profile=profile)


@pytest.mark.parametrize(
    ("signal_class", "profile", "score_version"),
    [
        (SignalClass.STRICT, STRICT_STRATEGY_PROFILE, "score_v2_watch_v1"),
        (SignalClass.EXPERIMENTAL, EXPERIMENTAL_STRATEGY_PROFILE, "score_v2"),
    ],
)
def test_invalid_score_version_lineage_fails_closed(
    signal_class, profile, score_version
):
    with pytest.raises(ValidationError):
        _metadata(
            signal_class=signal_class,
            strategy_profile=profile,
            score_version=score_version,
        )


def test_canonical_sha256_is_order_independent_and_rejects_nonfinite():
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})
    with pytest.raises(ValueError):
        canonical_sha256({"bad": float("nan")})
```

- [ ] **Step 2: Run test and verify RED**

```bash
pytest -q backend/tests/test_signal_metadata_contract.py backend/tests/test_decision_provenance.py
```

Expected: import/attribute failure because metadata primitives do not yet exist.

- [ ] **Step 3: Implement the minimal contract**

Create `signal_metadata.py` using frozen Pydantic v2, import `SignalClass`, validate only the two exact class/profile/score-version triples, require 64-char lowercase hex hashes, and require legacy classifications to carry `classification_evidence_hash` while future explicit lineage must not carry one.

Canonical hash implementation:

```python
def canonical_sha256(value) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
```

In `decision_provenance.py`:

```python
from waterfallhunter.core.signal_metadata import canonical_sha256


def decision_contract_sha256(contract: dict[str, Any]) -> str:
    return canonical_sha256(contract)
```

Do not alter `build_decision_contract()` semantics.

- [ ] **Step 4: Run focused tests and verify GREEN**

```bash
pytest -q backend/tests/test_signal_metadata_contract.py backend/tests/test_decision_provenance.py
pytest -q backend/tests/test_canonical_contracts.py \
          backend/tests/test_canonical_contract_determinism.py \
          backend/tests/test_foundation_tooling.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/waterfallhunter/core/signal_metadata.py \
        backend/src/waterfallhunter/core/decision_provenance.py \
        backend/tests/test_signal_metadata_contract.py \
        backend/tests/test_decision_provenance.py
git commit -m "feat: define canonical signal metadata lineage"
```

**Reviewer gate:** reject inference from absence, hashing that bypasses RFC8785, or any `SignalDecisionPacket` behavior change.

---

### Task 2: Schema Version 3, Immutable Metadata, and Canonical View

**Files:**
- Create: `backend/src/waterfallhunter/migrations/0003_signal_metadata.sql`
- Modify: `backend/src/waterfallhunter/core/schema_contract.py`
- Create: `backend/tests/test_signal_metadata_schema.py`
- Modify: `backend/tests/test_schema_contract.py`
- Modify: `backend/tests/test_migrations.py`
- Modify packaged-version assertions in migration/readiness tests.

**Interfaces:**
- Produces `CURRENT_RUNTIME_SCHEMA_VERSION = 3`, managed `signal_metadata`, managed `canonical_signal_view`, `managed_runtime_view_names()`, and `VIEW_MISMATCH` verification.

- [ ] **Step 1: Write RED schema tests**

Assert a disposable migrated DB reaches `PRAGMA user_version = 3`, contains exactly the metadata columns from the approved spec, rejects mismatched class/profile/score-version tuples at the SQLite boundary, has immutable UPDATE/DELETE triggers, and exposes the exact complete canonical normalized view definition.

Also seed one ledger-only row and one ledger+metadata row; the canonical view must expose only the latter.

- [ ] **Step 2: Run RED**

```bash
pytest -q backend/tests/test_signal_metadata_schema.py backend/tests/test_schema_contract.py backend/tests/test_migrations.py
```

Expected: missing migration/table/view/version failures.

- [ ] **Step 3: Add `0003_signal_metadata.sql`**

The migration creates schema only—no backfill/ledger rewrite. Required core DDL:

```sql
CREATE TABLE signal_metadata (
    signal_id INTEGER PRIMARY KEY,
    signal_class TEXT NOT NULL,
    strategy_profile TEXT NOT NULL,
    score_version TEXT NOT NULL,
    model_generation TEXT NOT NULL,
    decision_contract_hash TEXT NOT NULL,
    analysis_observed_at INTEGER NOT NULL,
    reference_observed_at INTEGER,
    metadata_contract_version TEXT NOT NULL,
    classification_method TEXT NOT NULL,
    classification_evidence_hash TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(signal_id) REFERENCES lbank_signal_ledger(id),
    CHECK(signal_class IN ('STRICT', 'EXPERIMENTAL')),
    CHECK(length(strategy_profile) > 0),
    CHECK(length(score_version) > 0),
    CHECK(length(model_generation) > 0),
    CHECK(length(decision_contract_hash) = 64),
    CHECK(decision_contract_hash NOT GLOB '*[^0-9a-f]*'),
    CHECK(analysis_observed_at >= 0),
    CHECK(reference_observed_at IS NULL OR reference_observed_at >= 0),
    CHECK(metadata_contract_version = 'signal_metadata_v1'),
    CHECK(classification_method IN ('FUTURE_PIPELINE_EXPLICIT','LEGACY_PROFILE_EXACT_MATCH')),
    CHECK(
      (classification_method='FUTURE_PIPELINE_EXPLICIT' AND classification_evidence_hash IS NULL)
      OR
      (classification_method='LEGACY_PROFILE_EXACT_MATCH'
       AND length(classification_evidence_hash)=64
       AND classification_evidence_hash NOT GLOB '*[^0-9a-f]*')
    ),
    CHECK(
      (signal_class='STRICT'
       AND strategy_profile='strict_score_v2'
       AND score_version='score_v2')
      OR
      (signal_class='EXPERIMENTAL'
       AND strategy_profile='experimental_pretrigger_v1'
       AND score_version='score_v2_watch_v1')
    )
);
```

Add canonical immutability triggers and `canonical_signal_view` with explicit ledger/metadata columns and `INNER JOIN signal_metadata AS m ON m.signal_id = s.id`; finish with `PRAGMA user_version=3;`.

- [ ] **Step 4: Add first-class view verification**

Add a focused immutable `ManagedViewSpec` in `schema_contract.py`, register `canonical_signal_view`, and compare its complete normalized executable definition from `sqlite_master` with the canonical manifest definition. The comparison must bind every selected expression and alias, the exact INNER JOIN, and the absence of extra predicates/clauses; required/forbidden fragment checks alone are not sufficient. Treat any missing or drifted view as schema failure. Do not model the view as a table.

- [ ] **Step 5: Run focused schema/migration/readiness tests**

```bash
pytest -q backend/tests/test_signal_metadata_schema.py \
          backend/tests/test_schema_contract.py \
          backend/tests/test_migrations.py \
          backend/tests/test_migration_preflight.py \
          backend/tests/test_db_readiness.py
```

Expected: PASS.

- [ ] **Step 6: Run full backend and commit**

```bash
pytest -q backend/tests
git add backend/src/waterfallhunter/migrations/0003_signal_metadata.sql \
        backend/src/waterfallhunter/core/schema_contract.py \
        backend/tests/test_signal_metadata_schema.py \
        backend/tests/test_schema_contract.py \
        backend/tests/test_migrations.py \
        backend/tests/test_migration_preflight.py \
        backend/tests/test_db_readiness.py
git commit -m "feat: add signal metadata schema and canonical view"
```

**Reviewer gate:** reject backfill in migration 3, LEFT JOIN, runtime DDL, or unverified view drift.

---

### Task 3: Read-only Metadata Completeness Primitive

**Files:**
- Create: `backend/src/waterfallhunter/core/signal_metadata_store.py`
- Extend: `backend/tests/test_signal_metadata_schema.py`
- Create: `backend/tests/test_signal_metadata_startup_gate.py`

**Interfaces:**
- Produces `MetadataCompletenessResult`, `SignalMetadataError`, `verify_signal_metadata_completeness(db_path)`, `require_signal_metadata_completeness(db_path)`, and a verify-only `SignalMetadataStore`.

- [ ] **Step 1: Write RED completeness tests**

Cover zero-signal PASS, complete ledger+metadata PASS, missing metadata FAIL, orphan metadata FAIL using a deliberately corrupted fixture, invalid class/profile/score-version metadata FAIL even when a weakened test schema permits insertion, and no filesystem mutation before/after read-only verification.

- [ ] **Step 2: Run RED**

```bash
pytest -q backend/tests/test_signal_metadata_schema.py backend/tests/test_signal_metadata_startup_gate.py
```

- [ ] **Step 3: Implement read-only verification**

Open SQLite via `Path.resolve().as_uri() + '?mode=ro'` and call `sqlite3.connect(database_uri, uri=True, ...)` so `mode=ro` is enforced. Also set `PRAGMA query_only=ON`; count ledger, metadata, canonical, missing, orphan, and invalid metadata rows; and prove a missing target path remains absent. Independently validate every metadata row against the exact class/profile/score-version triples and the full metadata contract even when a weakened schema/view permits the row. Any invalid row makes completeness fail with `INVALID_METADATA`, and `require_signal_metadata_completeness()` raises a stable `SignalMetadataError` beginning `SIGNAL_METADATA_INCOMPLETE`. It never migrates, inserts, updates, deletes, repairs, or creates a DB.

- [ ] **Step 4: Verify and commit P1-C1 foundation**

```bash
pytest -q backend/tests/test_signal_metadata_contract.py \
          backend/tests/test_signal_metadata_schema.py \
          backend/tests/test_signal_metadata_startup_gate.py \
          backend/tests/test_schema_contract.py \
          backend/tests/test_migrations.py
pytest -q backend/tests
git add backend/src/waterfallhunter/core/signal_metadata_store.py \
        backend/tests/test_signal_metadata_schema.py \
        backend/tests/test_signal_metadata_startup_gate.py
git commit -m "feat: add read-only signal metadata completeness checks"
```

After the design/plan PR is merged, open a clean draft **P1-C1** PR targeting `main`. Required CI, Sonar, and independent review must pass before P1-C2 starts.

**Reviewer gate:** check is read-only, zero-signal DB passes, incomplete DB is never repaired.

---

### Task 4: Atomic Future Ledger + Metadata Persistence

**Files:**
- Create: `backend/src/waterfallhunter/core/managed_sqlite.py`
- Modify: `backend/src/waterfallhunter/core/lbank_signal_ledger.py`
- Modify writer wrappers: `db.py`, `db_readiness.py`, `feature_replay.py`, `historical_outcome_store.py`, `lbank_execution_decision.py`, `lbank_execution_stats.py`, `lbank_execution_store.py`, `lbank_signal_outcome.py`, `production_evidence.py`, `provider_registry.py`, and `stage_lifecycle.py`.
- Create: `backend/tests/test_signal_metadata_persistence.py`
- Create: `backend/tests/test_managed_sqlite_foreign_keys.py`
- Modify: `backend/tests/test_lbank_signal_ledger.py`
- Modify every existing test caller: `backend/tests/test_lbank_signal_outcome.py` and `backend/tests/test_lbank_execution_outcome_report.py` in addition to the ledger tests.

**Interface:** `persist_trigger(..., metadata: SignalMetadataInput, metadata_created_at: int | None = None) -> int | None`.

- [ ] **Step 1: Write RED atomicity tests**

Test STRICT and EXPERIMENTAL inserts. Test metadata failure rolls back catalogue status + ledger + metadata. Test stale CAS inserts neither ledger nor metadata. Update all seven existing `persist_trigger()` test callers to pass a valid `SignalMetadataInput` fixture and retain every trigger-path assertion; a repository search test must prevent an unconverted caller. Attempt orphan inserts through the production writer connection and require `sqlite3.IntegrityError`. Add a repository guard proving every first-party managed-schema writer uses the common foreign-key-enforcing connection factory; read-only verifiers and the migration owner are explicit audited exceptions.

- [ ] **Step 2: Run RED**

```bash
pytest -q backend/tests/test_signal_metadata_persistence.py backend/tests/test_lbank_signal_ledger.py
```

Expected: `persist_trigger()` does not yet accept metadata.

- [ ] **Step 3: Insert metadata inside the existing transaction**

Validate `SignalMetadataInput` before DB mutation. Route writable managed-database connections through `managed_sqlite.py`; the factory must enable `PRAGMA foreign_keys=ON`, read it back as `1`, and fail closed before `BEGIN` otherwise. Migrate every production writer wrapper to that factory, including ledger, outcome, replay/evidence, lifecycle/provider/execution, historical-outcome, and legacy-classification writers. After ledger insert and before returning, execute one parameterized `INSERT INTO signal_metadata (...) VALUES (...)` using the inserted ledger id. Do not catch metadata insertion separately; any exception must cause the transaction to roll back the preceding catalogue CAS and ledger insert.

- [ ] **Step 4: Run GREEN and commit**

```bash
pytest -q backend/tests/test_signal_metadata_persistence.py \
          backend/tests/test_managed_sqlite_foreign_keys.py \
          backend/tests/test_lbank_signal_ledger.py \
          backend/tests/test_lbank_signal_outcome.py \
          backend/tests/test_lbank_execution_outcome_report.py
git add backend/src/waterfallhunter/core/lbank_signal_ledger.py \
        backend/src/waterfallhunter/core/managed_sqlite.py \
        backend/src/waterfallhunter/core/db.py \
        backend/src/waterfallhunter/core/db_readiness.py \
        backend/src/waterfallhunter/core/feature_replay.py \
        backend/src/waterfallhunter/core/historical_outcome_store.py \
        backend/src/waterfallhunter/core/lbank_execution_decision.py \
        backend/src/waterfallhunter/core/lbank_execution_stats.py \
        backend/src/waterfallhunter/core/lbank_execution_store.py \
        backend/src/waterfallhunter/core/lbank_signal_outcome.py \
        backend/src/waterfallhunter/core/production_evidence.py \
        backend/src/waterfallhunter/core/provider_registry.py \
        backend/src/waterfallhunter/core/stage_lifecycle.py \
        backend/tests/test_signal_metadata_persistence.py \
        backend/tests/test_managed_sqlite_foreign_keys.py \
        backend/tests/test_lbank_signal_ledger.py \
        backend/tests/test_lbank_signal_outcome.py \
        backend/tests/test_lbank_execution_outcome_report.py
git commit -m "feat: persist signal metadata atomically with ledger"
```

**Reviewer gate:** reject two-transaction/post-commit metadata writes or metadata inference inside the ledger store.

---

### Task 5: Explicit Future Metadata Producer at Signal Call Site

**Files:**
- Modify: `backend/src/waterfallhunter/main.py`
- Modify: `backend/src/waterfallhunter/core/decision_provenance.py`
- Modify: `backend/src/waterfallhunter/core/signal_metadata.py`
- Extend: `backend/tests/test_signal_metadata_persistence.py`
- Modify: `backend/tests/test_stale_trigger_safety.py`

**Interface:** pure `build_signal_metadata_input(metrics: dict, decision_contract_hash: str) -> SignalMetadataInput`.

- [ ] **Step 1: Write RED producer tests**

Missing/unknown `strategy_profile` must raise. Exact `experimental_pretrigger_v1` maps only to EXPERIMENTAL. Strict trigger path must emit `strict_score_v2` explicitly; absence never means STRICT.

- [ ] **Step 2: Run RED**

```bash
pytest -q backend/tests/test_signal_metadata_persistence.py backend/tests/test_stale_trigger_safety.py
```

- [ ] **Step 3: Compute deterministic decision-contract hash**

Use `decision_contract_sha256(build_decision_contract(...))` once from deterministic contract data. Do not hash volatile result packets.

- [ ] **Step 4: Build metadata before `persist_trigger`**

Require recognized exact profile, exact `score_version`, actual analysis observation timestamp, optional reference timestamp, `MODEL_GENERATION`, and `FUTURE_PIPELINE_EXPLICIT`. If a stable analysis timestamp is not currently produced, add it at the observation producer; do not substitute persistence time.

- [ ] **Step 5: Pass metadata into the sole active ledger call and run tests**

```bash
pytest -q backend/tests/test_signal_metadata_persistence.py \
          backend/tests/test_stale_trigger_safety.py \
          backend/tests/test_lbank_signal_ledger.py
pytest -q backend/tests
```

- [ ] **Step 6: Commit**

```bash
git add backend/src/waterfallhunter/main.py \
        backend/src/waterfallhunter/core/decision_provenance.py \
        backend/src/waterfallhunter/core/signal_metadata.py \
        backend/tests/test_signal_metadata_persistence.py \
        backend/tests/test_stale_trigger_safety.py
git commit -m "feat: emit explicit signal lineage at persistence boundary"
```

**Reviewer gate:** reject fake `time.time()` analysis timestamps, unknown-profile fallback, or trigger-eligibility changes.

---

### Task 6: Deterministic Legacy Classifier and Append-only Metadata Operation

**Files:**
- Create: `backend/src/waterfallhunter/core/legacy_signal_classifier.py`
- Create: `backend/tests/test_legacy_signal_classifier.py`
- Create: `backend/tests/fixtures/legacy_signal_metadata_cases.json`

**Interfaces:**
- `LegacyClassificationStatus = RESOLVED | UNRESOLVED | CONFLICT` (classifier status only, never `SignalClass`).
- `classify_legacy_evidence(signal_row) -> LegacyClassificationDecision`
- `preview_legacy_classification(db_path) -> LegacyClassificationReport`
- `apply_legacy_classification(db_path, *, expected_report_hash: str, created_at: int | None = None) -> LegacyClassificationReport`

- [ ] **Step 1: Freeze classifier fixture cases**

Include complete EXPERIMENTAL evidence, missing decision hash, missing analysis timestamp, unknown profile, contradictory class/profile, and complete STRICT evidence only when exact strict profile is historically persisted.

- [ ] **Step 2: Write RED classifier tests**

Prove `experimental_pretrigger_v1` alone is insufficient, evidence hashes are deterministic, preview is read-only, mismatched `expected_report_hash` fails before write, and unresolved/conflicting rows receive no metadata. Add a TOCTOU regression in which database state changes after preview but before apply obtains its write lock; apply must reject the stale hash before inserting metadata. Add an identical-rerun test proving no row is rewritten, plus a conflicting-rerun test proving the whole operation rolls back.

- [ ] **Step 3: Implement pure classification**

Build the evidence envelope only from persisted historical fields actually used. Missing/malformed mandatory values return UNRESOLVED; contradictory exact evidence returns CONFLICT. Never import current validator/settings defaults to fill historical gaps.

- [ ] **Step 4: Implement preview/apply**

Preview opens read-only, classifies every ledger row, and returns deterministic counts/IDs/report hash. Apply requires schema v3, obtains `BEGIN IMMEDIATE`, recomputes the complete classification report and hash from the locked database state, compares that locked-state hash with `expected_report_hash`, and rolls back before insertion on mismatch. Only the recomputed matching decision set may be applied. For each RESOLVED signal under that lock: INSERT only when metadata is absent; skip only an exact pre-existing match; and fail/roll back the entire operation on any mismatch. `created_at` participates in equivalence: an explicit retry value must equal the stored value, while a retry with `created_at=None` preserves and compares against the stored timestamp rather than generating a new value for an existing row. Never use `INSERT OR REPLACE`, UPDATE, or DELETE.

- [ ] **Step 5: Verify unresolved rows remain non-canonical**

For a two-row fixture with one resolved and one unresolved: ledger count 2, metadata count 1, canonical view count 1, completeness gate FAILS. That failure is expected and is the deployment blocker rather than a reason to weaken the gate.

- [ ] **Step 6: Run full tests and commit P1-C2**

```bash
pytest -q backend/tests/test_legacy_signal_classifier.py \
          backend/tests/test_signal_metadata_schema.py \
          backend/tests/test_signal_metadata_persistence.py
pytest -q backend/tests
git add backend/src/waterfallhunter/core/legacy_signal_classifier.py \
        backend/tests/test_legacy_signal_classifier.py \
        backend/tests/fixtures/legacy_signal_metadata_cases.json
git commit -m "feat: add deterministic legacy signal classifier"
```

After P1-C1 is merged, open a clean draft **P1-C2** PR targeting `main`.

**Reviewer gate:** reject current-default reconstruction, ledger UPDATE, guessed STRICT classification, `INSERT OR REPLACE`, or any preview write.

---

### Task 7: Canonical Outcome Settlement and Explicit Cohort Policy

**Files:**
- Modify: `backend/src/waterfallhunter/core/lbank_signal_outcome.py`
- Create: `backend/tests/test_canonical_signal_consumers.py`
- Modify: `backend/tests/test_lbank_signal_outcome.py`

- [ ] **Step 1: Write RED consumer tests**

Seed STRICT canonical, EXPERIMENTAL canonical, and ledger-only unresolved rows. `pending_signals()` must return only the first two and include explicit `signal_class`/`strategy_profile`.

- [ ] **Step 2: Run RED**

```bash
pytest -q backend/tests/test_canonical_signal_consumers.py backend/tests/test_lbank_signal_outcome.py
```

- [ ] **Step 3: Replace direct ledger source with canonical view**

Use `canonical_signal_view AS s` joined to outcomes on `s.signal_id`; select `s.signal_id AS id`, `s.signal_class`, and `s.strategy_profile` plus current settlement fields. Settlement includes both canonical cohorts because it records research evidence. Do not parse JSON for cohort.

- [ ] **Step 4: Run GREEN and commit**

```bash
pytest -q backend/tests/test_canonical_signal_consumers.py backend/tests/test_lbank_signal_outcome.py
git add backend/src/waterfallhunter/core/lbank_signal_outcome.py \
        backend/tests/test_canonical_signal_consumers.py \
        backend/tests/test_lbank_signal_outcome.py
git commit -m "feat: settle outcomes from canonical signal cohorts"
```

---

### Task 8: STRICT-default Reporting with Explicit Research Cohorts

**Files:**
- Modify: `backend/src/waterfallhunter/core/lbank_execution_outcome_report.py`
- Modify: `backend/tests/test_lbank_execution_outcome_report.py`
- Extend: `backend/tests/test_canonical_signal_consumers.py`

**Interface:** `ReportCohort = STRICT | EXPERIMENTAL | MIXED_RESEARCH`; constructor default `STRICT`.

- [ ] **Step 1: Write RED cohort tests**

Default counts only STRICT. Explicit EXPERIMENTAL counts only experimental. `MIXED_RESEARCH` counts both and is labeled `research_only=True`. Unresolved ledger-only rows never appear.

- [ ] **Step 2: Run RED**

```bash
pytest -q backend/tests/test_lbank_execution_outcome_report.py backend/tests/test_canonical_signal_consumers.py
```

- [ ] **Step 3: Query canonical view with parameterized cohort filters**

Do not interpolate class values into SQL. Every report returns `signal_class_scope` and `research_only`. No path calls this a calibrated probability.

- [ ] **Step 4: Run focused/full tests and commit**

```bash
pytest -q backend/tests/test_lbank_execution_outcome_report.py backend/tests/test_canonical_signal_consumers.py
pytest -q backend/tests
git add backend/src/waterfallhunter/core/lbank_execution_outcome_report.py \
        backend/tests/test_lbank_execution_outcome_report.py \
        backend/tests/test_canonical_signal_consumers.py
git commit -m "feat: default outcome reports to strict cohort"
```

**Reviewer gate:** production default STRICT; mixed always research-only.

---

### Task 9: Startup Completeness Gate and Repository Fallback Guard

**Files:**
- Modify: `backend/src/waterfallhunter/main.py`
- Modify: `backend/tests/test_signal_metadata_startup_gate.py`
- Create: `backend/tests/test_signal_metadata_fallback_guard.py`

- [ ] **Step 1: Write RED startup-order tests**

Incomplete v3 DB must raise `SignalMetadataError` before any `_start_background_task`. Complete v3 DB reaches worker scheduling.

- [ ] **Step 2: Write repository fallback guard**

Scan canonical consumer files and reject default-to-STRICT patterns, metadata LEFT JOIN fallback, or cohort decisions from `trigger_metrics_json` outside `legacy_signal_classifier.py`. The classifier is the only allowed historical cohort-evidence JSON reader.

- [ ] **Step 3: Run RED**

```bash
pytest -q backend/tests/test_signal_metadata_startup_gate.py backend/tests/test_signal_metadata_fallback_guard.py
```

- [ ] **Step 4: Insert read-only startup gate**

Immediately after existing full schema verification and before the first background worker:

```python
require_managed_schema(
    db.db_path,
    check_user_version=CURRENT_RUNTIME_SCHEMA_VERSION,
)
require_signal_metadata_completeness(db.db_path)
```

No migration/classification/repair from startup.

- [ ] **Step 5: Run startup/guard/full backend tests and commit**

```bash
pytest -q backend/tests/test_signal_metadata_startup_gate.py backend/tests/test_signal_metadata_fallback_guard.py
pytest -q backend/tests
git add backend/src/waterfallhunter/main.py \
        backend/tests/test_signal_metadata_startup_gate.py \
        backend/tests/test_signal_metadata_fallback_guard.py
git commit -m "feat: fail startup closed on incomplete signal metadata"
```

---

### Task 10: Golden Corpus, Full Verification, Independent Review, and Certification

**Files:**
- Modify Golden/model-regression fixtures only for expected lineage persistence/query output; never normalize score/lifecycle/execution differences.
- Modify: `docs/program/EXECUTION_LEDGER.md`
- Update P1-C PR descriptions with exact evidence.

- [ ] **Step 1: Run focused P1-C suite**

```bash
pytest -q \
  backend/tests/test_signal_metadata_contract.py \
  backend/tests/test_signal_metadata_schema.py \
  backend/tests/test_signal_metadata_persistence.py \
  backend/tests/test_legacy_signal_classifier.py \
  backend/tests/test_canonical_signal_consumers.py \
  backend/tests/test_signal_metadata_startup_gate.py \
  backend/tests/test_signal_metadata_fallback_guard.py \
  backend/tests/test_lbank_signal_ledger.py \
  backend/tests/test_lbank_signal_outcome.py \
  backend/tests/test_lbank_execution_outcome_report.py
```

- [ ] **Step 2: Run full backend and deterministic replay**

Run repository-provided full backend plus existing Wave-0 Golden/model-regression commands. Repeat canonical replay three times. Any unexpected score/eligibility/lifecycle/ranking/Entry-TP-SL/execution semantic difference is a blocker and must not be normalized into fixtures.

- [ ] **Step 3: Run frontend/dependency/repository gates**

Use the exact Node/Python versions and commands from current CI: frontend typecheck/build, npm audit, Python dependency audit with existing explicit exceptions only, repository hygiene/secret scan.

- [ ] **Step 4: Build/test exact production artifact family**

Run Compose validation, backend image build, exact backend artifact test family, and OCI revision-label verification against current GitHub SHA.

- [ ] **Step 5: Run Sonar/security diff review**

Quality Gate must pass; no unresolved vulnerability/security-hotspot. Security review scope includes migration/view SQL, persistence transaction, classifier, and startup gate.

- [ ] **Step 6: Request independent CodeRabbit/controller review**

Explicitly answer:
1. Can a future signal commit without metadata?
2. Can unresolved legacy enter canonical view?
3. Can missing lineage default to STRICT?
4. Can metadata failure leave catalogue/ledger partially committed?
5. Can default reports include EXPERIMENTAL?
6. Can startup migrate/classify/repair?
7. Can classifier use current defaults?
8. Did score/lifecycle/ranking/execution semantics change?

- [ ] **Step 7: Update execution ledger**

Record baseline/head SHAs, PRs, migration 3, RED/GREEN counts, Golden diff, classifier resolved/unresolved/conflict counts, CI, Sonar/security, CodeRabbit rounds, blockers, and development-side certification.

- [ ] **Step 8: Final state without merge**

If all development gates pass, mark P1-C2 and the overall Wave 1C stack `MERGE_READY_PENDING_MERGE_APPROVAL`. If Production-like legacy evidence cannot satisfy completeness, record that as an explicit migration/deployment blocker; do not weaken the gate.

---

## Self-Review Checklist

- Every approved spec section maps to a task.
- No third signal class is introduced.
- No missing lineage defaults to STRICT.
- Migration 3 creates schema only and does not backfill ledger rows.
- Future persistence is one transaction.
- Legacy preview is read-only and apply is explicit/hash-bound.
- Unresolved/conflicting legacy rows receive no metadata.
- Canonical view is explicit INNER JOIN.
- Production report default is STRICT; mixed is research-only.
- Startup is verify-only and runs before workers.
- Hashing uses RFC8785/JCS.
- No Production operation is implied by development tests.
- Golden/model semantic changes are blockers.
- PRs remain sequential/draft/unmerged absent separate approval.
