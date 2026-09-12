# P0 Shared WebSocket Ownership Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make shared FUEL-RICH CCXT Pro WebSocket evidence transport churn-independent by enforcing one immutable transport generation per venue, close-before-open retirement, and fail-closed replacement.

**Architecture:** Candidate lifecycle mutates only a desired shared-symbol set. A single-flight per-venue reconciler owns transport resources: it cancels and settles the three shared consumers, closes the old CCXT exchange, proves no old clients/subscriptions remain when introspection is available, then starts a fresh exchange with one immutable membership snapshot. Static market metadata may be copied with `set_markets_from_exchange`; WebSocket routing/client state is never reused.

**Tech Stack:** Python 3.13, asyncio, CCXT Pro 4.5.74, FastAPI runtime diagnostics, Prometheus/Alertmanager, pytest, Docker release image parity.

**Spec:** `docs/engineering/2026-09-05-p0-websocket-oom-root-solution.md`

## Global Constraints

- Frozen target/base SHA: `0594d7b3395fdacaa62d7ec63dbf09dfeedef00b`.
- Working branch: `fix/shared-ws-retention-20260905` in `/srv/wfh-worktrees/fix-shared-ws-retention-20260905`.
- Preserve ScoreV2, lifecycle transitions, eligibility, Anti-Chase, provenance, persistence-before-notification, scientific-validation, and production-execution semantics.
- Missing evidence remains `UNAVAILABLE`; no synthetic bullish/bearish substitution.
- `LIVE_TRADING_ENABLED=false`; no live order placement is designed or enabled.
- No Production deployment until exact-head verification and release-production-certification gates pass.
- Shared transport hard bound per supported venue: one exchange generation, at most three shared consumer tasks, one reconciler, zero overlap between retired and replacement transports.

---

### Task 1: Replace obsolete dynamic-unwatch tests with transport-generation contracts

**Files:**
- Modify: `backend/tests/test_shared_websocket_evidence_pool.py`
- No production file changes in this task.

**Interfaces:**
- Consumes current public methods `subscribe_shared_evidence(ex_name, symbol)`, `unsubscribe_shared_evidence(ex_name, symbol)`, `close_all()`.
- Defines modeled CCXT exchanges that expose `clients`, `close()`, and optional `set_markets_from_exchange()` for later production implementation.
- Produces RED contracts for one-generation plateau, close-before-open, latest-wins reconciliation, failed retirement, cancellation suppression, unsupported providers, and static-market handoff.

- [ ] **Step 1: Preserve the existing real-amplification regression**

Keep `test_shared_membership_churn_keeps_one_transport_generation` and its modeled `3/12 -> 9/24 -> ...` behavior. The assertion remains a plateau of `[(3,12), (3,12), ...]`, so current dynamic membership code fails for the original reason.

- [ ] **Step 2: Add RED test for close-before-open and fresh exchange identity**

Add a fake exchange whose `close()` records `closed=True` and clears clients. The replacement factory must assert the previous exchange is already closed before a second transport-capable exchange is started. Expected current-code failure: current implementation mutates membership on one exchange rather than replacing it.

- [ ] **Step 3: Add RED test for static market metadata handoff without WS-state handoff**

Model `set_markets_from_exchange(source)` by copying only `markets` and recording the source object. Assert generation 2 receives generation 1 market metadata, but starts with an empty `clients` dictionary and an empty fake `stream_routes` dictionary. Expected current-code failure: no generation replacement/handoff path exists.

- [ ] **Step 4: Add RED latest-wins concurrency test**

Block the old exchange `close()` on an event, change desired membership twice while retirement is blocked, then release close. Assert exactly one replacement starts and its immutable symbol tuple equals the final desired set. Assert no replacement watch starts before old close completes.

- [ ] **Step 5: Add RED failed-retirement test**

Make old `close()` raise or exceed the bounded close wait. Assert no second exchange starts, `shared_evidence_blocked_exchanges` contains the venue, and logical desired membership remains available for a later retry rather than being silently converted into evidence.

- [ ] **Step 6: Add RED cancellation-suppression test**

Use a fake consumer that catches its first `CancelledError` and remains pending. Assert the replacement transport does not start until the consumer truly settles; if it does not settle within the retirement budget, the venue is blocked.

- [ ] **Step 7: Remove tests that require partial `un_watch_*ForSymbols` as the desired architecture**

Replace `test_shared_membership_change_unwatches_only_retired_symbols`, retry-unwatch tests, and generation-threshold recycle tests with the new generation contracts. Keep cache-purge helper tests only if the helper remains used elsewhere; otherwise remove the dead helper and its test together in Task 2.

- [ ] **Step 8: Run the focused file and verify RED for the intended reasons**

Run:
```bash
docker run --rm \
  -v /srv/wfh-worktrees/fix-shared-ws-retention-20260905/backend:/work/backend:ro \
  -w /work/backend -e PYTHONPATH=/work/backend/src \
  wfh-release-backend:0594d7b3395fdacaa62d7ec63dbf09dfeedef00b \
  python -m pytest -q tests/test_shared_websocket_evidence_pool.py
```
Expected: new generation-ownership tests fail on current dynamic-unwatch behavior; failures must not be syntax/import errors.

- [ ] **Step 9: Commit RED contracts**

```bash
git add backend/tests/test_shared_websocket_evidence_pool.py
git commit -m "test: specify shared websocket generation ownership"
```

---

### Task 2: Implement single-flight immutable shared transport generations

**Files:**
- Modify: `backend/src/waterfallhunter/core/ws_streamer.py`
- Test: `backend/tests/test_shared_websocket_evidence_pool.py`

**Interfaces:**
- `shared_evidence_subscribers: dict[str, set[str]]` is desired logical state only.
- `shared_evidence_active_symbols: dict[str, tuple[str, ...]]` is the immutable active generation snapshot.
- `_shared_evidence_reconcile_tasks: dict[str, asyncio.Task]` permits at most one reconciler per venue.
- `_retire_shared_evidence_generation(ex_name) -> tuple[bool, Any | None]` returns retirement success and the closed prior exchange for optional market metadata handoff.
- `_start_shared_evidence_generation(ex_name, symbols, market_source=None) -> bool` constructs a fresh exchange and starts exactly three consumers.

- [ ] **Step 1: Replace generation-threshold/recycle state with desired/active/reconcile state**

Remove `shared_evidence_client_generation_limit` and membership-generation threshold counters used only for periodic mitigation. Add desired subscribers, immutable active symbols, generation counter, retirement failure counter, blocked venues, one reconcile task and lock per venue.

- [ ] **Step 2: Make shared consumers generation-scoped**

Change `_watch_shared_evidence_stream` to receive `(ex_name, kind, exchange, symbols, generation)`. Every watch call uses the immutable `symbols` tuple. Before ingesting a payload, intersect payload symbols with current desired subscribers and require the same active exchange/generation so logically retired symbols stop contributing immediately even while transport retirement is in progress.

- [ ] **Step 3: Add bounded task retirement**

Cancel only the three shared generation task IDs for the venue, await them under the existing retirement timeout budget, and return failure if any remain pending. Do not start replacement tasks after unproven settlement.

- [ ] **Step 4: Close the old exchange as the ownership boundary**

Await `exchange.close()` under the existing exchange-close timeout. On success, verify `exchange.clients` is empty and total client subscriptions are zero when those attributes exist. On failure or nonzero retained transport state, increment retirement failure telemetry, mark the venue blocked, retain enough state for a retry, and return failure.

- [ ] **Step 5: Start a fresh exchange and hand off only static market metadata**

Construct with `_new_exchange(ex_name)`. If a successfully closed prior exchange has loaded markets and the new exchange exposes `set_markets_from_exchange`, call it before starting consumers. Never copy `clients`, `subscriptions`, `options.streamBySubscriptionsHash`, `options.numSubscriptionsByStream`, stream indices, futures, sockets, or caches. If metadata handoff fails, log it and allow the fresh exchange to load markets normally.

- [ ] **Step 6: Implement latest-wins reconciler**

Within one per-venue lock: snapshot desired; if desired equals active and exactly three consumer tasks exist, stop. If a generation exists and desired differs, retire it first. Re-read desired after retirement. If desired is empty, stop with no transport. Otherwise start one fresh generation. If desired changed during start, loop and reconcile again without creating parallel generations.

- [ ] **Step 7: Make public subscribe/unsubscribe mutate only desired state**

`subscribe_shared_evidence` enforces the existing symbol limit, mutates desired state, and schedules the single-flight reconciler. `unsubscribe_shared_evidence` removes desired membership and schedules reconcile. Neither method calls CCXT `un_watch_*ForSymbols` for shared membership churn.

- [ ] **Step 8: Remove dead dynamic-unwatch/recycle helpers**

Delete helpers whose only purpose was partial membership diff/unwatch or generation-threshold recycling. Keep direct-stream and liquidation retirement logic unchanged.

- [ ] **Step 9: Run focused tests to GREEN**

Run the exact release-image command from Task 1. Expected: all tests in `test_shared_websocket_evidence_pool.py` pass with no unhandled task/future warnings.

- [ ] **Step 10: Commit runtime ownership fix**

```bash
git add backend/src/waterfallhunter/core/ws_streamer.py backend/tests/test_shared_websocket_evidence_pool.py
git commit -m "fix: bound shared websocket transport generations"
```

---

### Task 3: Expose direct recurrence telemetry and alerts

**Files:**
- Modify: `backend/src/waterfallhunter/main.py`
- Modify: Prometheus/Alertmanager rule file returned by repository search for existing `WaterfallWebSocketOrphanSubscriptions`, `WaterfallWebSocketOrphanClients`, and `WaterfallWebSocketRetirementBacklog`.
- Test: existing metrics/alert tests covering those files.

**Interfaces:**
- `WebSocketManager.runtime_diagnostics()` produces desired shared member count, active shared member count, active shared exchange count, reconcile task count, blocked venue count, retirement failure count, and generation count.
- Prometheus gauges mirror current-state quantities. Cumulative retirement failures remain counters/diagnostics, not a permanent firing condition by themselves.

- [ ] **Step 1: Write RED metrics tests**

Assert `/metrics` exports current gauges for shared desired members, active members, exchange instances, reconcile tasks, and blocked venues using deterministic manager diagnostics.

- [ ] **Step 2: Add current-state gauges in `main.py`**

Populate gauges from `runtime_diagnostics()` in the existing WebSocket metrics update path. Do not change API/SSE payload contracts or model decision semantics.

- [ ] **Step 3: Write RED alert test for blocked shared retirement**

Add a rule that fires when blocked shared venues remain nonzero for a sustained window consistent with the repository's existing runtime-alert style. Do not alert permanently from cumulative historical retirement failures alone.

- [ ] **Step 4: Update orphan/backlog alert formulas only as required by new ownership accounting**

Keep orphan detection based on active resource bounds. Include shared reconcile/blocked state in retirement health without weakening direct/liquidation retirement coverage.

- [ ] **Step 5: Run focused metrics/alert tests and commit**

Expected: tests pass and rule syntax validation remains green.

```bash
git add backend/src/waterfallhunter/main.py <resolved-rule-file> <resolved-tests>
git commit -m "obs: expose shared websocket retirement health"
```

---

### Task 4: Verify lifecycle handoff and shutdown boundaries

**Files:**
- Modify only tests unless a reproduced defect requires a minimal runtime correction.
- Test likely `backend/tests/test_websocket_lifecycle_subscription.py` or the current repository-equivalent found by search.

**Interfaces:**
- FUEL-RICH uses shared desired membership.
- PRE-TRIGGER/ARMED uses direct evidence.
- WATCH/removed/source-change retires evidence ownership.
- No model/lifecycle transition rules change.

- [ ] **Step 1: Add RED/characterization test for shared -> direct -> shared**

Drive the existing lifecycle subscription sync with a symbol moving FUEL-RICH -> PRE-TRIGGER -> FUEL-RICH. Assert logical shared membership is removed before direct ownership is accepted and that returning to shared schedules only one shared reconciler/generation.

- [ ] **Step 2: Add shutdown test with an active reconcile**

Start a reconcile with retirement blocked, call `close_all()`, then release the fake blocker. Assert no orphan reconcile task, shared consumer task, or exchange client remains.

- [ ] **Step 3: Run lifecycle/shutdown tests**

Use the exact release image and the smallest affected test files. If current behavior is already correct, preserve it as characterization; if a defect is reproduced, write the failing assertion before the minimal fix.

- [ ] **Step 4: Commit**

```bash
git add backend/tests <runtime-file-only-if-required>
git commit -m "test: cover shared websocket lifecycle handoff"
```

---

### Task 5: Run real-CCXT churn verification on the exact changed source contract

**Files:**
- No Production mutation.
- Optional diagnostic script may be placed under `.work/` and must remain untracked.

**Interfaces:**
- Real CCXT Pro 4.5.74 from the exact release image.
- Binance and Bybit USDT-linear swaps.

- [ ] **Step 1: Run three 24-symbol generations per provider**

For each generation record: active client count, active subscription count, FD count, watch-to-first-payload latency, retirement latency, and post-close counts.

- [ ] **Step 2: Verify the hard plateau**

Expected on current evidence: Binance active `3 clients / 72 subscriptions`, Bybit active `1 / 72`; after every retirement both return to `0 / 0`. A later generation must not inherit prior `streamBySubscriptionsHash` routing metadata because it uses a fresh exchange object.

- [ ] **Step 3: Run rapid desired-membership changes against the WFH reconciler model**

Assert latest-wins and no old/new transport overlap. Record any provider error separately; unavailable provider evidence must not be converted into a pass.

- [ ] **Step 4: Record verification evidence in the branch decision/verification note**

Include exact image revision, CCXT version, providers, symbol count, cycles, resource counts, and latency ranges.

---

### Task 6: Blast-radius regression and exact-artifact verification

**Files:**
- No new behavior unless a regression is reproduced.

- [ ] **Step 1: Run py_compile/type/static gates for changed Python**

Run repository-standard compile/lint/type checks found in CI for the backend.

- [ ] **Step 2: Run focused runtime test matrix**

Include shared pool, direct WebSocket retirement, liquidation streams, lifecycle subscription sync, runtime diagnostics, metrics/alerts, startup/shutdown, and cancellation tests.

- [ ] **Step 3: Run full backend suite in the exact release-parity container**

Record pass/fail/skip counts. Any failing check remains explicit and blocks `CODE_READY`.

- [ ] **Step 4: Re-read final diff**

Confirm no ScoreV2/lifecycle/eligibility/Anti-Chase/provenance/persistence/scientific/live-execution semantics changed and no secrets or generated artifacts entered the diff.

- [ ] **Step 5: Commit verification note if repository practice requires it**

Do not claim merge/deploy readiness yet.

---

### Task 7: PR, exact-head CI/review, merge and production certification

**Files:**
- GitHub PR and release evidence only; Production changes occur only through the official guarded deployment path.

- [ ] **Step 1: Push branch and open/update PR against current `main`**

Record exact PR head SHA and ensure current `main` has not moved. Rebase/reconcile only if required and re-run affected verification after any head change.

- [ ] **Step 2: Require exact-head required checks and review closure**

Required branch checks include `backend`, `frontend`, `dependency-audit`, `container-validation`, and `repository-hygiene`; inspect configured CodeRabbit/security/review threads on the exact head.

- [ ] **Step 3: Merge only after MERGE_READY evidence**

Use expected-head SHA protection. Do not treat green CI alone as Production certification.

- [ ] **Step 4: Use release-production-certification for deployment prerequisites**

Verify rollback target, certified backup/restore evidence where required by the official workflow, exact immutable image/revision identity, and mandatory `LIVE_TRADING_ENABLED=false`/signal-only invariants.

- [ ] **Step 5: Deploy the exact merged/approved artifact through the official path**

No ad-hoc Production rebuild after approval.

- [ ] **Step 6: Post-deploy smoke**

Verify `/livez`, `/readyz`, `/healthz`, runtime revision, key API/SSE/dashboard paths, worker progress, data freshness, WebSocket ownership gauges, and alert state.

- [ ] **Step 7: Memory/concurrency soak exceeding the prior failure window**

Soak at least 30 minutes, sampling every 30-60 seconds: backend RSS/limit, CPU, restart/OOM state, FD/socket count, CCXT clients/subscriptions, shared desired/active members, generations, reconcile tasks, blocked venues, retirement failures, hunter in-flight/backlog, analysis freshness, and alerts. Acceptance requires bounded client/subscription counts and no monotonic RSS growth toward the 2 GiB cgroup ceiling.

- [ ] **Step 8: Only release-production-certification may declare final Production state**

If any required soak/smoke/identity evidence is missing, remain `DEPLOYED_UNVERIFIED`; if a failure recurs, return to runtime incident workflow rather than raising memory/concurrency limits.
