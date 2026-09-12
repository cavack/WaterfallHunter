# Production Evidence Recorder

Production verification date: 2026-08-13

## Operational behavior

- Records one immutable decision packet per symbol per five-minute bucket.
- Captures the actual running evaluator result before dashboard compaction.
- Stores exchange provenance, timestamps, the top available order-book levels (up to 25 per side), ticker, candle-analysis output, microstructure, derivatives, benchmark context, stages, gates, score packets, and the active decision contract.
- Uses canonical JSON, SHA-256, and zlib compression.
- Deduplicates retries within the same symbol/bucket.
- Fails open: recorder failures cannot block or change candidate evaluation.
- Serves a read-only operational summary at `/api/production-evidence` and on the production dashboard.

## Safety contract

- `operational=true`
- `observational_only=true`
- `hard_gating_allowed=false`
- `LIVE_TRADING_ENABLED=false`
- Immutable update/delete triggers are installed.
- No ranking, threshold, lifecycle, alert, Telegram, eligibility, or order code path is controlled by the recorder.

## Replay boundary

Decision-packet replay is available. Raw-source replay is deliberately not claimed yet:

- `raw_ohlcv_captured=false`
- `raw_trades_captured=false`
- `source_replay_ready=false`

The next capture tier must add raw closed OHLCV and trade evidence before `strategy_equivalent=true` can be considered.

## Fresh production evidence

- First verified snapshots: 33 and increasing during the live hunter cycle.
- Recorder failures: 0.
- Actual packet includes LBank reference, Binance primary market data and derivatives, and Bybit confirmation provenance.
- Active contract recorded with ARMED 60, TRIGGERED 85, cross-exchange deviation 5%, and live trading disabled.
- Observed compression: approximately 2.4 KB per snapshot from substantially larger canonical packets.
- Projected storage at 155 symbols and five-minute buckets: approximately 101 MB/day.
- Available server storage at deployment: approximately 100 GB.

## Verification

- Focused persistence, API, ledger, and stale-trigger checks: 23 passed.
- Full backend suite: 314 passed, 9 deprecation warnings.
- Frontend production build, lint, and type checks: passed.
- Backend and frontend health: healthy.
- Dashboard API proxy: verified.
- Pre-migration SQLite backup: `/app/data/backups/pre_production_evidence_1786633609.db`.
