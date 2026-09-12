# Wave 1A Canonical Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add validated, deterministic canonical domain contracts without changing active WaterfallHunter model behavior or consumers.

**Architecture:** Introduce one focused `core/contracts.py` module containing only domain vocabulary and typed Pydantic packets. Existing `core/models.py`, ScoreV2, lifecycle, ranking, persistence, dashboard, and Telegram remain untouched. Tests exercise fail-closed validation and deterministic serialization; existing Golden Corpus/model tests remain the semantic regression gate.

**Tech Stack:** Python 3.13, Pydantic 2.13.x, pytest, existing RFC 8785 canonical hashing from Wave 0.

**Spec:** `docs/superpowers/specs/2026-08-20-wave1-canonical-contracts-design.md`

## Global Constraints

- `LIVE_TRADING_ENABLED=false` remains invariant.
- `execution_mode=SIGNAL_ONLY` only.
- `margin_mode=ISOLATED` only; cross margin and auto-add remain false.
- No active consumer cutover in Wave 1A.
- No ScoreV2/lifecycle/trigger/ranking/execution-level semantic change.
- No Production backup, DB write/migration, restart, deployment, Telegram send, or merge.
- Unexpected Golden Corpus/model regression is a blocker.

---

### Task 1: Canonical vocabulary and decision status

**Files:**
- Create: `backend/src/waterfallhunter/core/contracts.py`
- Create: `backend/tests/test_canonical_contracts.py`

**Interfaces:**
- Produces: `SourceRevisionStatus`, `SignalClass`, `LifecycleState`, `DecisionPrimary`, `DecisionQualifier`, `ExecutionMode`, `MarginMode`, `PositionExecutionState`, `PositionThesisState`, `DecisionStatus`.
- Consumers: later tasks in this plan only; active production code does not import these yet.

- [ ] **Step 1: Write failing tests for the vocabulary and fail-closed enums**

```python
from importlib import import_module, util
import pytest
from pydantic import ValidationError


def _contracts():
    spec = util.find_spec("waterfallhunter.core.contracts")
    assert spec is not None, "canonical contracts module must exist"
    return import_module("waterfallhunter.core.contracts")


def test_signal_class_is_only_strict_or_experimental():
    contracts = _contracts()
    assert {item.value for item in contracts.SignalClass} == {"STRICT", "EXPERIMENTAL"}


def test_decision_status_canonicalizes_qualifier_order_and_duplicates():
    contracts = _contracts()
    status = contracts.DecisionStatus(
        primary="CONFIRMED",
        qualifiers=["STALE_REFERENCE", "AI_CAUTION", "AI_CAUTION"],
    )
    assert status.qualifiers == (
        contracts.DecisionQualifier.AI_CAUTION,
        contracts.DecisionQualifier.STALE_REFERENCE,
    )


def test_unknown_decision_primary_is_rejected():
    contracts = _contracts()
    with pytest.raises(ValidationError):
        contracts.DecisionStatus(primary="MAYBE")
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests/test_canonical_contracts.py
```

Expected: assertion failure that `waterfallhunter.core.contracts` does not exist.

- [ ] **Step 3: Implement the minimal vocabulary**

Use `str, Enum` values exactly matching the approved spec. Implement `DecisionStatus` with a `tuple[DecisionQualifier, ...]` and a Pydantic `field_validator(..., mode="before")` that deduplicates and sorts qualifier values by their canonical enum string.

- [ ] **Step 4: Run focused tests and verify GREEN**

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests/test_canonical_contracts.py
```

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/waterfallhunter/core/contracts.py backend/tests/test_canonical_contracts.py
git commit -m "feat: add canonical contract vocabulary"
```

---

### Task 2: Common envelope, evidence, signal decision, and execution plan

**Files:**
- Modify: `backend/src/waterfallhunter/core/contracts.py`
- Modify: `backend/tests/test_canonical_contracts.py`

**Interfaces:**
- Produces: `CommonContractEnvelope`, `EvidenceQualityPacket`, `SignalDecisionPacket`, `ExecutionPlan`.
- Uses: enums and `DecisionStatus` from Task 1.

- [ ] **Step 1: Add failing packet tests**

```python
def test_signal_decision_packet_keeps_probability_unavailable_explicit():
    contracts = _contracts()
    packet = contracts.SignalDecisionPacket(
        contract_type="signal_decision",
        contract_version="1.1",
        schema_version="1",
        generated_at=1,
        producer="test",
        model_generation="legacy",
        source_revision_status="VERIFIED_GIT_REVISION",
        decision_id="decision-1",
        signal_id="signal-1",
        symbol="BTCUSDT",
        signal_class="STRICT",
        strategy_profile="strict_v1",
        lifecycle_state="TRIGGERED",
        decision_status={"primary": "CONFIRMED"},
        score_version="legacy_evidence_score_v2",
        decision_contract_hash="a" * 64,
        analysis_observed_at=1,
        reference_observed_at=1,
        eligibility_gates={"fresh": True},
        evidence_quality={
            "coverage_pct": 100,
            "completeness_status": "COMPLETE",
            "analysis_observed_at": 1,
            "analysis_age_seconds": 0,
            "reference_observed_at": 1,
            "reference_age_seconds": 0,
            "timestamp_alignment_status": "ALIGNED",
        },
        predictive_evidence_score=None,
        final_signal_score=88.0,
        calibrated_probability=None,
        anti_chase_risk="NOT_EVALUATED",
        execution_risk="SUITABLE",
        execution_plan_id="plan-1",
        reason_codes=["STRICT_GATES_PASS"],
    )
    assert packet.calibrated_probability is None
    assert packet.execution_mode.value == "SIGNAL_ONLY"


def test_signal_decision_packet_rejects_fake_probability():
    contracts = _contracts()
    with pytest.raises(ValidationError):
        contracts.SignalDecisionPacket.model_validate({
            **_valid_signal_packet(contracts),
            "calibrated_probability": 1.01,
        })


def test_execution_plan_is_lbank_isolated_only():
    contracts = _contracts()
    plan = contracts.ExecutionPlan(**_valid_execution_plan())
    assert plan.venue == "LBANK"
    assert plan.margin_mode.value == "ISOLATED"
    assert plan.cross_margin_allowed is False
    assert plan.auto_add_margin is False


def test_execution_plan_rejects_cross_margin_or_out_of_range_system_leverage():
    contracts = _contracts()
    with pytest.raises(ValidationError):
        contracts.ExecutionPlan(**{**_valid_execution_plan(), "cross_margin_allowed": True})
    with pytest.raises(ValidationError):
        contracts.ExecutionPlan(**{**_valid_execution_plan(), "system_leverage": 21})
```

Test helpers `_valid_signal_packet()` and `_valid_execution_plan()` must return the exact required fields and use positive finite prices.

- [ ] **Step 2: Run focused tests and verify RED**

Expected: missing packet classes/fields.

- [ ] **Step 3: Implement minimal packet models**

Implementation rules:

```python
Score100 = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
PositiveFinite = Annotated[float, Field(gt=0, allow_inf_nan=False)]
NonNegativeFinite = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
```

`CommonContractEnvelope.observational_only` is `Literal[True] = True`.

`SignalDecisionPacket.execution_mode` is `Literal[ExecutionMode.SIGNAL_ONLY]`/equivalent fail-closed field with default `SIGNAL_ONLY`.

`ExecutionPlan.venue` is fixed to `LBANK`; `margin_mode` fixed to `ISOLATED`; `cross_margin_allowed` and `auto_add_margin` are literal false. `system_leverage` is `[3,20]`; `raw_safe_leverage` remains a separate positive finite value and may be below 3.

When `levels_available=false`, `unavailable_reason` is required; when true, required executable levels must be present. Add a model validator for this relationship.

- [ ] **Step 4: Verify GREEN**

Run the focused tests and then:

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests/test_foundation_tooling.py backend/tests/test_golden_model_regression.py
```

Expected: focused tests pass and Wave 0 regression tests remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/src/waterfallhunter/core/contracts.py backend/tests/test_canonical_contracts.py
git commit -m "feat: add signal and execution contracts"
```

---

### Task 3: Position and notification contracts

**Files:**
- Modify: `backend/src/waterfallhunter/core/contracts.py`
- Modify: `backend/tests/test_canonical_contracts.py`

**Interfaces:**
- Produces: `PositionState`, `PositionAmendment`, `NotificationEvent`.
- Preserves separate lifecycle/execution/thesis state spaces.

- [ ] **Step 1: Add failing tests**

```python
def test_position_state_keeps_execution_and_thesis_states_separate():
    contracts = _contracts()
    state = contracts.PositionState(
        position_id="position-1",
        signal_id="signal-1",
        execution_state="OPEN",
        thesis_state="CAUTION",
        original_execution_plan_id="plan-1",
        isolated_margin_initial=20,
        isolated_margin_current=20,
        notional=120,
        entry_price=1.0,
        realized_pnl=0,
        unrealized_pnl=-2,
        fees=0.1,
        funding=0,
        opened_at=1,
        last_reassessed_at=1,
    )
    assert state.execution_state.value == "OPEN"
    assert state.thesis_state.value == "CAUTION"
    assert not hasattr(state, "lifecycle_state")


def test_notification_event_requires_sha256_material_hash():
    contracts = _contracts()
    with pytest.raises(ValidationError):
        contracts.NotificationEvent(
            contract_type="notification_event",
            contract_version="1.0",
            schema_version="1",
            generated_at=1,
            producer="test",
            model_generation="legacy",
            source_revision_status="VERIFIED_GIT_REVISION",
            event_id="event-1",
            event_type="SIGNAL_CONFIRMED",
            aggregate_type="signal",
            aggregate_id="signal-1",
            symbol="BTCUSDT",
            signal_class="STRICT",
            lifecycle_state="TRIGGERED",
            decision_status={"primary": "CONFIRMED"},
            material_state_hash="not-a-hash",
            idempotency_key="signal-confirmed:signal-1",
            priority=100,
            payload_contract_version="1",
            payload={},
            created_at=1,
        )
```

- [ ] **Step 2: Verify RED**

Run focused tests and confirm failures are due to absent classes.

- [ ] **Step 3: Implement minimal contracts**

`PositionState` must always carry `margin_mode=ISOLATED`; PnL values are finite signed numbers; fees/funding and margin/notional are finite and non-negative where appropriate.

`PositionAmendment` is append-only vocabulary only: identity, position ID, action, reason codes, created time, optional proposed SL/TP levels, and source context version. It does not mutate a `PositionState` in this Wave.

`NotificationEvent` validates material hash as lowercase SHA-256, requires non-empty idempotency key, carries stable decision/lifecycle projection, and does not contain Telegram delivery state or secrets.

- [ ] **Step 4: Verify GREEN**

Run focused tests plus Wave 0 Golden/model regression tests.

- [ ] **Step 5: Commit**

```bash
git add backend/src/waterfallhunter/core/contracts.py backend/tests/test_canonical_contracts.py
git commit -m "feat: add position and notification contracts"
```

---

### Task 4: Contract determinism and full regression gate

**Files:**
- Modify: `backend/tests/test_canonical_contracts.py`
- Modify: `docs/program/EXECUTION_LEDGER.md`

**Interfaces:**
- Uses: `waterfallhunter.core.canonical_json.canonical_bytes`/existing Wave 0 canonical hashing surface.
- Produces: evidence that semantically identical decision statuses canonicalize to identical bytes/hashes.

- [ ] **Step 1: Add deterministic serialization test**

```python
def test_decision_status_semantic_order_has_identical_canonical_bytes():
    contracts = _contracts()
    from waterfallhunter.core.canonical_json import canonical_bytes

    left = contracts.DecisionStatus(
        primary="CONFIRMED",
        qualifiers=["STALE_REFERENCE", "AI_CAUTION"],
    )
    right = contracts.DecisionStatus(
        primary="CONFIRMED",
        qualifiers=["AI_CAUTION", "STALE_REFERENCE", "AI_CAUTION"],
    )
    assert canonical_bytes(left.model_dump(mode="json")) == canonical_bytes(right.model_dump(mode="json"))
```

- [ ] **Step 2: Verify test fails before canonicalization is correct, then passes after the minimal fix**

Do not change unrelated contract behavior.

- [ ] **Step 3: Run full backend regression**

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests
```

Expected: zero failures; pre-existing deprecation warnings may remain documented.

- [ ] **Step 4: Run canonical corpus replay**

Use the repository's Wave 0 canonical corpus command/test. Expected: exact deterministic fixture semantics unchanged.

- [ ] **Step 5: Run dependency/security/static checks available to the branch**

CI must include backend, frontend, dependency-audit, container-validation, repository-hygiene plus configured Sonar/CodeQL checks. No suppression is accepted for newly introduced findings.

- [ ] **Step 6: Update execution ledger**

Record:

- branch/head SHA
- `SEMANTIC_INFRA` classification
- tests and check results
- Golden Corpus diff = none, or exact blocker details
- no consumer cutover
- no Production mutation

- [ ] **Step 7: Open a stacked draft PR**

Base the PR on `program/wave0-foundations-v1`, not `main`, so reviewers see only Wave 1A changes. Do not merge.
