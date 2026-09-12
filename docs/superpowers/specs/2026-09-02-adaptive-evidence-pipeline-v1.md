# Adaptive Evidence Pipeline v1 — Design Spec

## Goal

Reduce WaterfallHunter decision latency and stale/missing market evidence without changing ScoreV2, lifecycle semantics, eligibility, Anti-Chase, signal provenance, notification ordering, or execution policy.

## Verified production pressure

Baseline release: `460f557493f251bcd7ced27cd9f7736b607b037c`.

- Backend is healthy at a 2 GiB cgroup ceiling with restart count 0 after the runtime-memory release.
- Post-deploy global candidate analysis p95 commonly remains about 270–330 seconds, above the 180-second freshness objective.
- Production source selection frequently walks multiple venues before a complete packet is found.
- A live snapshot showed 111 of 129 analyzable candidates with at least one primary-source fallback and a mean 2.60 failed sources.
- Dominant fallback reasons were `insufficient fresh trades` and `stale orderbook snapshot`.
- The validator already owns a WebSocket manager, but PRE-TRIGGER retains liquidation-only streams and the microstructure path still fetches trades plus two additional order books through REST.
- Closed 15m/1h/4h candles are repeatedly re-fetched even though they cannot change before the next close.

## Safety invariants

`ENTRY_READY=78`, `FORMING=55`, and Anti-Chase `1.2 ATR` remain unchanged.
`LIVE_TRADING_ENABLED=false` remains mandatory.
No missing/stale evidence may be converted to directional evidence.
No cached observation may outlive its canonical freshness/closed-candle boundary.

## Wave A architecture

### 1. Continuous deadline scheduler

Replace the all-candidates `asyncio.gather()` barrier with a bounded in-flight set and per-symbol start-to-start deadlines. New candidates are immediately due. State priority remains `TRIGGERED → ARMED → PRE-TRIGGER → FUEL-RICH → WATCH`, but each state also receives a bounded target interval.

Initial runtime-only cadence targets:

- TRIGGERED: 15 s
- ARMED: 15 s
- PRE-TRIGGER: 30 s
- FUEL-RICH: 90 s
- WATCH: 150 s

These values are scheduling budgets, not model thresholds. Per-symbol single-flight is mandatory; no candidate may have two concurrent evaluations.

Maintenance work (active-universe refresh, expiration reconciliation, universe snapshot, heap trim and cache pruning) runs independently of evaluation completion, so a slow WATCH candidate cannot block a due PRE-TRIGGER candidate.

### 2. Hot evidence reuse

PRE-TRIGGER and ARMED keep full ticker/order-book/trade WebSocket evidence instead of liquidation-only evidence. Existing liquidation semantics stay intact.

The WebSocket manager keeps bounded recent order-book snapshots and a bounded rolling fresh-trade window. The microstructure analyzer may consume this preloaded packet only when it independently satisfies the existing freshness, sample-count and three-snapshot requirements; otherwise it falls back to the existing REST acquisition path.

### 3. Causal closed-candle cache

The candle analyzer caches only validated closed OHLCV series. A cache entry is reusable only when its newest closed candle covers the latest expected closed bucket for that timeframe. A boundary miss or exchange lag causes a real fetch; stale data is never promoted as fresh.

The cache is process-local, bounded, and stores at most 1024 series. It preserves exact source rows used by replay/evidence capture. No score calculation changes.

### 4. Runtime telemetry

Add low-cardinality Prometheus observations for:

- evaluation duration by lifecycle state;
- evidence-stage duration;
- primary-source attempt count;
- WebSocket/preloaded evidence hits and REST fallbacks;
- candle-cache hit/miss/eviction counts;
- in-flight evaluation count and due-candidate backlog.

`analysis_observed_at` continues to represent the start of the causal evaluation. Scheduler queue time and evaluation completion time are separately observable; the system must not fake freshness by rewriting the analysis timestamp late.

## Verification / release gate

Wave A is successful only if exact-head tests and CI pass, the deployed revision is exact, health remains green, restart/OOM counts remain zero, and a risk-proportional soak demonstrates materially improved freshness. The primary production target is global usable analysis p95 `<180s`; PRE-TRIGGER/ARMED should be materially lower.

## Conditional Wave B

Wave A was deployed on exact main `4940daf58ef40831fccab98afa0e245602e50875` and remained healthy with zero restart/OOM events, but the release freshness gate failed: a production snapshot had 93 usable analyses, global usable analysis p95 about 568 seconds, max about 680 seconds, and a sustained due backlog around 111-124 while all 12 evaluation slots were commonly occupied. REST evidence fallbacks increased rapidly while WebSocket evidence hits remained near zero. The `<180s` gate therefore requires Wave B; strategy thresholds must not be loosened to hide runtime latency.

Wave B routes only `FUEL-RICH` candidates through a shared multi-symbol order-book/trade/ticker pool. `PRE-TRIGGER` and `ARMED` keep their direct hot streams; `WATCH` retains no heavy evidence stream. Each exchange pool is single-flight per evidence kind, bounded to 64 symbols, protected by its own circuit breakers, and writes only through the existing causal TTL/history ingestion helpers. Shared pools use a dedicated CCXT Pro client per venue, separate from PRE-TRIGGER/ARMED direct-stream clients, so dynamic shared unwatch operations cannot cancel direct subscriptions during lifecycle promotion. Unknown, stale, future-dated, incomplete, or unsubscribed-symbol data remains unusable and REST fallback stays fail-closed.

Activation is capability-gated: all six CCXT Pro methods (`watch` and explicit `unwatch` for order book, trades, and tickers) must report native `True` support and be callable. Production-library probes confirmed this complete contract for Binance and Bybit. OKX/KuCoin lack explicit unwatch capability declarations for the full three-stream set, and MEXC/BingX lack required multi-symbol capabilities, so they remain on REST fallback. Bybit shared order books use the venue-safe depth limit 50; Binance uses 20.

Wave B adds low-cardinality `waterfall_websocket_shared_evidence_tasks` and `waterfall_websocket_shared_evidence_subscribers` gauges. Release success still requires exact-artifact CI/review, DR/recovery certification, guarded deploy, zero restart/OOM regressions, and a production soak proving global usable analysis p95 holds below 180 seconds.

## Separate follow-up work

Decision-event snapshot churn and long-term production-evidence retention are real measured storage pressures, but they touch immutable decision/persistence semantics. They will use a separate backend/data design and release path after runtime freshness is stable.

Threshold/weight/gate/Anti-Chase optimization is also separate. It requires production-equivalent replay, development/walk-forward evaluation, embargo, sensitivity analysis, and an untouched holdout. No calibration is promoted by this runtime work.

## Blast radius

Wave A may modify the hunter scheduler, WebSocket evidence cache, microstructure acquisition adapter, candle analyzer cache, Prometheus telemetry, focused tests and documentation. It must not modify decision thresholds, score weights, lifecycle transition rules, EntryDecisionPolicy semantics, notification eligibility, immutable signal-ledger rules, or live-trading configuration.
## Conditional Wave C — WATCH fairness

After the shared-membership churn fix reached Production, FUEL-RICH p95 fell to about 82 seconds and PRE-TRIGGER p95 to about 30 seconds, but global usable p95 remained about 384 seconds because WATCH work was starved behind continuously due heavy states. Production histograms showed 128 of 136 WATCH evaluations completed below 0.1 seconds while FUEL-RICH/PRE-TRIGGER averaged about 21 seconds and all 12 evaluation slots remained occupied.

Wave C keeps `DEFAULT_EVALUATION_CONCURRENCY=12` and all lifecycle/score/entry/Anti-Chase semantics unchanged. When WATCH work is due and no WATCH evaluation is already in flight, the deadline scheduler reserves at most one of the existing slots for WATCH. This is a fairness reservation, not an extra concurrency lane: near-trigger work continues to own the other slots and a second WATCH slot is never reserved while one WATCH evaluation is active.
