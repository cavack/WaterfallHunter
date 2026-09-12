# WaterfallHunter Wave 1 Canonical Contracts Design

**Status:** Approved design subset derived from Final Design v6.1

**Baseline:** `497ed954b09c158fcbf02df47a0f196eda5d61ab` (`program/wave0-foundations-v1`)

**Model impact classification:** `SEMANTIC_INFRA`

**Production impact:** none. This design does not authorize Production backup, migration, DB write, restart, deployment, Telegram delivery, live trading, or merge to `main`.

## Goal

Introduce versioned, typed canonical domain contracts without changing current scoring, thresholds, lifecycle behavior, trigger behavior, ranking behavior, execution levels, or signal-only safety. Consumers are migrated in later workstreams only after the contracts are independently validated.

## Source and safety invariants

- Canonical repository: `cavack/wfh`.
- GitHub is code truth; Production runtime is runtime truth.
- `LIVE_TRADING_ENABLED=false` remains a hard invariant.
- Execution semantics remain `SIGNAL_ONLY` and `ISOLATED`.
- No live create/cancel/modify order path may be added.
- Missing canonical metadata or evidence must fail closed in later consumer cutovers; this Wave does not add silent fallbacks.
- Current ScoreV2 and lifecycle semantics remain unchanged by this workstream.

## Common contract envelope

Canonical domain packets carry:

- `contract_type`
- `contract_version`
- `schema_version`
- `generated_at`
- `producer`
- `model_generation`
- `source_revision_status`
- `observational_only`

`source_revision_status` is exactly one of:

- `VERIFIED_GIT_REVISION`
- `LEGACY_RUNTIME_UNVERIFIED_REVISION`

The legacy runtime fingerprint is never represented as a Git SHA.

## Three independent signal axes

### Signal class

Immutable cohort identity:

- `STRICT`
- `EXPERIMENTAL`

No AI, execution, or lifecycle result may mutate signal class.

### Lifecycle state

- `WATCH`
- `FUEL_RICH`
- `PRE_TRIGGER`
- `ARMED`
- `TRIGGERED`
- `LATE`
- `EXHAUSTED`

Target invariants for the future Lifecycle V2 workstream:

- `ARMED`: setup ready while lower-timeframe trigger is false.
- `TRIGGERED`: lower-timeframe trigger is true.

This Wave defines the vocabulary only. It does not change the current lifecycle engine.

### Decision status

Primary values:

- `OBSERVING`
- `NOT_TRADE_ELIGIBLE`
- `CONFIRMED`
- `INVALIDATED`
- `UNAVAILABLE`

Qualifiers:

- `AI_CAUTION`
- `EXECUTION_LEVELS_UNAVAILABLE`
- `STALE_ANALYSIS`
- `STALE_REFERENCE`
- `LATE_ENTRY_BLOCKED`
- `ANTI_CHASE_BLOCKED`
- `EXECUTION_DEGRADED`
- `INSUFFICIENT_EVIDENCE`

`CONFIRMED_AI_CAUTION` is a presentation projection of `signal_class=STRICT`, `lifecycle_state=TRIGGERED`, `decision_status.primary=CONFIRMED`, and qualifier `AI_CAUTION`. It is not a third signal class.

## Core packet contracts

### SignalDecisionPacket v1.1

Required identity and provenance:

- `decision_id`
- `signal_id`
- `symbol`
- `signal_class`
- `strategy_profile`
- `lifecycle_state`
- `decision_status`
- `score_version`
- `model_generation`
- `decision_contract_hash`
- `analysis_observed_at`
- `reference_observed_at`
- `eligibility_gates`
- `evidence_quality`
- `predictive_evidence_score`
- `final_signal_score`
- `calibrated_probability`
- `anti_chase_risk`
- `execution_risk`
- `execution_plan_id`
- `reason_codes`
- `execution_mode=SIGNAL_ONLY`

`final_signal_score` is not probability. `calibrated_probability` may be unavailable/`None`; no replacement probability is invented in this Wave.

### ExecutionPlan v1.1

- `execution_plan_id`
- `signal_id`
- `venue=LBANK`
- `contract_identity`
- `margin_mode=ISOLATED`
- `cross_margin_allowed=false`
- `auto_add_margin=false`
- `entry_primary`
- `entry_secondary`
- `tp1`
- `tp2`
- `stop_loss`
- `raw_safe_leverage`
- `system_leverage`
- `risk_label`
- `spread`
- `entry_slippage`
- `exit_slippage`
- `depth`
- gross/net TP1, TP2, and SL PnL estimates
- fee/funding model versions
- `levels_available`
- `unavailable_reason`

No other exchange may supply user-facing executable levels under this contract.

### EvidenceQualityPacket

- coverage/completeness
- analysis observation time and age
- reference observation time and age
- timestamp alignment
- candle/derivatives/microstructure/execution/cross-exchange coverage
- missing sources
- stale sources
- uncertainty reasons

Evidence quality is separate from directional/predictive scoring.

### PositionState

Three state spaces remain independent:

1. signal lifecycle
2. position execution state: `OPEN`, `PARTIALLY_CLOSED`, `CLOSED`, `LIQUIDATED`
3. thesis state: `VALID`, `CAUTION`, `HIGH_RISK`, `THESIS_INVALIDATED`

`THESIS_INVALIDATED` is not a signal lifecycle state.

### PositionAmendment

Later changes to SL/TP/action are append-only amendments. Original Entry/TP1/TP2/SL are not overwritten.

### NotificationEvent

Carries stable event identity, aggregate identity, signal/lifecycle/decision projection, material-state hash, idempotency key, priority, payload contract version, payload, and creation time. Delivery state is a separate Telegram workstream.

## Validation rules for Wave 1A

- Unknown enum values are rejected.
- Signal class is restricted to `STRICT|EXPERIMENTAL`.
- Margin mode is restricted to `ISOLATED`.
- `cross_margin_allowed` and `auto_add_margin` must remain false.
- `execution_mode` must remain `SIGNAL_ONLY`.
- Scores use finite numeric values and declared ranges.
- Probabilities, when present, are finite values in `[0,1]`; absence remains explicit.
- Prices and leverage inputs reject NaN/Inf.
- System leverage is constrained to the approved `3x..20x` range; raw safe leverage may be below 3x and is preserved separately.
- Qualifiers are canonicalized deterministically so semantically identical statuses serialize consistently.
- Hash-looking fields that are declared SHA-256 digests must be validated as lowercase 64-character hex.

## Non-goals

This Wave does not:

- migrate any Production database
- create `signal_metadata`
- backfill legacy rows
- change current StageLifecycleStore behavior
- change ScoreV2 weights or names in active consumers
- remove `tp_24h_probability` from current consumers
- change FinalRanking
- change Telegram delivery
- change Dashboard behavior
- change LBank execution calculations

Those are dependency-ordered later workstreams.

## Acceptance gate

Wave 1A passes only when:

- typed contract tests pass
- invalid values fail closed
- current model/regression tests remain unchanged
- Golden deterministic fixture semantics remain unchanged
- CI/security/static checks are green
- no consumer cutover or model-semantic diff is introduced
