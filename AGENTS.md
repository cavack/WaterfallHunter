# TWFH Agent Entry Contract

This repository is the canonical WaterfallHunter engineering repository `cavack/WFH-ORG` for the ChatGPT Project folder `TWFH`.

## Canonical resume intent

When the user says exactly `ادامه کار گروهی`, treat it as the TWFH active-mission resume intent governed by `wfh_mission_continuity_v1` and Council route `mission_continuity`.

Canonical mapping: `phrase=ادامه کار گروهی | project=TWFH | repository=cavack/WFH-ORG | protocol=wfh_mission_continuity_v1 | route=mission_continuity`

Do not interpret it as “start a WaterfallHunter audit” and do not ask the user to restate previous work when durable mission state is available.

Before repository work:
1. Read `docs/chatgpt-project/00-WFH-CHATGPT-ROUTER-v2.md`.
2. Read `docs/mission-control/README.md` when present.
3. Reconcile `git status --porcelain`, current branch/head, registered worktree, current `origin/main`, required capability authorization, and Production revision when that evidence is available.
4. Collect every checkpointed observation that the current surface can prove: current `origin/main`, Production revision when required, branch head/name, registered worktree path, worktree cleanliness, and observed capability states. Never copy an expected checkpoint value into an observed field. Observed capabilities must meet or exceed the checkpointed minimum authorization; presence alone is insufficient.
5. Run `python3 scripts/wfh_mission.py resume --intent "ادامه کار گروهی" --json` with those `--observed-*` and `--capability NAME=STATE` values. Missing checkpointed observations must remain `RESUME_BLOCKED`, not guessed. Structured preconditions are evaluated from state or supplied observations; unsupported or unverifiable preconditions fail closed as `RESUME_BLOCKED`.
6. A dirty registered worktree or an interrupted journal step requires `RECONCILIATION_REQUIRED`; inspect side effects before retry.
7. Reconcile any `RECONCILIATION_REQUIRED`, `DRIFT_DETECTED`, or `RESUME_BLOCKED` result before continuing.
8. Continue only from the exact `next_action` and required preconditions in the latest valid checkpoint.

## Persistent context

`AGENTS.md` is only the entry map. Durable mission state belongs to GitHub mission control and host mission artifacts, not to this file or the chat transcript.

## Safety

Preserve WaterfallHunter protected invariants and signal-only policy. `LIVE_TRADING_ENABLED=false`; no live order placement is authorized by Mission Control.
