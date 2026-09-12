# WaterfallHunter Runtime Schema Ownership Inventory

**Inventory baseline:** `feat/wave1b2-schema-ownership-cutover-v1` rebuilt on
canonical `main` at `e2b18d443e87433bc41dc06067fdf8f2c17dcd1b` after the
verified Wave 1B1 squash merge.

**Original design baseline:** `652f99446ed523c0a602798dde4457bab7983373`.

**Purpose:** enumerate the actual runtime modules that currently own SQLite schema and distinguish them from broad-search false positives before schema ownership is cut over to first-party migrations.

## Verification method

The inventory was refreshed in two stages:

1. repository-wide recall searches for `CREATE TABLE`, `CREATE INDEX`, `CREATE TRIGGER`, `ALTER TABLE`, and `PRAGMA table_info`;
2. direct inspection of every candidate file on the actual B2 branch.

Direct-file inspection is authoritative when broad search and current branch content disagree.

## Actual runtime schema owners

| Source file | Managed objects | Current mutation | B2 disposition |
| --- | --- | --- | --- |
| `backend/src/waterfallhunter/core/db.py` | `lbank_catalog`, `catalog_events` | `CREATE TABLE`; `PRAGMA table_info(lbank_catalog)`; legacy `ALTER TABLE` evolution | HIGH. Move complete current catalog/event schema into v2 baseline; constructor becomes verify/use only. Preserve catalog lifecycle/CAS behavior. |
| `backend/src/waterfallhunter/core/lbank_signal_ledger.py` | `lbank_signal_ledger`, named index, UPDATE/DELETE immutability triggers | create table/index/triggers; `PRAGMA table_info`; legacy `ALTER TABLE` evolution | HIGH. Preserve immutable ledger and atomic catalog-state + signal insert transaction exactly. |
| `backend/src/waterfallhunter/core/lbank_signal_outcome.py` | `lbank_signal_outcomes`, named index, UPDATE/DELETE immutability triggers | create table/index/triggers | Cut over; preserve FK to ledger and append-only semantics. |
| `backend/src/waterfallhunter/core/stage_lifecycle.py` | `lbank_stage_lifecycle`, named index | create table/index | Cut over without changing Lifecycle V1 semantics. |
| `backend/src/waterfallhunter/core/production_evidence.py` | `production_evidence_snapshots`, two indexes, UPDATE/DELETE immutability triggers | create table/index/triggers; `PRAGMA table_info`; legacy `ALTER TABLE` evolution | HIGH. Baseline must contain every current evolved v2/v3/v4/v5 column from the outset; never rewrite historical evidence. |
| `backend/src/waterfallhunter/core/feature_replay.py` | `production_feature_replay_results_v2`, index, UPDATE/DELETE immutability triggers | create table/index/triggers | Cut over; preserve replay uniqueness, FK, hashes/results, and observational-only semantics. |
| `backend/src/waterfallhunter/core/lbank_execution_store.py` | `lbank_execution_observations`, `lbank_execution_observation_history`, five indexes | create tables/indexes | Cut over; preserve current/latest + append-only-history transaction semantics. Fresh direct inspection found no current `ALTER TABLE` in this file, correcting the prior search-only inventory. |
| `backend/src/waterfallhunter/core/lbank_execution_decision.py` | `lbank_execution_decision_log`, two indexes | create table/indexes | Cut over; remain observational/non-trading and non-authoritative for strategy eligibility. |
| `backend/src/waterfallhunter/core/historical_outcome_store.py` | `operational_historical_outcome_datasets`, `operational_historical_signal_outcomes`, index, four UPDATE/DELETE immutability triggers | create tables/index/triggers | Cut over; preserve historical/live provenance separation and immutable imported evidence. |
| `backend/src/waterfallhunter/core/provider_registry.py` | `provider_states` | create table | Cut over. Optional legacy object because provider registry is not constructed by current `main.py`; if absent, v2 may create it. |

## Current source files that are not schema owners

The previous broad-search inventory incorrectly flagged these files. Direct inspection of the B2 branch shows:

| Source file | Direct-file result | B2 treatment |
| --- | --- | --- |
| `backend/src/waterfallhunter/core/ws_streamer.py` | no SQLite import or DDL | Remove from schema-owner list; no B2 schema edit. |
| `backend/src/waterfallhunter/core/lbank_execution.py` | read-only exchange observer; no SQLite schema mutation | Remove from schema-owner list; preserve SIGNAL_ONLY/read-only execution boundary. |
| `backend/src/waterfallhunter/main.py` | no direct DDL | Not a schema owner. B2 changes only runtime schema-gate/construction ordering; migration must never run automatically here. |

## Script/test-only schema

| Source file / area | Role | B2 treatment |
| --- | --- | --- |
| `scripts/historical_backtest.py` | research/disposable historical schema | Explicitly outside Production runtime migration ownership. |
| `backend/tests/` | temporary schema fixtures | Replace runtime-constructor bootstrap assumptions with first-party migration test helpers. Legacy fixture creation exists only under tests. |
| `docs/superpowers/` | historical design/plan SQL examples | Documentation only. |

## Current managed runtime tables

The version-2 runtime schema contract covers:

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

Migration infrastructure remains:

14. `schema_migrations`
15. `db_readiness_probe`

## Actual legacy evolution sites

Fresh direct inspection confirms constructor-level schema evolution only in:

- `backend/src/waterfallhunter/core/db.py`
  - `lbank_catalog.last_seen_at`
  - `lbank_catalog.scan_eligible`
  - `lbank_catalog.consecutive_missing_snapshots`
  - `lbank_catalog.lifecycle_id`
- `backend/src/waterfallhunter/core/lbank_signal_ledger.py`
  - `quote_volume_at_trigger`
  - `volume_gate_passed`
  - `proxy_execution_disagreement`
- `backend/src/waterfallhunter/core/production_evidence.py`
  - `source_ohlcv_captured`
  - `source_trades_captured`
  - `source_replay_ready_v2`
  - `feature_replay_ready_v3`
  - `triggered_path_replay_ready_v4`
  - `decision_provenance_ready_v5`
  - `raw_derivatives_captured_v5`
  - `production_evidence_complete_v5`
  - `confirmation_ohlcv_captured_v5`
  - `code_sha256_v5`

These columns must appear directly in the complete version-2 baseline definitions. B2 does not issue historical `ADD COLUMN` statements against the current canonical Production shape.

## Legacy presence policy

For a pre-migration runtime DB (`user_version=0`, no `schema_migrations`), current-main-owned tables are required for `LEGACY_CANONICAL` adoption:

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

Allowed missing optional legacy objects:

- `lbank_execution_observations`
- `lbank_execution_observation_history`
- `provider_states`

Optional objects are still validated if present. Version 2 creates them if absent. Missing required objects fail preflight before any write.

Unknown non-managed legacy tables are reported and preserved; they are not silently dropped or rewritten.

## Runtime construction concern

`backend/src/waterfallhunter/main.py` currently creates these DB-backed objects at module import:

- `DBAdapter`
- `StageLifecycleStore`
- `HistoricalOutcomeStore`
- `ProductionEvidenceRecorder`
- `FeatureReplayStore`
- `LBankSignalLedger`
- `LBankSignalOutcomeStore`
- `LBankExecutionDecisionLogger`

`LBankExecutionStore` is created later when the execution-shadow worker is built.

B2 must not auto-migrate during import or startup. Main will use non-mutating construction and perform a read-only full-schema gate before any background task starts.

## B1 ownership boundary

Wave 1B1 owns only:

1. `schema_migrations` plus canonical immutability triggers;
2. `db_readiness_probe` via `0001_db_readiness_probe.sql`.

B1 does not cut over business/evidence/execution constructors.

## B2 target ownership boundary

Wave 1B2 adds:

- a declarative canonical runtime schema manifest;
- read-only preflight classification;
- `0002_runtime_schema_baseline.sql`;
- an explicit migration command requiring `--apply` for mutation;
- verify-only store constructors;
- a read-only application startup schema gate;
- a repository runtime-DDL guard.

Migration execution is never coupled to application startup.

## B2 exit criteria

Before W1-C starts, B2 must prove:

- clean-install migration to v2;
- current canonical legacy adoption to v2 with business/evidence row hashes and counts preserved;
- incompatible/partial legacy cases fail before write;
- all actual runtime schema owners are verify/use only;
- no executable runtime DDL remains outside `waterfallhunter/migrations/` and dedicated migration infrastructure;
- immutable ledger/outcome/evidence/history triggers and FKs remain valid;
- full backend + Golden Regression exactness;
- five first-party CI jobs green;
- independent static/code review clean.

No Production migration is implied by completing B2 development work. Production execution still requires a verified backup/restore plus separate `MIGRATION_APPROVAL`; deployment remains a separate approval.
