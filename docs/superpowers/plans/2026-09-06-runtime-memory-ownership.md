# Runtime Memory Ownership Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the reproduced WebSocket exchange-close ownership defect and identify/fix the actual cause of Production RSS growth without speculative changes to signal semantics.

**Architecture:** Preserve immutable shared-evidence generations and current signal semantics. First complete causal memory probes; independently make timed-out generic exchange retirement retain/retry ownership until closure, expose that ownership in diagnostics, and only implement an allocation fix if a controlled probe reproduces sustained growth in a specific path.

**Tech Stack:** Python 3.13, asyncio, CCXT/CCXT Pro, FastAPI, Prometheus, pytest, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-06-runtime-memory-ownership-design.md`

## Global Constraints

- `LIVE_TRADING_ENABLED=false` remains mandatory.
- No live-order placement is designed, authorized, or enabled.
- Do not change ScoreV2, lifecycle, eligibility, Anti-Chase, ranking, provenance, persistence-before-notification, scientific-validation, or missing-evidence semantics.
- Missing market evidence remains `UNAVAILABLE`.
- Preserve exact-SHA/artifact provenance and separate PR #127 from this runtime branch.

---
### Task 1: Finish causal memory probes

**Files:**
- No production code changes.
- Evidence: `/tmp/wfh-fixed-stream-probe.log` and incident directory outside the repository.

**Interfaces:**
- Consumes: current `WebSocketManager`, `MultiExchangeValidator`, exact Production image `wfh-release-backend:3e88df...`.
- Produces: evidence classification that either reproduces a specific growth path or rules it out.

- [x] **Step 1:** Run a 24-symbol fixed-membership Binance shared-evidence probe for at least 90 seconds, sampling RSS, tracemalloc, clients/subscriptions, cache keys/rows, and message count every 10 seconds.
- [x] **Step 2:** If fixed-stream RSS grows materially, compare tracemalloc snapshots and attribute the retained/allocation growth to exact source lines before changing code.
- [x] **Step 3:** If fixed-stream RSS plateaus, run controlled `cross_check_symbol` batches at concurrency 1, 3, 6, and 12 and compare peak/post-GC RSS and task completion.
- [x] **Step 4:** Record `REPRODUCED_DEFECT`, `VERIFIED_FACT`, or `INFERENCE` precisely; do not implement any memory change without a reproduced causal path.

### Task 2: Repair timed-out generic exchange retirement

**Files:**
- Modify: `backend/src/waterfallhunter/core/ws_streamer.py`
- Test: `backend/tests/test_liquidation_flow_stream.py`

**Interfaces:**
- Consumes: `_close_exchange_instance(ex_name, stream_id, exchange, cancelled_tasks)` and retirement task dictionaries.
- Produces: tracked close-finalization ownership that does not discard an exchange after the first close timeout.

- [x] **Step 1: Write the RED close-timeout ownership test**

Add a fake exchange whose single `close()` blocks past `exchange_close_timeout_seconds`. Schedule a liquidation retirement and assert that after the timeout the manager still owns that same close task, then release it and assert ownership reaches zero without invoking `close()` twice.

```python
class RetryingCloseExchange:
    def __init__(self):
        self.calls = 0
        self.release = asyncio.Event()
    async def close(self):
        self.calls += 1
        await self.release.wait()
```

- [x] **Step 2: Verify RED**

Run: `PYTHONPATH=backend/src:. pytest -q backend/tests/test_liquidation_flow_stream.py -k close_timeout`
Expected on baseline: FAIL because `_close_exchange_instance()` logs the timeout and returns; no tracked retry owner remains.

- [x] **Step 3: Implement minimal tracked finalization**

Create one manager-owned close task per retirement and await it through `asyncio.shield()` under the normal close timeout. If the wait times out, keep that same task tracked and await its completion; do not cancel/restart `exchange.close()`. Replacement for the same symbol must wait for retirement. Shutdown uses a separate bounded deadline and cancels remaining close tasks only because the process itself is terminating.

- [x] **Step 4: Make shutdown settle tracked finalizers**

`close_all()` must wait for current tracked finalizers under the existing bounded shutdown discipline and clear only settled ownership.

- [x] **Step 5: Verify GREEN and neighboring retirement tests**

Run the RED test plus `backend/tests/test_liquidation_flow_stream.py`, `backend/tests/test_ws_streamer.py`, and `backend/tests/test_shared_websocket_evidence_pool.py`.

- [ ] **Step 6: Commit the independently testable correction**

Commit message: `fix: retain ownership of timed-out websocket closes`.

### Task 3: Expose unfinished close ownership

**Files:**
- Modify: `backend/src/waterfallhunter/core/ws_streamer.py`
- Modify: `backend/src/waterfallhunter/main.py`
- Test: `backend/tests/test_websocket_metrics.py`
- Test: `backend/tests/test_liquidation_flow_stream.py`

**Interfaces:**
- Produces additive `runtime_diagnostics()` keys for active tracked close finalizers and cumulative timed-out close attempts; corresponding Prometheus gauges/counters follow existing WebSocket metric naming.

- [x] **Step 1: Write RED diagnostics/metric assertions**

Extend the diagnostics shape test and metric capture test to require `exchange_close_finalizer_tasks` and `exchange_close_timeouts` values.

- [x] **Step 2: Verify RED**

Run: `PYTHONPATH=backend/src:. pytest -q backend/tests/test_websocket_metrics.py backend/tests/test_liquidation_flow_stream.py -k 'diagnostics or metric or close_timeout'`
Expected: FAIL because the new fields/metrics do not exist.

- [x] **Step 3: Implement additive diagnostics and metrics**

Count only non-done finalizer tasks; keep timeout count cumulative. Existing metric names and semantics remain unchanged.

- [x] **Step 4: Verify GREEN and Prometheus rule compatibility**

Run the two test files and `python scripts/validate_prometheus_rules.py` if present; otherwise use the repository's canonical Prometheus validation command discovered from CI/docs.

- [ ] **Step 5: Commit**

Commit message: `obs: expose websocket close finalizers`.

### Task 4: Conditional memory correction

`NO_CODE_CHANGE_JUSTIFIED` — the controlled probes did not reproduce a causal memory-retention mechanism that warrants an additional production-code change. The only reproduced runtime ownership defect is handled in Task 2; the independent CCXT/MEXC defect is routed to PR #93.

**Files:** determined only by Task 1 causal evidence; likely `backend/src/waterfallhunter/core/ws_streamer.py` plus its focused tests if stream ingestion is reproduced, or scheduler/runtime files plus dedicated tests if concurrent evaluation is reproduced.

- [x] **Step 1:** Write a RED regression that reproduces the exact causal growth/retention mechanism identified in Task 1.
- [x] **Step 2:** Run it against unmodified baseline behavior and preserve the expected failure output.
- [x] **Step 3:** Implement the narrowest correction. Do not lower evaluation concurrency, shrink evidence, alter TTLs, or change lifecycle behavior merely as containment.
- [x] **Step 4:** Re-run the focused RED/GREEN test and the controlled real-provider probe under the same workload.
- [x] **Step 5:** Commit only if the correction is causally supported and materially improves the reproduced condition; otherwise document `NO_CODE_CHANGE_JUSTIFIED` for this task.

### Task 5: Exact-branch regression and release gates

**Files:**
- Modify documentation only if final behavior/evidence changed.

- [x] **Step 1:** Re-read final diff and list changed files plus semantic blast radius.
- [x] **Step 2:** Run focused WebSocket/lifecycle/metrics tests.
- [x] **Step 3:** Run full backend suite on exact branch head.
- [x] **Step 4:** Run WaterfallHunter skill validation, runtime parity, repository hygiene, compileall, `git diff --check`, and Prometheus validation.
- [x] **Step 5:** Run exact Production-image real-provider Binance and Bybit retirement/churn checks, including FD and RSS samples.
- [ ] **Step 6:** Push branch, open PR, verify exact-head required CI, security/dependency evidence, and unresolved review threads.
- [ ] **Step 7:** Merge only after required evidence is green and re-resolve `main` identity.
- [ ] **Step 8:** Use the canonical release workflow and exact CI-tested artifact; preserve rollback target and safety flags.
- [ ] **Step 9:** Verify `/livez`, `/readyz`, `/healthz`, `/api/health`, runtime revision, worker progress/freshness, WebSocket ownership metrics, and alerts.
- [ ] **Step 10:** Soak longer than the prior failure-growth window. Require bounded/plateauing RSS, no persistent idle/unclosed clients, healthy progress, and no OOM/restart before production certification.

### Task 6: Reconcile dependency-validation PR #127 separately

**Files:** no changes on this runtime branch.

- [ ] **Step 1:** After runtime merge, re-fetch PR #127 and compare its head against new `main`.
- [ ] **Step 2:** Rebase/update only if necessary; preserve the lock-consistency and interpreter-harness scope.
- [ ] **Step 3:** Verify exact-head backend/frontend/dependency/hygiene/container checks plus review/security evidence.
- [ ] **Step 4:** Merge/release independently under normal certification; do not claim its current pre-runtime CI as proof for a rebased head.

## Self-review

- Spec coverage: root-cause probes, reproduced close ownership, observability, proportional regression, release soak, and separate #127 stream are covered.
- Placeholder scan: Task 4 is intentionally conditional on causal evidence; it explicitly forbids speculative implementation rather than deferring undefined work.
- Type/name consistency: diagnostic names in Task 3 are the names to use in tests and implementation.
