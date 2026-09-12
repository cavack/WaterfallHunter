# Feature-Equivalent Replay

## Operational status

The production evidence recorder writes immutable `production_decision_evidence_v7`
packets every 15 minutes. A background worker replays eligible packets through
the production candle, microstructure, strategy-stage, and scoring functions and
appends immutable results to a generation-aware replay ledger. Replay v2 uses
`production_feature_replay_results_v2` with a unique
`(snapshot_id, replay_version)` key, so engine upgrades can replay the same
immutable evidence without deleting or rewriting earlier results.

The endpoint is read-only at `/api/feature-replay`; the dashboard renders the
same report. The worker runs every 60 seconds in batches of three.

## Promotion boundary

Replay is observational only. It cannot modify ranking, signal thresholds,
candidate state, eligibility, Telegram delivery, or order execution. Global
`strategy_equivalent` remains false unless all of these are true:

- at least 100 packets have been replayed;
- there are zero mismatches and zero non-replayable/error results;
- at least one real `TRIGGERED` decision path is exactly equivalent.

Replay v7 accepts only evidence whose recorded Python source-tree SHA-256 equals
the active replay generation. It rebuilds candle analysis through the same
`evaluate_closed_sources` production seam, derivatives through the same Binance
or CoinGlass parsers and `DerivativesAnalyzer`, microstructure through the same
`MicrostructureAnalyzer`, and scoring/status through the production validator.
Normalized derivatives are comparison targets, not replay inputs.

Raw confirmation OHLCV is mandatory for replay completeness. Every derivatives
fallback attempt retains its exchange, mapped contract, retrieval time, failure
reason, and raw provider response when one exists.

After immutable recording, dashboard/runtime metrics retain only derivatives
fallback metadata; raw fallback payloads are released from `active_candidates`
to keep the backend below its memory cgroup limit.

The v7 source captures the data required for non-triggered decisions. When the
real validator enters the `TRIGGERED` branch, it additionally stores the exact
5-minute OHLCV response requested with limit 1000, the mark price, and the
evaluation timestamp. Replay calls the production `PositionCalculator` with
those sources and that timestamp. Global equivalence still requires at least
one naturally occurring, exactly equivalent triggered-path result; synthetic
test evidence never contributes to the production count.

Position calculation alone is classified as `TRIGGER_CANDIDATE`, not
`TRIGGERED`. A replay counts as `TRIGGERED` only when its immutable evidence was
recorded after `SignalLedger.persist_trigger` returned a real signal id. Final
AI-veto, leverage failure, stale-trigger suppression, and persistence failure
events are also written as separate immutable packets. These final packets use
an isolated event key and cannot be lost when an ordinary packet for the same
symbol already exists in the 15-minute evidence bucket.

Order-book source evidence now retains every level actually consumed by the
production microstructure calculation. This closes the PEPE v6 mismatch where
an exchange returned more rows than its requested limit and depth/churn were
calculated from rows that the prior evidence packet did not retain.

At deployment verification, replay v7 passed the sample floor with 102 of
102 exact `REJECTED` replays, no mismatch and no non-replayable result. PEPE was
also replayed exactly. No final
production trigger event had yet occurred, so `strategy_equivalent=false` and
the next roadmap stage remains blocked by natural production evidence.

## Integrity and rollback

Both evidence and replay-result tables reject updates and deletes with SQLite
triggers. Replay rows are idempotent by unique `snapshot_id`. Before deployment,
the database was backed up to
`/app/data/backups/pre_feature_replay_1786635389.db`.
Additional pre-v4 and pre-generation-ledger backups are stored at
`/app/data/backups/pre_trigger_source_v4_1786636141.db` and
`/app/data/backups/pre_generation_ledger_1786636604.db`.
The pre-v6 final-event deployment backup is
`/app/data/backups/pre_final_event_replay_v6_1786641208.db`.
The pre-v7 full-orderbook deployment backup is
`/app/data/backups/pre_full_orderbook_replay_v7_1786685427.db`.

Rollback consists of restoring the prior application images. The new tables and
columns are additive and are not read by the trading decision path. Database
restoration is only needed if the additive evidence data must also be removed.

## Safety invariants

- `LIVE_TRADING_ENABLED=false`
- no order placement
- no production threshold or weight change
- no hard gating or promotion
- no Telegram test message
- stale-trigger and lifecycle persistence semantics remain covered by the full
  backend test suite
