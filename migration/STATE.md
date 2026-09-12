# Migration State

Status: `CLEAN_IMPORT_PREPARED_RUNTIME_CERTIFIED`

Frozen inputs:

- `cavack/wfh@3e88df0014c30a8aae656a78cc949689174a631f` — 519 tracked files.
- `cavack/wfh-dr@add3f01cf3b9f3e55d735294dae99d5a5792b5c2` — 3 tracked files.
- PR #127 head `9b97820b43503cd0ef1d07950cbbad2ea7a79b43`.
- PR #128 head `6db3472980c9d16fe090317324b94f743edaa955`.

Certified reconciliation candidate:

- Runtime candidate content source: local reconciliation commit `1fbd87f220ae5e5e9547493ae0fe929ecaf55760` — 530 tracked files.
- Backend source suite: `1590 passed / 6 skipped / 0 failed`.
- Exact-image backend suite: `1590 passed / 6 skipped / 0 failed`.
- Production-shaped SQLite v9→v10 rehearsal: `MIGRATED_COMPATIBLE`; `quick_check=ok`; `integrity_check=ok`.
- Representative staging soak: due backlog reached and remained `0`; API remained responsive; memory stayed within a bounded high-water envelope with periodic `malloc_trim` recovery; no unclosed session, coroutine warning, orderbook KeyError, traceback, OOM kill, or retirement failure remained.

`migration/source-manifest.json` is being promoted to schema v2. It retains disposition for all 522 frozen source files and separately records every final target path, Git mode, final blob SHA, origin class, rationale, and verification evidence. The manifest file itself is the sole self-hash exception; its containing Git commit provides immutable identity.

Production remains on the legacy release until WFH-ORG clean-tree CI, DR/rollback certification, and explicit cutover gates pass. `LIVE_TRADING_ENABLED=false` remains mandatory.
