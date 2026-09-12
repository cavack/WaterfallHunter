# Adaptive Evidence Pipeline v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep near-trigger WaterfallHunter analysis fresh under bounded resources by removing the all-candidate batch barrier and reusing causally valid market evidence.

**Architecture:** A process-local deadline scheduler maintains bounded single-flight evaluations while state-specific deadlines prioritize urgent candidates. PRE-TRIGGER/ARMED reuse bounded WebSocket order-book/trade evidence; closed OHLCV uses a causal bounded cache; all reuse paths fall back to the existing REST/fail-closed semantics.

**Tech Stack:** Python 3.12, asyncio, CCXT/CCXT Pro, FastAPI, prometheus_client, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-09-02-adaptive-evidence-pipeline-v1.md`

## Global Constraints

- Base SHA: `460f557493f251bcd7ced27cd9f7736b607b037c` unless `main` moves before PR/release, in which case rebase and re-verify.
- Preserve `ENTRY_READY=78`, `FORMING=55`, Anti-Chase=`1.2 ATR`.
- Preserve strict/experimental eligibility, lifecycle semantics, signal provenance, persistence-before-notification and immutable signal-ledger behavior.
- `LIVE_TRADING_ENABLED=false`; no real orders.
- Missing/stale evidence remains unavailable; cache reuse must never extend canonical freshness.
- Every production-code behavior starts with a failing regression test.

---
### Task 1: Pure deadline scheduling contract

**Files:**
- Modify: `backend/src/waterfallhunter/core/hunter_schedule.py`
- Modify: `backend/tests/test_hunter_schedule.py`

**Interfaces:**
- Produces: `HunterDeadlineSchedule`, `evaluation_interval_seconds(state)`, `due_candidates(...)`, `seconds_until_next_due(...)`.
- Consumes: current candidate DB rows and `scanner.active_candidates` freshness metadata.

- [ ] **Step 1: Write RED tests** proving new candidates are due immediately, PRE-TRIGGER outranks overdue WATCH, demotion cannot starve already-due work, removed candidates are pruned, and the same symbol cannot be returned when marked in-flight.

```python
schedule = HunterDeadlineSchedule()
schedule.sync(candidates, now=100.0)
assert [s for s, _ in schedule.due_candidates(candidates, live, now=100.0, in_flight=set(), limit=2)] == ["PRE", "WATCH"]
schedule.mark_started("PRE", "PRE-TRIGGER", now=100.0)
assert schedule.seconds_until_next_due(candidates, now=100.0, in_flight={"WATCH"}) == 30.0
```

- [ ] **Step 2: Run `pytest -q backend/tests/test_hunter_schedule.py` and confirm RED because the deadline scheduler does not exist.**
- [ ] **Step 3: Implement the minimal bounded schedule** with state intervals `15/15/30/90/150` seconds and no background tasks.
- [ ] **Step 4: Re-run the focused test and confirm GREEN.**
- [ ] **Step 5: Run `git diff --check`.**

---
### Task 2: Replace the hunter batch barrier with bounded single-flight deadlines

**Files:**
- Modify: `backend/src/waterfallhunter/main.py`
- Modify: `backend/tests/test_hunter_progress_semantics.py`
- Modify: `backend/tests/test_hunter_flush_semaphore.py`
- Modify: `backend/tests/test_shutdown_hunter_drain.py`
- Modify: `backend/tests/test_hunter_error_logging.py`

**Interfaces:**
- Consumes: `HunterDeadlineSchedule` from Task 1 and existing `evaluate_candidate(symbol, data)`.
- Produces: one long-running hunter loop with at most `DEFAULT_EVALUATION_CONCURRENCY` in-flight symbols and periodic maintenance independent of batch completion.

- [ ] **Step 1: Write RED async regressions** proving a slow WATCH evaluation does not delay a newly due PRE-TRIGGER when a slot frees; a symbol never overlaps itself; periodic flush/universe maintenance does not consume an evaluation slot; and shutdown drains in-flight work.

```python
assert max_inflight <= DEFAULT_EVALUATION_CONCURRENCY
assert per_symbol_peak["PRE"] == 1
assert "PRE" in started_before_slow_watch_finished
```

- [ ] **Step 2: Run the four hunter-focused test files and confirm RED on the batch-barrier behavior.**
- [ ] **Step 3: Implement continuous scheduling** using an `in_flight: dict[str, Task]`, a caller-supplied maintenance interval (60 seconds in startup), task reaping via `asyncio.wait`, and scheduler wakeups via `_hunter_stop_event`.
- [ ] **Step 4: Keep `_hunter_last_progress_at` success-only and `_hunter_last_completed_at` as successful maintenance completion; preserve traceback logging for failed evaluations.**
- [ ] **Step 5: Re-run hunter-focused tests and confirm GREEN.**

---
### Task 3: Bounded hot WebSocket evidence packet

**Files:**
- Modify: `backend/src/waterfallhunter/core/ws_streamer.py`
- Modify: `backend/src/waterfallhunter/main.py`
- Modify: `backend/tests/test_ws_streamer.py`
- Modify: `backend/tests/test_websocket_metrics.py`

**Interfaces:**
- Produces: `get_realtime_orderbook_samples(ex_name, symbol, count=3, min_span_seconds=...)` and a rolling `get_realtime_trades(...)` window bounded by count and TTL.
- PRE-TRIGGER and ARMED call the existing full `subscribe`; other states keep current lighter/unsubscribed behavior.

- [ ] **Step 1: Write RED tests** proving order-book history is deep-copied, bounded, freshness-checked and temporally separated; trade batches merge/deduplicate into a bounded fresh window; unsubscribe removes all history; PRE-TRIGGER requests full evidence rather than liquidation-only evidence.

```python
manager._ingest_orderbook("binance", symbol, first, received_at=10.0)
manager._ingest_orderbook("binance", symbol, second, received_at=10.3)
manager._ingest_orderbook("binance", symbol, third, received_at=10.6)
assert len(manager.get_realtime_orderbook_samples("binance", symbol, now=10.7)) == 3
```

- [ ] **Step 2: Run `pytest -q backend/tests/test_ws_streamer.py backend/tests/test_websocket_metrics.py` and confirm RED.**
- [ ] **Step 3: Implement bounded history/rolling-trade ingestion** without changing the 5-second order-book TTL or 60-second trade TTL.
- [ ] **Step 4: Change `_sync_websocket_evidence_subscription` so PRE-TRIGGER and ARMED use full evidence; preserve liquidation subscription and WATCH/FUEL behavior.**
- [ ] **Step 5: Re-run focused tests and confirm GREEN.**

---
### Task 4: Reuse hot evidence in microstructure with fail-closed REST fallback

**Files:**
- Modify: `backend/src/waterfallhunter/core/microstructure.py`
- Modify: `backend/src/waterfallhunter/core/multi_exchange_validator.py`
- Modify: `backend/src/waterfallhunter/core/multi_exchange.py`
- Modify: `backend/tests/test_microstructure.py`
- Modify: `backend/tests/test_multi_exchange_validator.py`

**Interfaces:**
- `MicrostructureAnalyzer.analyze(..., preloaded_snapshots=None, preloaded_trades=None)` keeps the current return contract.
- `compatible_market_sources(..., realtime_ticker_getter=None)` may use a fresh WS ticker before REST; invalid/missing cache data still executes the existing REST path.

- [ ] **Step 1: Write RED tests** proving three valid preloaded snapshots + 20 fresh trades make zero REST order-book/trade calls, while incomplete/stale preload falls back to REST and preserves existing rejection reasons.
- [ ] **Step 2: Add a RED validator test** proving a fresh cached ticker is used for price compatibility and a stale/missing ticker calls `fetch_ticker`.
- [ ] **Step 3: Run focused microstructure/validator tests and confirm RED.**
- [ ] **Step 4: Implement optional preload adapters only; do not relax any validity, freshness, depth, spread, spoofing or sell-flow check.**
- [ ] **Step 5: Wire `cross_check_symbol` to WebSocket ticker, order-book samples and rolling trades.**
- [ ] **Step 6: Re-run focused tests and confirm GREEN, including existing source-capture tests.**

---
### Task 5: Causal closed-OHLCV cache

**Files:**
- Modify: `backend/src/waterfallhunter/core/candle_analyzer.py`
- Modify: `backend/tests/test_candle_analyzer.py`
- Modify: `backend/tests/test_candle_source_capture.py`

**Interfaces:**
- Produces an internal bounded cache keyed by `(exchange_id, symbol, timeframe, limit)` and latest expected closed bucket.
- Existing `analyze_candles(...)` and `evaluate_closed_sources(...)` public contracts remain unchanged.

- [ ] **Step 1: Write RED tests** proving repeated analysis inside the same closed-candle bucket fetches each primary/confirmation series once; crossing a candle boundary causes a new fetch; an exchange-lagged response that does not reach the expected closed bucket is not cached; cache size is bounded.

```python
await analyzer.analyze_candles(exchange, symbol)
await analyzer.analyze_candles(exchange, symbol)
assert exchange.fetch_count["1h"] == 1
clock.advance(3600)
await analyzer.analyze_candles(exchange, symbol)
assert exchange.fetch_count["1h"] == 2
```

- [ ] **Step 2: Run candle analyzer/source-capture tests and confirm RED.**
- [ ] **Step 3: Implement cache lookup only after closed-candle validation; cache entries must cover the current expected closed start timestamp.**
- [ ] **Step 4: Preserve exact closed rows in `source_capture`; no open candle may be persisted.**
- [ ] **Step 5: Re-run focused tests and confirm GREEN.**

---
### Task 6: Low-cardinality runtime telemetry

**Files:**
- Modify: `backend/src/waterfallhunter/core/multi_exchange_validator.py`
- Modify: `backend/src/waterfallhunter/main.py`
- Modify: `backend/tests/test_metrics_async_boundary.py`
- Modify: `backend/tests/test_websocket_metrics.py`

**Interfaces:**
- Validator emits an internal bounded `runtime_diagnostics` dictionary with stage durations and source-attempt count; dashboard compaction does not expose it.
- Prometheus metrics use only fixed labels (`state`, `stage`, `outcome`) and never symbol labels.

- [ ] **Step 1: Write RED tests** proving metrics names render on `/metrics`, symbol names are not labels, cache/WS hit counters update, and stage timing capture survives unavailable paths.
- [ ] **Step 2: Run metrics-focused tests and confirm RED.**
- [ ] **Step 3: Add histograms/gauges/counters** for evaluation duration, stage duration, source attempts, in-flight/backlog, WS evidence hit/fallback, and candle-cache hit/miss/eviction.
- [ ] **Step 4: Record monotonic stage durations in validator without modifying decision timestamps.**
- [ ] **Step 5: Re-run metrics tests and confirm GREEN.**

---

### Task 7: Proportional verification, review and release

**Files:** exact final diff only.

- [ ] Run focused scheduler/WS/microstructure/candle/validator/metrics tests.
- [ ] Run full backend suite with `PYTHONPATH="$WT:$WT/backend/src"` on the project backend venv.
- [ ] Run `git diff --check` and the repository hygiene/skill validators relevant to changed docs/code.
- [ ] Re-read the final diff and verify no model threshold, score weight, lifecycle, Anti-Chase, eligibility, notification or live-trading semantic changed.
- [ ] Commit, push and open a dedicated PR from `feat/adaptive-evidence-pipeline-v1-20260902`.
- [ ] Require exact-head CI: backend, frontend, dependency-audit, container-validation, repository-hygiene; inspect CodeQL/Sonar and trigger CodeRabbit if automatic review is skipped.
- [ ] After merge, rebuild release recovery evidence if required by the deployment runbook, dispatch the official exact-artifact Production workflow, and verify revision/health/signal-only safety.
- [ ] Soak Production across multiple deadline windows and record p50/p95/max analysis age by state, evaluation latency, source attempts, WS/cache hit ratios, RSS, restart count and OOM events.
- [ ] If global usable p95 does not hold `<180s`, execute Conditional Wave B rather than lowering strategy thresholds.
