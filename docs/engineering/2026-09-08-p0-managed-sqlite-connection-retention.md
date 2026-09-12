# P0 Managed SQLite Connection-Retention Decision Record

Canonical application repository: `cavack/WFH-ORG`.

Production revision at diagnosis remains `2828a59df4722fd8e34bc901261beeac1c631014`, sourced from legacy `cavack/wfh`. Production has not yet received the canonical correction and remains `PRODUCTION_ACTIVE — DEPLOYED_UNVERIFIED`.

Canonical transfer base: `64fc095d08f82a1743c236148b2f7df3848ead7c`.
Canonical corrective code/test commit: `2cb1e3830c006dffdae576ca46cad5fb47186665`.
Supporting historical legacy correction: `cavack/wfh@54deec416917e9128392c2a0503352d58c24272d` / legacy PR `#135`.

## Evidence classification

- `VERIFIED_FACT`: Production remained on exact revision `2828a59...`, backend health stayed available, `RestartCount=0`, `OOMKilled=false`, and the product remained `SIGNAL_ONLY` throughout diagnosis.
- `VERIFIED_FACT`: a synchronized 600-second sample showed private process memory dominating RSS/PSS while cgroup `memory.events:max` increased by `6347`; no OOM kill occurred.
- `VERIFIED_FACT`: total CCXT client ownership remained non-monotonic/bounded during the same investigation, so the old simple WebSocket-client leak does not explain this event.
- `REPRODUCED_DEFECT`: Production FD count reached `233`; a direct classification near the same pressure episode found `113` descriptors for `waterfall_registry.db`, `6` for its WAL and `1` for SHM, while sockets were only `39`.
- `VERIFIED_FACT`: across the 600-second sample, RSS correlated strongly with total FD count (`r≈0.843`) and with non-socket FD count used as a DB-FD proxy (`r≈0.830`).
- `VERIFIED_FACT`: at the existing periodic GC/heap-trim boundary, FD count fell `161 → 47`, heap RSS fell by about `127 MiB`, and process RSS fell about `269 MiB` in one sample interval.
- `REPRODUCED_DEFECT`: on the exact Production backend image, 100 ordinary `with connect_managed_sqlite(...)` operations left `69` SQLite connections/FDs live until explicit cyclic GC; 200 operations with GC disabled retained `200` main DB and `200` WAL descriptors.
- `REPRODUCED_DEFECT`: on exact canonical base `64fc095...`, `test_managed_connection_context_closes_immediately` failed because the connection remained usable after context exit.

## Root cause

`connect_managed_sqlite()` returned a native `sqlite3.Connection`. Python's SQLite connection context manager commits or rolls back but does **not** close the connection when leaving `with`. Many high-frequency WaterfallHunter stores use that factory directly as a context manager.

Those connection objects are cyclic-GC tracked. Between collection cycles they retain SQLite handles, database/WAL descriptors, page-cache/native allocations and Python/native heap state. The hunter's existing five-minute `gc.collect()` plus `malloc_trim(0)` periodically releases much of that retained working set, producing the observed sawtooth rather than deterministic per-operation reclamation. Under the 2 GiB cgroup, the high side of that sawtooth repeatedly forces reclaim and `memory.events:max` increments.

The mechanism is causal rather than inferred from code shape alone: Production shows the DB-FD/RSS correlation and synchronized release; the exact release image reproduces connection retention; and the corrected canonical implementation no longer retains SQLite descriptors under the equivalent controlled workload.

## Selected correction

Keep `connect_managed_sqlite()` API-compatible by returning a `sqlite3.Connection` subclass. Override only context-manager exit: preserve native commit/rollback behavior, then always close in `finally`.

Direct callers that intentionally manage connection lifetime remain compatible because the returned object is still a `sqlite3.Connection`. Existing explicit `close()` paths remain valid. The separate `managed_connection()` wrapper remains supported; removing it would be unrelated cleanup.

This is preferred over periodic forced GC, shorter trim intervals, a higher memory limit, or broad store rewrites. Those alternatives either mask the lifetime bug or expand blast radius without repairing ownership.

## Canonical RED → GREEN evidence

RED on exact WFH-ORG base `64fc095d08f82a1743c236148b2f7df3848ead7c`:

- `test_managed_connection_context_closes_immediately`: `1 failed`, because the connection remained usable after the `with` block.

GREEN on canonical corrective source:

- managed-SQLite regression file: `9 passed`;
- neighboring managed-SQLite/runtime-memory/Feature-Replay/persistence/lifecycle/WebSocket matrix: `117 passed`;
- full canonical backend harness: `1603 passed, 6 skipped`;
- repository hygiene: PASS (`539` tracked files);
- WaterfallHunter skill validation: PASS;
- runtime declaration parity: PASS;
- changed Python compilation: PASS;
- Docker Compose config: PASS;
- `git diff --check`: PASS.

Controlled 200-context FD/WAL reproduction with cyclic GC disabled:

- rows persisted: `200`;
- residual main DB FDs before GC: `0`;
- residual WAL FDs before GC: `0`;
- residual SHM FDs before GC: `0`;
- RSS delta during the workload: `0 KiB` in the sampled process;
- explicit GC collected `0` related objects and was not required to release SQLite descriptors.

## Current status

`ROOT_CAUSE = PROVEN`

`CANONICAL_FIX = IMPLEMENTED / LOCALLY VERIFIED`

`PRODUCTION_FIX = NOT YET DEPLOYED`

`PRODUCTION_VERIFIED = NO`

Exact-head WFH-ORG CI/review, protected merge, exact-main CI, canonical cutover/recovery gates, deployment, and Production soak are still required before Production certification.
