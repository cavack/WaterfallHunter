# P0 WebSocket/OOM Root-Solution Decision Record

Target repository SHA before implementation: `0594d7b3395fdacaa62d7ec63dbf09dfeedef00b`.
Production revision at RCA: the same SHA. This record is intentionally written before runtime code changes.

## Evidence classification

- `VERIFIED_FACT`: Production backend is capped at 2 GiB and kernel OOM-killed uvicorn at 2026-09-05 16:02:30 UTC with anon RSS 2,039,312 kB.
- `VERIFIED_FACT`: post-restart Production continued with high CPU/RSS, persistent hunter due backlog around 129-134, 12 in-flight evaluations, and dynamic CCXT client/subscription counts.
- `REPRODUCED_DEFECT`: real CCXT 4.5.74 Binance shared-evidence churn grows 3/12 -> 9/24 -> 15/36 -> 21/48 clients/subscriptions for four logical symbols.
- `REPRODUCED_DEFECT`: calling only Binance `un_watch_*` for the retired symbol grows clients from 3 to 6 before the replacement watch is issued.
- `VERIFIED_FACT`: fixed membership repeated watches plateau at 3 clients / 12 subscriptions on Binance; close-before-open generations return clients/subscriptions to zero before the next generation.
- `VERIFIED_FACT`: CCXT Binance derives stream ownership from a membership-dependent `streamHash`; both watch and unwatch route through `watch_multiple()` and can select/create different internal WebSocket clients.
- `VERIFIED_FACT`: Production logs contain repeated `Timed out retiring shared evidence tasks` for Binance and Bybit.
- `DEBT`: current WFH generation-threshold recycle is a bounded mitigation only at recycle boundaries, not an ownership invariant between them.

## Root cause

WFH owns one logical `shared_evidence_exchanges[venue]` object and three shared task kinds, but does not own or bound the CCXT Pro clients created inside that exchange object. The implementation assumes that `un_watch_*ForSymbols` retires subscriptions on the transport that owns the corresponding watch. That assumption is false for current Binance CCXT Pro behavior: membership-dependent hashes can route unsubscribe and replacement watches to new internal clients. Dynamic membership therefore creates transport generations that remain reachable from `exchange.clients` until a full `exchange.close()`.
## Why prior fixes were insufficient

Direct-stream retirement fixes correctly serialized WFH-owned direct clients. Shared-evidence cache purging and partial unwatch fixes remove WFH/CCXT symbol cache entries, but they cannot guarantee transport retirement because CCXT may create another client to issue the unsubscribe. The generation threshold eventually calls `exchange.close()` and resets the fan-out, which explains periodic plateaus, but it allows unbounded growth up to each recycle and can overlap generations when task retirement times out.

A speculative local patch that attempted `unwatch full active set + close zero-subscription clients` was quarantined before implementation because the real-CCXT reproduction falsified its premise: the unwatch itself can create a new client. It is preserved only as `.work/pre-rca-speculative-diff.patch` and is not part of the branch diff.

## Ownership defect

The ambiguous resource is the CCXT Pro internal WebSocket client/subscription generation. WFH has no stable mapping from a logical symbol membership to the specific internal client that owns it, yet lifecycle transitions directly drive dynamic multi-symbol watch/unwatch calls. Ownership can also move shared -> direct -> shared while old shared tasks are still retiring. Therefore the current design cannot state or enforce a transport bound under churn.

## Options considered

- Option A, WFH refcounts over current dynamic CCXT multi-symbol calls: rejected as the root fix. Refcounts cannot retire a hidden CCXT client reliably when `un_watch_*` may allocate a different client.
- Option B, one long-lived exchange object per venue/stream kind with dynamic desired subscriptions: insufficient without controlling CCXT internals; the current API can still fan out clients by membership hash.
- Option C, fixed membership per transport generation plus close-before-open replacement: selected as the smallest root-safe transport model.
- Option D, fully separate market-data acquisition from candidate lifecycle: architecturally strongest long-term, but broader than required. This incident adopts its ownership principle only: lifecycle mutates desired membership; a transport reconciler alone creates/retires CCXT resources.

## Selected architecture

For each shared-evidence venue, introduce one single-flight desired-state reconciler. The lifecycle path may only mutate the desired symbol set. The reconciler owns exactly one immutable transport generation at a time. A generation contains one CCXT Pro exchange instance and the three shared consumer tasks for orderbook, trades, and ticker using one fixed membership snapshot.
On any desired-membership change, the reconciler performs a generation replacement, not dynamic CCXT unwatch/watch mutation:

1. mark the current generation retiring and stop new acquisitions against it;
2. cancel its three consumer tasks and await their settlement under a bounded deadline;
3. call and await `exchange.close()`;
4. verify the retired exchange exposes zero internal clients/subscriptions when introspection is available;
5. only after retirement succeeds, construct a fresh exchange and start three consumers using the latest desired membership snapshot;
6. if retirement cannot be proven, do **not** create a replacement generation; shared evidence for that venue remains unavailable and normal REST fallback semantics apply;
7. membership changes arriving during reconciliation only replace the pending desired set; they do not create parallel transport generations.

No `un_watch_*ForSymbols` call is required for ordinary shared-membership churn. Full exchange close is the ownership boundary.

## Resource bounds by construction

Per supported shared-evidence venue:

- WFH shared exchange objects: `<= 1` active generation;
- WFH shared consumer tasks: `<= 3` active, one per stream kind;
- WFH shared reconcile task: `<= 1`;
- overlapping old/new exchange generations: `0` by policy;
- logical shared symbols: `<= shared_evidence_symbol_limit` (currently 64);
- CCXT clients: provider-dependent but bounded by one fixed generation; real Binance evidence is 3 clients for 3 stream kinds and real Bybit evidence is 1 client for the tested fixed set;
- subscriptions: bounded by the fixed membership and stream kinds; for the Binance four-symbol reproduction this is 12, with no growth on repeated fixed-set watches;
- sockets/FDs: bounded by the one-generation client count, not by churn count.

The implementation must publish per-venue generation, desired-member count, active-member count, reconcile state, internal client/subscription count, and retirement failure metrics so the bound is observable rather than implicit.

## Expected effect and remaining capacity question

Removing transport-generation fan-out should reduce socket/message processing, retained CCXT state, RSS growth, GC pressure, and event-loop/CPU contention. That should improve hunter service time and backlog indirectly. It does not prove that concurrency 12 is sufficient. Current state counts imply an intended due rate of roughly 1.15 evaluations/s (`122/150 + 24/90 + 2/30`) while current cumulative evaluation durations are too slow to sustain that rate. Those durations are contaminated by the active transport incident, so independent capacity work is deferred until the ownership fix is verified under load.
## Risks

- Some providers may not settle cancellation or `exchange.close()` promptly. The design fails closed: no overlapping replacement generation is allowed after an unproven retirement.
- Full generation replacement on every committed membership change can add reconnect cost. Single-flight latest-wins reconciliation prevents concurrent generation storms; post-fix measurements will decide whether further coalescing is justified.
- CCXT internals are provider/version-dependent. Resource assertions therefore combine WFH hard bounds with real-provider churn tests and observability rather than assuming identical client topology across venues.

## RED -> GREEN test plan

Before implementation, add regressions for:

1. membership churn that models the real 3 -> 9 -> 15 -> 21 amplification and asserts it violates the selected one-generation bound on current code;
2. repeated add/remove and replace cycles;
3. multiple symbols changing concurrently;
4. failed retirement/close: replacement generation must not start;
5. reconnect while desired membership changes: latest desired set wins without overlap;
6. cancellation suppression and shutdown;
7. unsupported provider: evidence remains `UNAVAILABLE` and no transport is leaked;
8. lifecycle shared -> direct -> shared transitions without dual ownership;
9. realistic churn soak with resource counts sampled after every cycle;
10. isolated real-CCXT Binance and Bybit tests recording clients, subscriptions, FDs, and RSS after each generation.

Acceptance is a bounded plateau after every cycle, not a periodic reset after threshold recycling. After this runtime fix is GREEN, re-measure hunter arrival rate, service rate, queue wait, lifecycle-specific duration, backlog drain, and candidate freshness before making any scheduler/concurrency change.

## Second recheck amendment — transport retirement semantics

A second real-CCXT review after the initial decision record tested two possible optimizations before implementation was resumed.

- `REPRODUCED_DEFECT`: Binance 4.5.74 watch of `[A,B,C,D]` followed by full unwatch of the same four symbols in reverse order creates a different membership hash; clients rise from 3 to 6 while the original 12 subscriptions remain. Exact membership ordering is therefore part of CCXT transport identity.
- `REPRODUCED_DEFECT`: `exchange.close_ws_clients()` removes live clients/subscriptions but reusing the same Binance exchange object with new membership sets retains `options.streamBySubscriptionsHash` and `options.numSubscriptionsByStream`; the retained routing map grew 3 -> 6 -> 9 -> ... -> 30 entries over ten unique four-symbol generations. Same-object reuse therefore does not provide a churn-independent bound even when sockets are closed.
- `VERIFIED_FACT`: a fresh CCXT exchange object has empty WebSocket routing metadata. After closing the old exchange, `new_exchange.set_markets_from_exchange(old_exchange)` transfers static market metadata without carrying WebSocket client/routing state.
- `VERIFIED_FACT`: real Binance and Bybit tests with 24 symbols across three changing generations, using close-before-open plus fresh exchange objects and `set_markets_from_exchange`, plateaued at Binance 3 clients/72 subscriptions and Bybit 1 client/72 subscriptions for every active generation, returning to zero clients/subscriptions after each close. No market reload was required between generations.

Therefore the earlier chat-level optimization of exact-set unwatch plus same-exchange reuse is **rejected** as a root solution. The canonical decision remains Option C: immutable membership per transport generation, full retirement of the old exchange, and a fresh exchange object before the next generation. The implementation may use CCXT's `set_markets_from_exchange` only as a static-market metadata handoff after retirement; it must never copy or preserve `clients`, subscription maps, stream hashes, stream indices, or other WebSocket routing state.

The correctness fallback remains fail-closed: if consumer settlement or `exchange.close()` cannot be proven, no replacement generation starts. Static market metadata handoff is an optimization, not a prerequisite; if it is unavailable or fails, the fresh generation may perform its ordinary market load rather than weakening transport retirement.

## Implementation verification checkpoint — real CCXT on WFH reconciler

Exact branch checkpoint tested: `4a0820f8bff72ea1bb41512e94791787c8da75ec` plus no runtime source modifications after that checkpoint during the real-provider run. The test mounted the branch backend source into the exact Production-parity image `wfh-release-backend:0594d7b3395fdacaa62d7ec63dbf09dfeedef00b` using CCXT Pro 4.5.74.

Four consecutive 24-symbol shared-evidence generations were exercised through the actual `WebSocketManager.subscribe_shared_evidence()` / `unsubscribe_shared_evidence()` reconciler, requiring a post-generation payload from orderbook, trades, and ticker before each sample.

- `VERIFIED_FACT`: Binance plateaued at 3 clients / 72 subscriptions for all four active generations. Every retired exchange retained 0 clients; the fresh active exchange held exactly 3 current stream-route entries. First-payload readiness was 2.780s, 1.868s, 1.870s, and 2.025s. Final shutdown returned to 0 clients / 0 subscriptions.
- `VERIFIED_FACT`: Bybit plateaued at 1 client / 72 subscriptions for all four active generations. Every retired exchange retained 0 clients. First-payload readiness was 2.688s, 1.519s, 1.516s, and 1.469s. Final shutdown returned to 0 clients / 0 subscriptions.
- `VERIFIED_FACT`: no generation entered the blocked-retirement state in either provider run.
- `VERIFIED_FACT`: process-local RSS stayed near a plateau within each provider run rather than tracking generation count: Binance about 243.4-244.7 MiB, Bybit about 264.9-265.7 MiB over the four samples. This isolated run is supporting evidence only; Production RSS closure still requires the post-deploy soak longer than the original failure window.

This real-provider checkpoint verifies the intended churn-independent transport bound under controlled 24-symbol membership changes. It does not by itself establish `CODE_READY`, merge readiness, or Production verification; broader regression, exact-head CI/review, deployment identity, and Production soak remain mandatory.
