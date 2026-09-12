# Runtime Memory Ownership Stabilization Design

## Context

Target baseline: `3e88df0014c30a8aae656a78cc949689174a631f` on protected `main`.
Working branch: `fix/runtime-memory-ownership-20260906`.
Production incident evidence was captured before containment at `/srv/waterfallhunter/runtime/incidents/20260906T032224Z-runtime-memory`.

`VERIFIED_FACT`: Production reached about 1.706 GiB of its 2 GiB backend limit and a health probe timed out before containment restart.
`VERIFIED_FACT`: the deployed backend, frontend, and watchdog artifacts were revision-labelled with the exact current `main` SHA.
`VERIFIED_FACT`: shared-evidence generation count rose rapidly while RSS rose; the observed pre-restart RSS/generation correlation was about 0.84, which is correlation, not causation.
`VERIFIED_FACT`: PR #126 permits a timed-out shared exchange close to continue in the background while a replacement generation starts.
`VERIFIED_FACT`: the original P0 ownership decision record specified close-before-open and no overlapping old/new transport generations.
`VERIFIED_FACT`: isolated real-CCXT Binance churn across 12 immutable generations plateaued at 3 clients / 18 subscriptions and about 231 MiB RSS; generation churn alone did not reproduce Production growth.
`VERIFIED_FACT`: a forced PR #126 abandonment probe across 15 cycles also plateaued after transient warm-up; every sampled abandoned-close task completed. PR #126 is therefore not established as the Production memory root cause.
`REPRODUCED_DEFECT`: Production logged `WebSocket exchange close failed for bybit:TRIA/USDT:USDT (bybit): TimeoutError` followed by an `Unclosed client session`. The generic liquidation/direct exchange retirement path drops ownership after a bounded close timeout and has no retry owner.
`VERIFIED_FACT`: a 24-symbol fixed-membership real-provider probe reached about 562-565 MiB RSS and about 130-132 MiB traced heap over its final 30 sampled seconds (60-90s), rather than continuing the earlier warm-up slope; the process later exited 137, so this is bounded sampling evidence rather than a clean-completion soak.
`VERIFIED_FACT`: controlled real-provider `cross_check_symbol` batches at concurrency 1/3/6/12 completed without errors and showed peak/post-GC RSS deltas of about 12/14/16/18 MiB, not the Production-scale growth pattern.
`INFERENCE`: neither shared-generation churn, the PR #126 abandon path, fixed-membership ingestion, nor bounded cross-check concurrency has reproduced the Production RSS growth pattern in isolation. No additional memory-behavior code change is justified on this branch without a new causal reproduction.
`REPRODUCED_DEFECT`: Production on CCXT 4.5.74 emitted three MEXC `watch_swap_public was never awaited` runtime warnings. The same WFH lifecycle probe on CCXT 4.5.77 emitted zero such warnings; upstream changed those call sites to `self.spawn(...)`. This dependency defect is handled separately from the close-ownership branch.

## Protected invariants

- `LIVE_TRADING_ENABLED=false` remains mandatory.
- No live-order placement is designed, authorized, or enabled.
- No ScoreV2 weights, lifecycle transitions, eligibility boundaries, Anti-Chase semantics, ranking, provenance, persistence-before-notification ordering, or scientific-validation rules change.
- Missing market evidence remains `UNAVAILABLE`.
- Backend remains the canonical owner of decision/ranking semantics.

## Selected architecture

1. Keep immutable shared-evidence transport generations. Do not reintroduce dynamic multi-symbol CCXT unwatch/watch mutation.
2. Do not change shared generation replacement semantics merely from RSS/generation correlation. Controlled real-provider probes must justify any removal-only coalescing or debounce change first.
3. Repair the reproduced generic exchange-close ownership defect: create one close coroutine, await it through `asyncio.shield()` under the normal close deadline, and if that deadline expires retain ownership of that same close task until it actually completes. A replacement liquidation exchange for the same symbol waits for the prior retirement; runtime does not overlap old/new instances merely to recover availability. Shutdown remains separately bounded and may cancel unfinished finalizers when the process itself is terminating.
4. Track timed-out-but-still-owned close tasks explicitly with additive runtime diagnostics and Prometheus metrics. The cumulative timeout count remains observable; arbitrary non-timeout close failures retain the repository's existing log-and-continue shutdown behavior and are not broadened without a reproduced defect.
5. Treat process-local cache changes as conditional. Existing periodic TTL pruning already bounds stale orderbook/ticker/trade/liquidation state; immediate owner-loss purge may be added only if the fixed-membership stream probe shows material retained cache pressure.
6. Treat `_ingest_trades` allocation behavior as a hypothesis. It currently deep-copies the existing bounded trade cache plus each incoming payload; change it only if fixed-membership tracing reproduces sustained allocation/RSS growth attributable to this path.
7. Preserve direct-client ownership cleanup from PR #125 and PR #126 shared latest-wins availability behavior unless a RED regression proves a narrower correction is required.

## Root-cause proof requirements

Before production code changes, reproduce the relevant failure mode with a regression or controlled probe. The investigation must distinguish:
- generation churn that only correlates with time from churn that causes retained object/RSS growth;
- transient abandoned-close overlap from permanently retained transports;
- bounded evidence caches from ownerless cache keys/rows;
- high-water allocator behavior from a true reachable-object leak.

A fix may be narrowed if reproduction disproves one of the proposed contributors. No speculative change is required merely because it appears in this design.

## Observability contract

`runtime_diagnostics()` remains the canonical source for WebSocket ownership counts. New fields must be additive and exposed through Prometheus without changing existing metric names. Alerts should target persistent ownership failures, not short expected retirement transients.

## Verification

Required evidence, proportional to this concurrency/memory incident:
- preserve RED evidence for every reproduced ownership defect;
- focused shared/direct WebSocket and lifecycle tests;
- metrics and Prometheus alert regressions;
- full backend suite on the exact changed SHA;
- repository hygiene, WaterfallHunter skill validation, runtime parity, compile/diff checks, and Prometheus rule validation;
- exact Production-image real-provider Binance/Bybit churn with client/subscription/FD/RSS samples;
- exact-head GitHub CI and review reconciliation;
- official artifact deployment only after release gates pass;
- post-deploy soak longer than the prior failure-growth window with RSS slope, client ownership, backlog, freshness, health, and restart/OOM state observed.

## Separate dependency streams

PR #127 remains a separate change stream. Its dependency-lock drift and native test-interpreter corrections must not be bundled into this runtime branch. After the runtime branch is resolved, rebase/reconcile #127 against current `main`, verify its exact head, and release it independently unless the final release authority explicitly batches compatible artifacts.

PR #93 (`ccxt` 4.5.74 -> 4.5.77) is also separate. The MEXC coroutine-warning reproduction gives concrete runtime justification to evaluate that upgrade, but it still requires lock consistency, exact-head CI, dependency/security review, and real-provider regression before merge/release.

## Success criteria

The runtime correction is successful only if the reproduced failure no longer occurs and resource ownership is bounded by construction and telemetry. A restart or temporary low RSS is containment, not closure. `PRODUCTION_VERIFIED` is reserved for release certification after exact artifact identity, health/smoke, freshness, and risk-proportional soak all pass.
