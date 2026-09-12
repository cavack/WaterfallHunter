# TWFH Resume Contract

Project: `TWFH`
Repository: `cavack/WFH-ORG`
Protocol: `wfh_mission_continuity_v1`
Council route: `mission_continuity`
Canonical phrase: `ادامه کار گروهی`

Canonical mapping: `phrase=ادامه کار گروهی | project=TWFH | repository=cavack/WFH-ORG | protocol=wfh_mission_continuity_v1 | route=mission_continuity`

When this phrase is used in the TWFH ChatGPT Project, Chat, Work, Codex, or another authorized project-aware agent surface, resolve:

`TWFH -> cavack/WFH-ORG -> active mission -> latest certified checkpoint -> exact next action`

Required behavior:
1. Do not restart from zero.
2. Do not rely on transcript memory as the source of truth.
3. Resolve the active mission and latest checkpoint from durable mission control.
4. Verify checkpoint integrity and supply independently observed values for every checkpointed repository/Production/branch/head/worktree/cleanliness field required by the active task. Never reuse the expected checkpoint value as the observation.
5. If work stopped mid-step, return `RECONCILIATION_REQUIRED` and reconcile side effects before retrying.
6. If state drifted, return `DRIFT_DETECTED`; do not silently reuse stale evidence.
7. If a required capability, control source, or checkpointed observation is unavailable, return `RESUME_BLOCKED` with the exact missing prerequisite; absence is not permission to skip a drift check. Every observed capability must meet or exceed the checkpointed minimum authorization; tool presence alone is insufficient.
8. Evaluate every structured precondition from checkpointed state or independently supplied observations. Unsupported or unverifiable structured preconditions fail closed as `RESUME_BLOCKED`.
9. If valid, return `RESUME_READY` and continue from the checkpoint's exact `next_action`.

Current initial active mission contract: `WFH-ME-V3-20260902` (`WaterfallHunter Model Excellence v3`). The active-mission pointer may later select a successor without changing the meaning of the resume phrase.

Mission Control never authorizes model/Production changes by itself. Existing WaterfallHunter skill ownership, scientific gates, release certification, `LIVE_TRADING_ENABLED=false`, and no-live-order policy remain authoritative.
