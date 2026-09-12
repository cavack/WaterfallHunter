# Wave 1C — Unified Signal Metadata and Cohort Purity Design

Date: 2026-08-20
Status: DESIGN APPROVED IN CHAT / CLEAN SPEC PR MERGE APPROVAL PENDING
Canonical repository: `cavack/wfh`
Publication base: merged Wave 1B2 `main` at `e5a133718959187b694ef79aa550801228188231`
Authorized Wave 1B2 source head: `6dc20b7edf3a880e2277f7b0dd0d429afb240ca6`
Historical design baseline `main`: `652f99446ed523c0a602798dde4457bab7983373`; source facts were revalidated against the publication base.
Model impact classification: `SEMANTIC_INFRA` with persistence/query semantics changes, but no ScoreV2/lifecycle/ranking threshold change.

## 1. Purpose

Wave 1C creates one authoritative signal lineage/metadata layer so STRICT and EXPERIMENTAL signals cannot silently mix in outcome, reporting, calibration, dashboard, or later model-development datasets.

The design is intentionally fail-closed:

- no signal metadata duplication in ledger columns;
- no silent fallback to `trigger_metrics_json`;
- no implicit default to STRICT;
- no third `signal_class` for unresolved legacy rows;
- future signal persistence is atomic across catalogue transition, ledger row, and metadata row;
- consumers read canonical signal identity/semantics through `canonical_signal_view`.

This work is required before any strict calibration, probability promotion, or outcome-based model claims.

## 2. Current source facts that drive the design

The current development stack already provides:

- canonical domain enum `SignalClass = STRICT | EXPERIMENTAL`;
- `SignalDecisionPacket` v1.1 carrying `signal_class`, `strategy_profile`, `score_version`, `model_generation`, `decision_contract_hash`, `analysis_observed_at`, and `reference_observed_at`;
- first-party SQLite migration runner and versioned schema ownership;
- runtime stores converted to verify-only schema consumers;
- startup managed-schema verification before workers are scheduled.

Current signal persistence still writes only the immutable ledger row. `LBankSignalLedger.persist_trigger()` owns the catalogue CAS and ledger insert transaction, but it does not write first-class metadata.

Current experimental trigger generation explicitly marks:

- `strategy_profile = experimental_pretrigger_v1`;
- `calibration_status = pending`.

Current deterministic score versions are `score_v2` for STRICT and
`score_v2_watch_v1` for EXPERIMENTAL.

Current outcome settlement and execution outcome reporting query `lbank_signal_ledger` directly and do not enforce cohort separation.

Historical Production audit evidence from 2026-08-18 found 1,031 ledger rows whose `trigger_metrics_json.strategy_profile` was `experimental_pretrigger_v1`; this is evidence for a future legacy classification operation, not authorization to mutate Production in this wave.

## 3. Scope

Wave 1C includes:

1. schema contract for authoritative `signal_metadata`;
2. immutable metadata persistence for future signals;
3. `canonical_signal_view` as the authoritative signal read interface;
4. explicit future STRICT/EXPERIMENTAL lineage policy;
5. deterministic legacy classification tooling for disposable/restored/dev databases;
6. fail-closed handling for unresolved/conflicting legacy rows;
7. cohort-aware outcome settlement and reporting defaults;
8. startup/readiness metadata-completeness verification after the cutover is active;
9. Golden Corpus/model-preservation checks proving no unintended score/lifecycle/ranking behavior change.

## 4. Non-scope

Wave 1C does not:

- run a Production backup;
- write to the Production database;
- execute a Production migration or legacy backfill;
- deploy or restart Production services;
- merge any PR to `main` without separate approval;
- change ScoreV2 weights, thresholds, gates, or score version;
- redesign lifecycle states;
- remove or rename `tp_24h_probability` yet;
- implement probability calibration;
- change Entry/TP/SL/leverage behavior;
- send Telegram messages;
- enable live trading.

`BACKUP_EXECUTION_APPROVAL`, `MIGRATION_APPROVAL`, `DEPLOYMENT_APPROVAL`, and `MERGE_APPROVAL` remain independently closed.

## 5. Canonical metadata model

### 5.1 `signal_metadata`

Schema version target: runtime schema version 3.

Conceptual table:

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
    CHECK(analysis_observed_at >= 0),
    CHECK(reference_observed_at IS NULL OR reference_observed_at >= 0),
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

Exact SQL remains subject to migration/schema-contract implementation details, but the domain fields and invariants above are binding.

### 5.2 Immutability

`signal_metadata` is append-only per `signal_id`:

- one metadata row maximum per signal;
- UPDATE forbidden by trigger;
- DELETE forbidden by trigger;
- signal class/profile/version/hash/timestamps cannot be rewritten after persistence.

The only supported correction path for a bad historical classification is an explicit new classification program/design, not in-place metadata mutation.

### 5.3 Contract versions and lineage constants

Initial metadata contract version:

`signal_metadata_v1`

Initial future strategy profiles:

- STRICT: `strict_score_v2`
- EXPERIMENTAL: existing `experimental_pretrigger_v1`

Initial model generation label:

`waterfall_signal_model_v1`

These labels describe lineage only. They do not change ScoreV2 behavior.

`score_version` is taken from the deterministic scoring producer and is bound to
the complete lineage tuple:

- `STRICT / strict_score_v2 / score_v2`;
- `EXPERIMENTAL / experimental_pretrigger_v1 / score_v2_watch_v1`.

## 6. Future signal persistence boundary

Future signal persistence becomes one SQLite transaction:

1. validate an explicit `SignalMetadataInput`/equivalent object before DB mutation;
2. `BEGIN`;
3. compare-and-set `lbank_catalog` from the expected state to `TRIGGERED`;
4. insert immutable `lbank_signal_ledger` row;
5. obtain `signal_id`;
6. insert exactly one `signal_metadata` row for that `signal_id`;
7. persist any deterministic downstream event only if it is intentionally part of the same consistency boundary;
8. `COMMIT`.

If metadata validation or insertion fails, the entire transaction rolls back, including the catalogue transition and ledger insert.

A future signal without canonical metadata must not exist.

## 7. Metadata producer contract

Persistence must receive explicit lineage. It must not infer STRICT from the absence of an experimental marker.

The caller provides:

- `signal_class`;
- `strategy_profile`;
- `score_version`;
- `model_generation`;
- `decision_contract_hash`;
- `analysis_observed_at`;
- `reference_observed_at`;
- `classification_method`.

For the current deterministic pipeline:

- experimental eligibility emits `EXPERIMENTAL / experimental_pretrigger_v1 / score_v2_watch_v1`;
- strict eligibility emits `STRICT / strict_score_v2 / score_v2`;
- any path unable to prove one of those combinations fails before persistence.

Signal classification is lineage, not AI/execution/risk state. AI caution, execution availability, anti-chase risk, or later outcomes cannot change `signal_class`.

## 8. Decision provenance hash

`decision_contract_hash` is SHA-256 over RFC8785/JCS canonical bytes for the deterministic decision contract used for that signal.

Requirements:

- no plain `json.dumps(sort_keys=True)` substitute;
- no NaN/Inf;
- no volatile fields unless the contract explicitly defines them as semantic;
- same decision contract yields the same hash;
- the stored hash is lowercase 64-character hex.

This hash is lineage/provenance and must not be recomputed later from the current application defaults to reinterpret a historical row.

## 9. `canonical_signal_view`

The authoritative signal read interface is a database view joining ledger and metadata with INNER JOIN semantics.

Conceptual shape:

```sql
CREATE VIEW canonical_signal_view AS
SELECT
    s.id AS signal_id,
    s.symbol,
    s.triggered_at,
    s.state_before,
    s.score,
    s.entry_price,
    s.stop_loss,
    s.take_profit_1,
    s.take_profit_2,
    s.execution_status,
    s.volume_gate_passed,
    s.proxy_execution_disagreement,
    m.signal_class,
    m.strategy_profile,
    m.score_version,
    m.model_generation,
    m.decision_contract_hash,
    m.analysis_observed_at,
    m.reference_observed_at,
    m.metadata_contract_version,
    m.classification_method,
    m.classification_evidence_hash
FROM lbank_signal_ledger AS s
INNER JOIN signal_metadata AS m
    ON m.signal_id = s.id;
```

The final migration and schema manifest must bind one complete canonical view
definition: explicit selected expressions, exact aliases, the exact INNER JOIN,
and no additional predicate or clause. Fragment-only verification is
insufficient because an added `WHERE`, changed expression, or omitted cohort
could otherwise drift consumer semantics while preserving required fragments.

Forbidden consumer behavior after cutover:

- `LEFT JOIN signal_metadata` followed by fallback;
- reading `trigger_metrics_json.strategy_profile` to decide cohort;
- hard-coded `EXPERIMENTAL` or `STRICT` fallback;
- interpreting missing metadata using current application defaults.

Missing metadata means the row is not canonical.

## 10. Legacy classification

### 10.1 Principle

Legacy ledger rows are immutable and are never updated.

Legacy classification writes only new `signal_metadata` rows, and only when deterministic evidence supports the classification.

### 10.2 Classification policy v1

Supported classification methods:

- `LEGACY_PROFILE_EXACT_MATCH`
- `FUTURE_PIPELINE_EXPLICIT`

For legacy rows, `LEGACY_PROFILE_EXACT_MATCH` is allowed only when the persisted historical evidence contains an exact recognized strategy profile and all required metadata needed by the metadata contract can be reconstructed deterministically from frozen historical evidence/provenance available to the tool.

An exact `experimental_pretrigger_v1` profile classifies as EXPERIMENTAL.

No legacy row is classified STRICT merely because the experimental profile is absent.

### 10.3 Unresolved/conflicting legacy rows

`UNRESOLVED` is not a third `SignalClass`.

If required evidence is missing, contradictory, ambiguous, or cannot produce the mandatory metadata contract:

- no canonical `signal_metadata` row is inserted for that signal;
- the classification tool reports that signal as unresolved;
- the row is excluded from `canonical_signal_view`;
- strict/report/calibration consumers cannot see it through the canonical interface.

The classification report is append-only audit evidence generated by the tool/run, not a fallback metadata store.

### 10.4 Production legacy evidence

The 2026-08-18 audit found all 1,031 then-current ledger rows carrying `experimental_pretrigger_v1` in JSON. This remains historical evidence only.

Before any Production classification operation, a fresh read-only verification must reconfirm:

- ledger count and IDs;
- exact profile evidence;
- required provenance fields;
- conflict count;
- expected metadata row count;
- classification evidence hash.

Any Production write still requires the separate backup/restore and migration approval gates.

## 11. Classification evidence hashing

Each legacy classification run computes a deterministic evidence envelope per signal containing only the persisted fields used to justify classification.

The envelope is RFC8785/JCS serialized and SHA-256 hashed into `classification_evidence_hash`.

The hash proves what evidence justified the immutable metadata row; it does not prove that the deployed legacy runtime corresponded to a verified Git SHA.

Legacy source revision status remains `LEGACY_RUNTIME_UNVERIFIED_REVISION` wherever that provenance distinction is represented.

## 12. Consumer cutover

### 12.1 Outcome settlement

`LBankSignalOutcomeStore.pending_signals()` stops selecting directly from `lbank_signal_ledger` and reads canonical signal rows.

Settlement is allowed for both STRICT and EXPERIMENTAL signals because outcomes are research evidence, but the returned signal always carries explicit `signal_class` and `strategy_profile`.

Missing metadata never falls back to JSON.

### 12.2 Reports and calibration defaults

Production-facing outcome/execution/calibration reports default to:

`signal_class = STRICT`

Research callers may request EXPERIMENTAL explicitly through a typed cohort parameter/policy.

A report combining cohorts must be explicitly labeled mixed/research-only and cannot be used by default calibration/promotion paths.

### 12.3 Dashboard/API preparation

Wave 1C does not redesign the dashboard, but backend canonical read surfaces must carry cohort metadata so Wave 2 typed API/UI work can separate STRICT and EXPERIMENTAL without reinterpreting legacy JSON.

## 13. Runtime completeness gate

After the P1-C cutover is enabled, application startup adds a read-only canonical metadata completeness check in addition to managed-schema verification.

Rules:

- zero ledger rows / zero metadata rows: PASS;
- every ledger signal has exactly one valid metadata row: PASS;
- ledger row without metadata: FAIL CLOSED;
- metadata row without ledger row: FAIL CLOSED / FK/schema integrity failure;
- invalid signal class/profile/version/hash: FAIL CLOSED;
- duplicate metadata impossible by PK, otherwise schema invalid.

This gate performs no migration and no repair.

It prevents deploying code that expects canonical metadata onto an incompletely classified database.

## 14. Migration and rollout sequence

Development/disposable sequence:

1. migrate schema v2 → v3;
2. verify empty/new database behavior;
3. classify frozen legacy fixture rows into `signal_metadata`;
4. verify unresolved rows remain non-canonical;
5. verify `canonical_signal_view` contents;
6. verify consumer cohort filters;
7. verify future atomic persistence;
8. run full regressions and Golden Corpus.

Future Production sequence is intentionally separate:

1. fresh Production baseline/read-only classification preview;
2. separate `BACKUP_EXECUTION_APPROVAL`;
3. verified current backup + isolated restore + rollback readiness;
4. separate `MIGRATION_APPROVAL`;
5. schema migration and explicit legacy classification operation;
6. metadata completeness/readiness verification;
7. separate `DEPLOYMENT_APPROVAL`;
8. candidate deployment/shadow verification;
9. separate `MERGE_APPROVAL` when appropriate.

This specification authorizes none of those Production steps.

## 15. Error handling and fail-closed behavior

Typed/structured failure reasons should distinguish at least:

- `SIGNAL_METADATA_INCOMPLETE`
- `SIGNAL_METADATA_INVALID`
- `SIGNAL_METADATA_CONFLICT`
- `SIGNAL_METADATA_PERSISTENCE_FAILED`
- `LEGACY_CLASSIFICATION_UNRESOLVED`
- `LEGACY_CLASSIFICATION_CONFLICT`

Critical persistence failures return failure to the caller and roll back the transaction.

Read/report paths do not silently return a row under a guessed cohort.

## 16. Model-preservation contract

P1-C must not change:

- ScoreV2 weights;
- ScoreV2 gates;
- `armed_threshold`;
- `triggered_threshold`;
- experimental threshold;
- lifecycle transition logic;
- entry/take-profit/stop calculations;
- execution suitability calculations;
- anti-chase behavior;
- ranking weights;
- Telegram delivery policy.

Expected semantic differences are limited to persistence/query lineage:

- future persisted signals now require metadata;
- missing-metadata legacy rows become non-canonical;
- strict-default reports exclude EXPERIMENTAL rows;
- research callers can request EXPERIMENTAL explicitly.

Any unexpected Golden Corpus difference in score, eligibility, lifecycle, reason codes, execution plan, ordering, or signal levels is a blocker.

## 17. Test and acceptance plan

Required tests before development-side certification:

1. schema v2 → v3 migration;
2. clean v3 install;
3. metadata table schema, CHECK constraints, FK, indexes/triggers, immutability;
4. canonical view definition uses INNER JOIN and explicit fields;
5. missing metadata is not visible through canonical view;
6. no default-to-STRICT behavior anywhere in canonical readers;
7. future STRICT persistence writes ledger + metadata atomically;
8. future EXPERIMENTAL persistence writes ledger + metadata atomically;
9. metadata insertion failure rolls back catalogue CAS and ledger insert;
10. invalid/unknown lineage is rejected before persistence;
11. decision-contract JCS hash determinism;
12. exact experimental legacy profile classification;
13. unresolved/conflicting legacy evidence produces no metadata row;
14. classification evidence hashes are deterministic;
15. repeated legacy classification is idempotent or fail-closed without rewriting metadata;
16. outcome settlement carries explicit cohort;
17. production report default includes only STRICT;
18. explicit EXPERIMENTAL research filter works;
19. mixed cohort is never the default calibration/report path;
20. startup metadata-completeness gate passes complete DB and fails incomplete DB;
21. repository search/guard rejects canonical cohort fallback to legacy JSON/defaults;
22. Golden Corpus non-model semantic equality;
23. deterministic repeated replay equality;
24. full backend suite;
25. frontend typecheck/build;
26. dependency audit;
27. repository hygiene/secret scan;
28. exact production artifact family container test;
29. Sonar/static security review;
30. independent CodeRabbit/reviewer pass with no unresolved actionable findings.

## 18. Observability

Wave 1C should expose bounded metrics suitable for later alerting, without high-cardinality signal IDs:

- canonical metadata completeness ratio;
- canonical signal count by `signal_class`;
- unresolved legacy classification count per classification run;
- metadata persistence failure count;
- startup metadata-gate pass/fail state.

No raw decision contract, secret, full JSON evidence, or signal ID is used as a Prometheus label.

## 19. Security and data-integrity considerations

- all SQL values are parameterized;
- no schema identifiers come from user-controlled input;
- metadata strings are bounded/validated before persistence;
- classification tooling is deterministic and non-network-dependent for a frozen input database;
- legacy JSON is parsed only as classification evidence, never trusted as a canonical fallback after cutover;
- no secrets are stored in metadata or classification evidence hashes;
- classification reports must not dump sensitive runtime configuration.

## 20. Documentation impact

Implementation should update, as applicable:

- `docs/program/EXECUTION_LEDGER.md`;
- `docs/program/DEPENDENCY_GRAPH.md`;
- `docs/program/ROADMAP_RECONCILIATION.md`;
- database/schema documentation introduced by the implementation plan;
- signal-contract/cohort documentation if present or introduced.

## 21. Development-side certification state

Allowed terminal state for this wave before merge approval:

`MERGE_READY_PENDING_MERGE_APPROVAL`

or, if non-blocking limitations remain:

`CERTIFIED_WITH_KNOWN_LIMITATIONS`

No development-side certification authorizes Production operations or merge.

## 22. Explicit invariants

1. Canonical code truth remains `cavack/wfh`.
2. `LIVE_TRADING_ENABLED=false` remains invariant.
3. Signal class has exactly two values: STRICT and EXPERIMENTAL.
4. Unresolved legacy classification is not a signal class.
5. Future persisted signals always have exactly one metadata row.
6. Metadata is immutable.
7. `canonical_signal_view` is INNER JOIN/fail-closed.
8. Missing metadata never defaults to STRICT.
9. Legacy JSON is evidence only, never canonical fallback.
10. Default production reporting/calibration is STRICT-only.
11. Experimental data remains available for explicitly labeled research.
12. This wave does not change deterministic model behavior.
13. Migration runner remains the only schema owner.
14. Runtime stores remain verify-only.
15. Production backup, migration, deployment, restart, Telegram send, live trading, and merge remain unauthorized without their independent gates.
