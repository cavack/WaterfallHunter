# WaterfallHunter Development Execution Ledger

Baseline: `652f99446ed523c0a602798dde4457bab7983373`

Wave 0 branch: `program/wave0-foundations-v1`

Wave 1A branch: `feat/wave1a-canonical-contracts-v1`

Wave 1B1 branch: `feat/wave1b-migration-readiness-v1`

Worktree: isolated development checkout; Production source and state are excluded.

## Program gates

| Gate | Status | Evidence |
| --- | --- | --- |
| Source authority read | PASSED | Requirements v6, Final Design v6.1, Production Baseline Audit, Master Orchestrator v7 read in full |
| GitHub main freshness | PASSED | Current `main` equalled the design baseline at the implementation-preparation freshness gate |
| Baseline reconciliation | NO_DESIGN_IMPACT | No commits after the design baseline at the freshness gate |
| Backend baseline | PASSED | 340 tests, 9 pre-existing deprecation warnings |
| Frontend baseline | PASSED | Node 20 typecheck and production build |
| Dependency baseline | PRE_EXISTING_SECURITY_FINDING_FIXED | Baseline required four exceptions; updated lock has npm: 0 and Python: 0 known vulnerabilities without exceptions |
| Compose config | PASSED | Configuration parses without Production mutation |
| Production image build | CI_PASSED | PR #22 built revision-labelled backend/frontend/watchdog artifacts and tested image-contained backend code without Production mutation |
| Static/security analysis | PASSED | Wave 0 static/security checks passed. Wave 1A and Wave 1B1 latest Sonar quality gates report 0 new issues and 0 security hotspots; substantive CodeRabbit findings were regression-tested and fixed, all inline threads are resolved, and the latest completed W1B1 incremental source review generated no actionable comments. |

## Workstreams

| ID | Workstream | Dependency | Model impact | Status |
| --- | --- | --- | --- | --- |
| W0-A | Runtime Fingerprint | baseline | NO_MODEL_IMPACT | SOURCE_GATE_PASSED; legacy capture reserved |
| W0-B | Golden Regression Corpus tooling | baseline, canonical hashing | NO_MODEL_IMPACT | CANONICAL_FIXTURES_PASSED; legacy evidence blocked |
| W0-C | Artifact/deployment provenance | W0-A contract | NO_MODEL_IMPACT | TOOLING_PASSED; running digest reserved |
| W0-D | CI/runtime artifact parity | W0-C chain | NO_MODEL_IMPACT | CI_PASSED |
| W1-A | Canonical domain contracts | Wave 0 | SEMANTIC_INFRA | MERGE_READY_PENDING_MERGE_APPROVAL |
| W1-B1 | Migration runner + DB readiness foundation | W1-A | SEMANTIC_INFRA | MERGE_READY_PENDING_MERGE_APPROVAL |
| W1-B2 | Runtime schema-ownership cutover | W1-B1 | SEMANTIC_INFRA | NOT_STARTED |
| W1-C | Unified signal metadata/cohort purity | W1-B2 | SEMANTIC_INFRA | BLOCKED_ON_W1_B2 |
| W1-D | Probability/freshness/strict filtering | W1-C | SEMANTIC_INFRA | NOT_STARTED |
| W2 | Typed API, dashboard, Telegram, observability | Wave 1 | mixed | NOT_STARTED |
| W3 | Execution/risk/portfolio/backtest | Wave 2 | MODEL_AFFECTING where noted | NOT_STARTED |
| W4 | Observational intelligence | Wave 3 | OBSERVATIONAL_MODEL_CHANGE | NOT_STARTED |
| W5 | Scientific promotion | clean strict dataset | MODEL_AFFECTING | BLOCKED |
| W6 | Certification | all prior waves | mixed | NOT_STARTED |

## Controller rulings

1. Existing ScoreV2 and balanced-signal plans remain historical implementation evidence. Their real-data-only, signal-only, conservative same-bar, and validator-ownership rules remain binding.
2. Their old `ARMED`-requires-trigger semantics are superseded only in Lifecycle V2 shadow work; current model behavior is preserved until Golden Corpus and OOS gates pass.
3. No Production DB, backup, migration, deployment, restart, Telegram message, live trading, or merge is authorized.
4. The canonical fixture corpus is not a substitute for real-data replay, OOS evidence, or the legacy-runtime corpus.
5. W1-A is contract vocabulary only: no active consumer imports/cutover, no ScoreV2/lifecycle/ranking/execution semantic change.
6. A green quality gate does not by itself clear static-analysis findings; substantive findings must be inspected, fixed, or explicitly adjudicated before `MERGE_READY`.
7. W1-B is split into B1 foundation and B2 schema-ownership cutover. W1-C cannot begin until B2 is independently verified.
8. Branch synchronization inside the development stack does not constitute `MERGE_APPROVAL` and must not merge any PR or update `main`.

## Wave 0 review evidence

- Draft PR: #22.
- Backend: 346 passed; 9 pre-existing deprecation warnings.
- Frontend: Node 26 typecheck and production build passed.
- Dependency audit: backend, watchdog, and npm passed with zero known findings and no audit exceptions.
- Artifact parity: Python 3.13 and Node 26 declarations match digest-only image inputs; container validation passed.
- Independent analysis: SonarCloud initially found seven issues; all were fixed without suppressions and the rerun quality gate passed. CodeQL reported no new alerts.
- Model impact: no scoring, threshold, lifecycle, trigger, or execution-level changes; canonical fixture replay remained exact.
- Remaining hard gate: legacy-runtime evidence capture requires separate Production authorization before model-affecting work.

## Wave 1A canonical-contract evidence

- Draft stacked PR: #23.
- Base: `program/wave0-foundations-v1` at `497ed954b09c158fcbf02df47a0f196eda5d61ab`.
- Verified code/test head before this ledger-only commit: `b0ddfa6de937841296b46107e6990a10b135ba5e`.
- Model impact classification: `SEMANTIC_INFRA`.
- Active consumer cutover: none.
- Production mutation: none.

### TDD RED evidence

1. Initial vocabulary RED: `3 failed, 346 passed` — intentionally missing `waterfallhunter.core.contracts`.
2. Signal/execution packet RED: `5 failed, 349 passed` — absent `SignalDecisionPacket` / `ExecutionPlan` contracts.
3. Position/notification RED: `5 failed, 354 passed` — absent `PositionState`, `PositionAmendment`, and `NotificationEvent`.
4. Contract identity review RED: `2 failed, 361 passed` — mismatched `contract_type` / `contract_version` accepted before fix.
5. Deep-immutability / payload-security review RED: `3 failed, 363 passed` — nested validated data remained mutable and delivery/secret payload keys were not fully rejected.
6. Concatenated/plural secret-key review RED: `7 failed, 366 passed` — `apitoken`, `accesstoken`, `authtoken`, `bearertoken`, `usersecret`, `tokens`, and `credentials` passed before hardening.

### Final verification at `b0ddfa6...`

- Backend full suite: `374 passed, 9 warnings`.
- Runtime parity: `runtime declarations are aligned`.
- Frontend Node 26 typecheck and production build: passed.
- Dependency audit: Python and npm jobs passed.
- Container validation: revision-labelled production artifacts built; exact backend artifact family tested; OCI revision labels verified.
- Repository hygiene/secret scan: passed.
- SonarQube Cloud: quality gate passed; `0 New issues`; `0 Security Hotspots`; no accepted/suppressed issue required.
- CodeRabbit: substantive findings on deep immutability, payload field isolation, and concatenated secret-like keys were fixed. All inline review threads are resolved. The latest completed incremental source review (`36a60af...` → `b69c3df...`) generated no actionable comments.
- Additional review coverage: explicit tests now cover non-finite JSON numbers, non-string object keys, and unsupported container values.
- Manual security diff scope: canonical contract code introduces validation/serialization only; no network/file/subprocess/credential/order-execution path and no active consumer cutover.
- Model semantic diff: none expected or accepted; no active scoring/lifecycle/ranking/execution behavior was modified.

### Wave 1A controller state

`W1-A = MERGE_READY_PENDING_MERGE_APPROVAL`.

This is an evidence state only. PR #23 remains Draft and unmerged. `MERGE_APPROVAL` has not been granted.

## Wave 1B1 migration/readiness foundation evidence

- Draft stacked PR: #24.
- Base branch: `feat/wave1a-canonical-contracts-v1`.
- Pre-stack-sync implementation head: `a57e92b693302531a56cfcdede164b2c62ce48c3`.
- Final reviewed source/test head before this ledger-only commit: `1e4c043a3649d93496fa4013289cc180fab1af3e`.
- Model impact classification: `SEMANTIC_INFRA`.
- Runtime schema-ownership cutover: none; reserved for W1-B2.
- Production migration/readiness probe: never executed.

### Implemented B1 scope

- first-party packaged migration discovery and canonical `NNNN_name.sql` identity;
- exact-byte SHA-256 migration checksums and canonical reconstruction of caller-supplied migration objects;
- immutable `schema_migrations` history with verified UPDATE/DELETE abort triggers;
- fail-closed history schema validation, including sole `version` primary key and required column constraints;
- `PRAGMA user_version` consistency and fail-closed state verification;
- per-migration atomic transaction and rollback;
- serialized pending re-check under `BEGIN IMMEDIATE` for concurrent runners;
- dedicated `db_readiness_probe` migration only;
- rollback-only deep readiness primitive with bounded busy timeout;
- URI-safe SQLite read/write probing for database paths containing reserved URI characters;
- optional integrity/FK checks, zero-residue verification, and typed `require_ready()` failure;
- schema-ownership inventory for the later B2 cutover.

### TDD / hardening evidence

- discovery foundation RED: missing migration module/package;
- runner RED: `6 failed, 370 passed` before `MigrationRunner` implementation;
- readiness RED: `8 failed, 376 passed` before `db_readiness` implementation;
- migration hardening RED: `2 failed, 393 passed` for migration identity/filename mismatch and missing-parent directory auto-creation;
- external review RED: `4 failed, 396 passed` for URI-special database paths and malformed migration-history reads;
- integrity/concurrency review RED: `8 failed, 400 passed` for incomplete history schema, weakened constraints, forged checksum/noncanonical migration identity, and concurrent-runner race;
- immutability review RED: `3 failed, 408 passed` for extra composite primary key, wrong-target immutability trigger, and non-aborting immutability trigger.

### Final source/test verification at `1e4c043...`

- Backend full suite: `411 passed, 9 warnings`.
- Runtime parity: `runtime declarations are aligned`.
- Frontend Node 26 typecheck and production build: passed.
- Dependency audit: Python and npm jobs passed.
- Container validation: revision-labelled production artifacts built; exact backend artifact family tested; OCI revision labels verified.
- Repository hygiene/secret scan: passed.
- SonarQube Cloud: quality gate passed; `0 New issues`; `0 Accepted issues`; `0 Security Hotspots`.
- CodeRabbit: URI handling, malformed history, schema completeness, checksum provenance, concurrent runners, composite-PK rejection, and immutable-trigger validation findings were fixed with regression tests. All inline review threads are resolved. Manual incremental review run `237943ef-3598-4f11-bcd2-6b17aef02464` over `249da967...` → `1e4c043...` generated no actionable comments.
- CodeRabbit docstring coverage remains a non-functional advisory warning; no behavior, safety, or integrity finding remains open.
- Model semantic diff: none expected or accepted; no scoring, lifecycle, ranking, execution-level, Telegram, or trading behavior was modified.
- Production mutation: none.

### B2 read-only inventory refresh

Repository-wide source searches reconfirm the existing runtime schema-mutation inventory. `CREATE TABLE` remains present across the catalog/ledger/outcome/lifecycle/evidence/replay/execution/provider/streaming/application stores, while explicit `ALTER TABLE` evolution remains concentrated in `core/db.py`, `core/lbank_signal_ledger.py`, `core/production_evidence.py`, and `core/lbank_execution_store.py`; `PRAGMA table_info` migration-like checks remain in `core/db.py`, `core/lbank_signal_ledger.py`, and `core/production_evidence.py` (plus tests). No B2 runtime cutover has started.

### Wave 1B1 controller state

`W1-B1 = MERGE_READY_PENDING_MERGE_APPROVAL`.

This is an evidence state only. PR #24 remains Draft and unmerged. `MERGE_APPROVAL` has not been granted. W1-B2 may proceed only as development-side planning/implementation under the approved safety gates; no Production migration is implied or authorized.
