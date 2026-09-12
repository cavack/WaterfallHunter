# Balanced Signal Quality Design

## Objective

Increase valid simulated-alert coverage without relaxing execution quality. The system must not manufacture data, execute live orders, or promote a parameter set solely because of a small-sample win rate.

## Architecture

The strategy is split into three deterministic stages:

1. **Regime (4h):** require valid closed candles, broken dynamic support, and a bearish structure. This stage only allows a symbol to be evaluated; it does not emit a trade.
2. **Setup (1h):** classify failed pullback, strong breakdown, or continuation. Failed pullback has priority. A setup expires if it reclaims support.
3. **Trigger (15m/5m):** require two bearish closed candles, lower high, selling-volume acceleration, real sell-flow/footprint where available, executable orderbook, and cross-exchange confirmation. Only this stage can move a candidate to ARMED or TRIGGERED.

## Quantity and Quality Controls

- Evaluate trigger conditions on completed 15m candles while the 4h regime remains valid. This increases opportunity frequency without using open candles.
- Allow at most one final trigger per symbol per 24 hours.
- Do not set a global daily quota. A daily target is an operational observation, never a reason to weaken a gate.
- Preserve the existing USDT-linear-perpetual, active-market, price, volume, spread, slippage, spoofing, contract-filter, and no-stale-data constraints.
- Keep BTC and ETH in catalog tracking, but do not bypass quality gates for them.

## Risk and Outcome Definition

- Keep signal-only operation and `LIVE_TRADING_ENABLED=false`.
- Use the structural stop, constrained by a minimum and maximum percent distance. The backtest must use the next candle open, conservative two-sided fee/slippage, and stop-first resolution for intrabar ambiguity.
- A target must be at least 1R net of cost. Win-rate optimization by shrinking TP below 1R is rejected.
- A timeout at 24 hours is reported separately and not counted as a win.

## Research Protocol

- Use a fixed 10-symbol research universe selected before each run.
- Fetch only real USDT-perpetual OHLCV. Reject missing, duplicate, gapped, invalid, or open candles.
- Reserve chronological windows: oldest six months for parameter selection, subsequent three months for validation, and the most recent month/week as holdout tests.
- Backtest parameters across a bounded grid of regime, setup, trigger, and structural-risk thresholds. Store every run, including failed runs.
- Historical L2/trade data is not fabricated. The footprint/orderbook filter remains live-only unless the equivalent historical source is available.

## Acceptance Criteria

A configuration may be proposed for simulated deployment only when all conditions hold:

- At least 50 settled validation trades.
- Positive net expectancy after configured costs.
- Net target at least 1R.
- No material degradation between validation and six-month aggregate results.
- No use of stale, simulated, or substituted execution data.
- The number of final signals per day is reported; it is not force-targeted.

## Failure Handling

- Missing required data fails closed.
- Rate limiting retries deterministically with bounded backoff and does not convert a failed source into a passing result.
- A failed backtest run is preserved with source and error provenance.

## Compatibility

Gemini/Ollama stay advisory-only. Existing container security, lifecycle persistence, WebSocket resilience, dashboard transport, and live-trading lock remain unchanged.
