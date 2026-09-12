# Raw Source Capture Tier v2

Production verification date: 2026-08-13

## Operational behavior

- Reuses the exact exchange responses already fetched by the live evaluator; it adds no duplicate market-data request.
- Captures validated closed OHLCV for 5m, 15m, 1h, and 4h from the primary exchange.
- Captures validated closed 15m OHLCV from the selected confirmation exchange.
- Captures up to 100 fresh trades after the same freshness filter used by microstructure.
- Preserves exchange, mapped symbol, reference source, timestamps, order book, derivatives, stages, gates, and the active decision contract.
- Records one immutable source packet per symbol per 15-minute bucket.
- Claims source replay readiness only when both closed OHLCV and fresh trade evidence exist.

## Safety and compatibility

- Existing v1 snapshots remain immutable and are not reclassified.
- The v2 migration adds readiness columns without rewriting old rows.
- Capture remains fail-open and observational-only.
- No score, threshold, ranking, lifecycle, notifier, Telegram, eligibility, or order behavior changes.
- `LIVE_TRADING_ENABLED=false`.

## Data integrity

- Independent verification over 66 initial raw packets found all stored primary OHLCV series closed, contiguous, and gap-free.
- A verified packet contained 119 closed candles for each primary timeframe, 119 confirmation candles, and 100 fresh trades.
- Open candles are removed by the existing production candle validator before persistence.
- Trade rows are the freshness-filtered rows actually used for footprint and flow calculations.
- First natural v2 verification: 15 packets, 13 source-replay-ready (86.67%), and 0 recorder failures.
- Incomplete market packets remain explicitly not replay-ready; coverage is not zero-filled or promoted to 100%.
- A natural v2 CFX packet was 13,269 compressed bytes and contained all four primary OHLCV series, confirmation OHLCV, and 100 fresh trades.

## Capacity

- Raw fields duplicated inside derived packets were removed before canonical serialization.
- Capture cadence was reduced from 5 minutes to 15 minutes.
- Estimated compressed source storage at 155 symbols: approximately 198 MB/day or 5.8 GB per 30 days.
- Available disk at deployment: approximately 100 GB.
- A durable archive/retention lifecycle is required before indefinite collection.

## Verification

- Focused candle, trade, recorder, validator, ledger, and stale-trigger checks: passed.
- Full backend suite: 315 passed, 9 deprecation warnings.
- Frontend production build, lint, and type checks: passed.
- Backend and frontend health: healthy.
- Pre-migration SQLite backup: `/app/data/backups/pre_raw_source_capture_1786634080.db`.
