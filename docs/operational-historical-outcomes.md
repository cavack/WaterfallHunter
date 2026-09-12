# Operational Historical Outcomes

Production verification date: 2026-08-13

## Operational behavior

- Historical source data is downloaded into `/srv/waterfallhunter/data/operational_backfill`.
- Validated reports are imported atomically into the production SQLite volume.
- Dataset and event rows are immutable and report imports are idempotent by SHA-256.
- `/api/historical-outcomes` serves the latest imported dataset.
- Candidate packets include a symbol-level `historical_outcome` packet when coverage exists.
- The production dashboard displays the operational historical summary.

Historical evidence remains provenance-labelled and is not inserted into the natural signal ledger or natural outcome table.

## Active dataset

- Source: Binance USD-M perpetual public 5m klines, funding, and archived metrics.
- Window: `1771080000000..1786632000000` (180 days).
- Report SHA-256: `70415b0b0f4cec5e3a38138c6e90dce18cfe43f855aff81920d849ec81771de9`.
- Events: 122.
- Complete modeled execution-cost packets: 122/122.
- Settled events: 118.
- Wins: 47.
- Settled win rate: 39.8305%.
- Modeled net expectancy: 0.179344R.
- Strategy equivalent to the live pipeline: no.

## Safety contract

- `operational=true`
- `observational_only=true`
- `hard_gating_allowed=false`
- `threshold_calibration_allowed=false`
- `ranking_eligible=false`
- `LIVE_TRADING_ENABLED=false`

The historical data is production-served and candidate-linked, but cannot alter ranking, lifecycle, signal thresholds, alerts, eligibility, or orders.

## Verification

- Focused storage and API tests: passed.
- Full backend suite: 311 passed, 9 deprecation warnings.
- Frontend production build, lint, and type checks: passed.
- Import retry: no duplicate dataset or events.
- Production database: 1 dataset / 122 events.
- Backend and frontend health: healthy.
- Dashboard proxy to the historical endpoint: verified.
- Pre-import SQLite backup: `/app/data/backups/pre_operational_backfill_1786632816.db`.
