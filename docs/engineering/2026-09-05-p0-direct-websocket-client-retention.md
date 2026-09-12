# P0 Direct WebSocket Client-Retention Decision Record

Target and deployed revision at fresh diagnosis: `08cadaf9d3ff2d1637c86edf5e3ea19942dee1d5`.

This is a distinct follow-up defect discovered during Production certification of PR #123. It does not invalidate the immutable shared-evidence generation architecture; it affects the older direct per-venue transport path.

## Evidence classification

- `VERIFIED_FACT`: Production remained on exact clean revision `08cadaf...`, with backend RestartCount `0`, `OOMKilled=false`, and `LIVE_TRADING_ENABLED=false` / `TELEGRAM_SIGNAL_DELIVERY_ENABLED=false` during the investigation.
- `VERIFIED_FACT`: the PR #123 shared-evidence path remained bounded at at most two current shared exchange generations (one per supported venue), with blocked shared retirements returning to zero after retry.
- `VERIFIED_FACT`: Production CCXT client count repeatedly rose and fell while aggregate subscriptions did not track client count one-for-one. The aggregate orphan-client expression was true only in short scrape episodes, which explained the alert moving through pending and later clearing.
- `REPRODUCED_DEFECT`: on the exact running Production image and real CCXT Pro 4.5.74 Binance, keeping one direct symbol alive while retiring four other direct symbols caused client retention `3 -> 6 -> 9 -> 12 -> 15` while the surviving logical subscriptions stayed at `3`.
- `REPRODUCED_DEFECT`: invoking the current `WebSocketManager._close_idle_ccxt_clients()` after each retired symbol did not remove those clients while any other direct task on that venue remained active.
- `VERIFIED_FACT`: each retained client in the isolated reproduction had `subscriptions=0`, `futures=0`, and remained connected. They were not current logical owners.
- `VERIFIED_FACT`: once the last direct task was removed, the current cleanup dropped the retained client set from `15` to the three surviving subscribed clients, then to zero after the survivor was retired.
- `DEBT`: aggregate client/subscription metrics do not distinguish direct, liquidation, and shared-evidence ownership, making Production triage unnecessarily ambiguous.

## Root cause

Direct evidence intentionally shares one CCXT Pro exchange object per venue. A single-symbol Binance orderbook/ticker/trades watch uses distinct internal client routes. When a direct symbol retires, its three `un_watch_*` calls remove the three subscriptions but leave their internal client objects reachable in `exchange.clients`.

The cleanup function then applies a venue-wide guard: if *any* direct task for that venue remains alive, it returns without inspecting the retired clients. Therefore a long-lived direct owner prevents collection of every zero-subscription client produced by other symbol churn. The resource lifetime is incorrectly tied to venue idleness rather than the individual CCXT client's ownership state.

This differs from the shared-evidence defect. Shared evidence required immutable exchange generations because multi-symbol membership hashes could allocate new transports during unwatch itself. Direct single-symbol transports already expose a usable per-client ownership signal after the symbol unwatch completes: the retired client has neither subscriptions nor pending futures.

## Selected correction

Keep the existing one-direct-exchange-per-venue architecture, but make cleanup client-owned instead of venue-idle-owned.

After a direct symbol's cancelled consumers have settled and its three `un_watch_*` operations complete:

1. stay inside the existing per-venue direct lock;
2. inspect every client currently held by that direct exchange;
3. preserve any client with at least one subscription;
4. preserve any client with at least one pending future;
5. close and remove only clients with both `subscriptions == 0` and `futures == 0`;
6. if client close fails or times out, keep the client reference so later retirement can retry and observability can expose it;
7. only remove the exact client identity after successful close.

The ownership predicate is based on current CCXT 4.5.74 behavior: `watch()` / `watch_multiple()` select/create the client and record subscription ownership before waiting for network connection/data. Therefore an active watch that has reached a client is not represented as zero-subscription. A client shape whose subscription/future ownership cannot be inspected is not treated as idle.

Cleanup is executed before releasing the same per-venue lock used by direct-start exchange acquisition. This preserves close-vs-start serialization. If a new direct task obtained the exchange before retirement acquired the lock, its subsequent watch either has not created a client yet or records a subscription on the selected client before yielding to network I/O; the cleanup predicate therefore does not close a current owner.

## Rejected alternatives

- Keep the venue-wide active-task guard and rely on eventual all-idle cleanup: rejected; exact-image real-provider reproduction proves churn-dependent retention and temporary client/socket/RSS amplification.
- Increase backend memory or restart periodically: rejected as containment that does not repair ownership.
- Give every direct symbol a separate full exchange object: rejected for this P0 because it multiplies sessions/static exchange state and broadens lifecycle/close-failure behavior unnecessarily.
- Replace every direct membership change with a whole-venue immutable generation: rejected as broader than needed and would reconnect unrelated PRE/ARMED symbols on every churn event.
- Reuse the shared-evidence full-generation strategy mechanically: rejected because the direct defect has a narrower per-client ownership boundary that is directly observable and independently closable.

## Observability correction

Expose current CCXT ownership separately for:

- direct evidence clients/subscriptions;
- direct idle clients (`subscriptions=0` and `futures=0`);
- liquidation clients/subscriptions (symbol-owned plus shared liquidation);
- shared-evidence clients/subscriptions;
- existing aggregate clients/subscriptions.

Add a persistent alert for direct idle clients rather than relying only on the aggregate clients-vs-subscriptions heuristic. Short transient retirement states may be sampled, so the alert requires persistence before firing.

## RED -> GREEN evidence

Automated RED tests on unmodified runtime behavior established:

- with another direct symbol represented as active, retiring one symbol left all six fake clients reachable instead of removing the three newly idle clients;
- a zero-subscription client with a pending future would have been closed by the older cleanup once the venue became fully idle, demonstrating that ownership must include futures, not subscriptions alone;
- runtime diagnostics lacked transport-family ownership counts.

The implementation must keep these cases GREEN and retain the pre-existing cross-symbol start/retirement race regression.

## Real-provider acceptance

On the corrected source mounted into the exact Production image:

- Binance: one surviving direct symbol established `3 clients / 3 subscriptions`; six other direct symbols were watched and retired sequentially. After every cleanup and a fresh survivor payload, the state remained exactly `3 / 3`, idle-client count `0`; final survivor retirement returned `0 / 0`. FD count did not grow with churn and process max RSS remained flat in the isolated run.
- Bybit: one surviving direct symbol established `1 client / 3 subscriptions`; six other symbols were churned sequentially. Every cycle returned to exactly `1 / 3`, idle-client count `0`, and the survivor continued receiving data; final retirement returned `0 / 0`.

These isolated tests establish the direct ownership correction but do not certify Production. Full regression/CI, official release, exact artifact identity, and a new post-deploy soak remain required before `PRODUCTION_VERIFIED`.

## Protected invariants

No ScoreV2, lifecycle semantics, ENTRY_READY threshold, eligibility, Anti-Chase, ranking, provenance, persistence-before-notification, scientific validation, missing-evidence semantics, or execution policy changes are authorized by this correction. Missing evidence remains `UNAVAILABLE`; live trading and automatic Telegram delivery remain disabled.

## Post-PR #124 reconciliation checkpoint

Before release, `origin/main` advanced to `2761e98870c8f2d59f04ac2cf8bac4e84ca3e1da` via PR #124. The direct-WebSocket fix was preserved in an external checkpoint, rebased cleanly onto that exact main revision, and the only overlapping file (`backend/src/waterfallhunter/main.py`) auto-merged without conflict because PR #124 changed forward-outcome provenance/worker configuration while this fix adds WebSocket ownership metrics.

Fresh verification on the rebased worktree:

- affected WebSocket/lifecycle matrix: `78 passed`;
- full backend suite: `1524 passed`, `1` pre-existing Starlette/httpx deprecation warning;
- WaterfallHunter skill validation: PASS;
- runtime parity: PASS;
- repository hygiene: PASS (`518` tracked files before this new document is committed);
- Python compileall and `git diff --check`: PASS;
- Prometheus rule validation: PASS (`9 rules`);
- exact Production-image real-provider churn recheck: Binance held `3 clients / 3 subscriptions / 0 idle` across six retire cycles with a live survivor, Bybit held `1 / 3 / 0`; both returned `0 / 0 / 0` after final retirement, with no FD or isolated max-RSS growth across the cycles.

These results establish `CODE_READY` for the rebased correction only. PR exact-head CI/review, merge identity, official deployment, and Production soak remain required before any production certification claim.
