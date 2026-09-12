# WaterfallHunter Mission Control & Continuity Protocol v1

## Status

Approved design for Phase -1 of `WFH-ME-V3-20260902`.

## Goal

Make WaterfallHunter work resumable without conversational memory. A new ChatGPT chat, ChatGPT Work context, Codex session, or other authorized agent surface must be able to identify the TWFH project, locate the active mission, verify the latest durable checkpoint, state exactly where work stopped, and name the next safe action.

The canonical Persian resume intent is:

`ادامه کار گروهی`

Within the TWFH ChatGPT Project, repository, or a Codex workspace rooted in `cavack/wfh`, that phrase means:

`TWFH -> cavack/wfh -> active mission -> latest certified checkpoint -> exact next action`

It must never mean “restart a general WaterfallHunter audit.”

## Core principle

**Chat is disposable. GitHub and runtime mission artifacts are durable memory.**

No material mission state may exist only in a transcript.

## Scope

This protocol governs continuity and orchestration metadata only. It does not change ScoreV2, lifecycle, eligibility, Anti-Chase, market evidence semantics, leverage, trade planning, signal delivery, or Production behavior.

Current repository policy remains signal-only with live order placement forbidden.

## Sources of truth

Use three layers with distinct authority:

1. **Repository contract plane** — static protocol, schemas, CLI, tests, `AGENTS.md`, Project Source overlay.
2. **GitHub control plane** — active-mission pointer, mission issue, immutable checkpoint comments, current repository/PR/CI truth.
3. **Host evidence plane** — full mission state, runtime evidence references, branch/worktree registry, large artifacts and logs.

A summary never overrides exact Git or runtime evidence.

## Active mission identity

The first mission using this protocol is:

- project: `TWFH`
- repository: `cavack/wfh`
- mission id: `WFH-ME-V3-20260902`
- mission name: `Model Excellence v3`
- continuity contract: `wfh_mission_continuity_v1`

Future missions may replace the active mission through the pointer contract, without changing the meaning of `ادامه کار گروهی`: it always resolves the active TWFH mission.

## Resume intent contract

The resume phrase is a control intent, not free-form prose. Consumers must Unicode-normalize and collapse whitespace before comparison. Exact canonical value remains `ادامه کار گروهی`.

On detection, an agent must:

1. identify the repository/project as TWFH / `cavack/wfh`;
2. read the static resume protocol before doing domain work;
3. resolve the active mission pointer;
4. load the latest checkpoint metadata;
5. verify checkpoint integrity;
6. reconcile current `main`, branch/worktree and Production provenance where capabilities permit;
7. classify drift explicitly;
8. return one deterministic resume disposition and exact next action;
9. continue only after required preconditions are satisfied.

It must not ask the user to restate prior work when durable state is available.

## Resume dispositions

Allowed top-level dispositions:

- `RESUME_READY` — checkpoint is internally valid and current prerequisites are satisfied.
- `RECONCILIATION_REQUIRED` — work stopped mid-step or a moving external fact must be reconciled before retry/continue.
- `DRIFT_DETECTED` — repository, Production, branch/worktree, mission pointer, or scientific state differs from checkpoint expectations.
- `RESUME_BLOCKED` — a required control/evidence source or authorized capability is unavailable.
- `MISSION_COMPLETE` — mission terminal state is already certified.

No other state may be silently mapped to `RESUME_READY`.

## Mission state

The host control directory is:

`/srv/waterfallhunter/research/mission-control/<mission_id>/`

An active pointer lives at:

`/srv/waterfallhunter/research/mission-control/ACTIVE_MISSION.json`

The mission directory contains:

- `MISSION_STATE.json`
- `TASK_GRAPH.json`
- `EVIDENCE_LEDGER.json`
- `DECISION_LOG.jsonl`
- `BRANCH_REGISTRY.json`
- `SCIENTIFIC_STATE.json`
- `STEP_JOURNAL.json`
- `LATEST_CHECKPOINT.json`
- `checkpoints/`
- `artifacts/`

Writes to state/pointer/checkpoint files must be atomic.

## Mission state required fields

`MISSION_STATE.json` records at minimum:

- contract version, canonical project/repository identity, mission id and mission name;
- charter hash;
- current phase/task/subtask;
- baseline/current `main` SHA and Production SHA;
- active PR, branch, branch-head, worktree and worktree-cleanliness references;
- completed/in-progress/blocked/deferred tasks;
- latest verified facts and open defect/hypothesis/proposal IDs;
- scientific, runtime and release summaries;
- exact `next_action`, owner and structured preconditions; supported preconditions are validated state equality or independently observed equality, and anything unsupported/unverifiable fails closed;
- required capabilities plus a per-capability minimum authorization (`AVAILABLE`, `AUTHORIZED_READ`, or `AUTHORIZED_WRITE`);

Authorization strength is ordered `AVAILABLE < AUTHORIZED_READ < AUTHORIZED_WRITE`; an observed state satisfies a requirement only at the same or a stronger level.
- last checkpoint id/hash/time;
- updated timestamp.

Secrets, credentials and raw private exchange/account data are forbidden. Mission state is recursively checked for secret-bearing key names before checkpoint serialization.

## Task graph

Tasks form a DAG. Allowed task states:

`NOT_STARTED | READY | IN_PROGRESS | BLOCKED | VERIFYING | COMPLETE | SUPERSEDED | REJECTED`

Every task has:

- stable `task_id`;
- parent ids/dependencies;
- owner role;
- scope;
- acceptance/verification requirement;
- current state;
- exact next action when not terminal.

Task ids must be unique, every parent must exist, and the dependency graph must be acyclic. A child cannot become `COMPLETE` without a parent-consumable handoff record.

## Workstream bound

At most three independent workstreams may be `IN_PROGRESS` simultaneously by default. A fourth requires one existing workstream to become terminal/blocked/deferred or an explicit mission-control policy change.

This prevents branch/worktree explosion and context mixing.

## Branch/worktree registry

Each mission branch/worktree record contains:

- task id;
- branch name;
- worktree path or environment identifier;
- parent SHA;
- current SHA;
- purpose;
- owned file/semantic scope;
- owner;
- state;
- PR number when applicable;
- merge SHA when applicable.

No unregistered mission branch is considered authoritative mission state. The registry must contain exactly one record matching the active branch/worktree, and that record's `current_sha` must equal `MISSION_STATE.active_branch_head`.

## Step interruption protocol

Potentially long or mutating steps must be journaled before execution.

A step-start record contains:

- task id;
- step id;
- command/action description;
- expected artifact/state change;
- pre-step SHA;
- required capabilities;
- retry policy;
- reconciliation procedure.

If the session dies after step start and before completion, the next resume disposition is `RECONCILIATION_REQUIRED`, not automatic retry. The agent checks process state, Git state, created artifacts and external side effects first.

This is the primary protection against abrupt Codex/context-limit termination.

## Checkpoints

Checkpoints are event-driven, not token-limit-driven.

Mandatory checkpoint boundaries include:

- start/end of a material task;
- reproduced defect;
- fix implementation;
- material commit;
- before/after PR or merge;
- before/after deploy;
- before opening scientific holdout;
- scientific phase transition;
- `main` or Production revision change;
- child task creation/closure;
- long-running operation start/completion;
- detected drift/blocker.

Each checkpoint has a machine-readable JSON document and a concise `RESUME.md` projection.

## Checkpoint integrity

Checkpoint payloads are canonical-JSON serialized and SHA-256 hashed. `LATEST_CHECKPOINT.json` stores checkpoint id, relative path and expected hash.

Resume fails closed if:

- the target is missing;
- the hash does not match;
- the mission id differs;
- checkpoint sequence regresses;
- any contract-required durable state file is absent or invalid (`TASK_GRAPH.json`, `EVIDENCE_LEDGER.json`, `BRANCH_REGISTRY.json`, `SCIENTIFIC_STATE.json`, `STEP_JOURNAL.json`, or `DECISION_LOG.jsonl`);
- branch/worktree authority disagrees with mission state.

## Human resume projection

`RESUME.md` must contain only high-value continuation information:

- mission/checkpoint id;
- main and Production SHAs;
- current phase/task;
- completed and do-not-repeat items;
- open defects/blockers;
- active branch/worktree/PR;
- last known in-progress operation;
- exact next action;
- preconditions;
- explicit “do not” constraints.

It is a projection, not authority.

## Evidence ledger

Every material finding receives a stable id and one classification:

`VERIFIED_FACT | REPRODUCED_DEFECT | INFERENCE | DEBT | PROPOSAL`

A record includes evidence references, source SHA, Production SHA when relevant, observation time, validity scope, status and supersession links.

Historical evidence never becomes current fact merely because it is copied into a checkpoint.

## Decision log

Decisions are separate from evidence. Each decision records:

- decision id;
- evidence ids used;
- alternatives considered;
- chosen action;
- reason;
- reversibility;
- owner;
- timestamp;
- supersession link.

## Scientific state lock

`SCIENTIFIC_STATE.json` records dataset/config hashes and whether development, calibration or final holdout material has been opened.

Once final holdout is recorded as opened, ordinary mission update commands cannot set it back to unopened. A contaminated holdout is retired, never reset.

## Do-not-repeat and open-question registries

Mission state carries explicit `do_not_repeat` and `open_questions` ids.

Completed investigations may be reopened only when new current evidence contradicts their validity scope. The reason for reopening must be recorded.

## Drift guard

Resume evaluation holds the mission lock across checkpoint loading, live-journal/state-file checks, observations, capability authorization, structured preconditions and final drift evaluation so a concurrent journal mutation cannot create a false `RESUME_READY`.

Before a material implementation/release/scientific action, the mission controller compares:

- mission id/charter hash;
- expected and actual repository SHA;
- registered branch/worktree;
- active task/scope;
- scientific state;
- protected invariants;
- required capabilities.

Mismatch produces `DRIFT_DETECTED` or `RESUME_BLOCKED`; it is never auto-ignored.

## GitHub control plane

Use two durable issues:

1. stable pointer issue: `[MISSION][POINTER] TWFH Active Mission`;
2. mission issue: `[MISSION] WFH-ME-V3-20260902 — Model Excellence v3`.

The pointer issue exposes the active mission id, mission issue number and latest checkpoint reference. The mission issue title is derived from validated `mission_name`. The mission issue body contains the current compact state; checkpoint transitions are mirrored as immutable comments where GitHub write authorization exists. Remote synchronization writes mission body and immutable checkpoint evidence first, then publishes the pointer issue last as the commit marker.

Mission Control has one authoritative writable control root on the canonical host. ChatGPT/Codex/other agent surfaces may invoke it concurrently, but they serialize through the same mission-scoped filesystem lock. Independent multi-host GitHub writers are not supported by this contract; adding them requires a separate remote concurrency/lease design rather than pretending the host-local lock is distributed.

GitHub state must not contain secrets or bulky raw evidence.

If GitHub write capability is unavailable, checkpoint remains valid locally and remote sync state becomes `UNAVAILABLE`; it must not be falsely reported as synchronized.

## Cross-surface persistence

### ChatGPT Project / Chat / Work

Project Instructions and the lightweight Project Source overlay contain the resume-intent contract and direct the agent to GitHub/host durable state rather than transcript memory.

### Codex

A root `AGENTS.md` is a short table of contents, not a second manual. It maps `ادامه کار گروهی` to the canonical resume protocol and commands Codex to run the resume guard before repository work. Deeper details remain in `docs/mission-control/`.

### Other agent surfaces

Any surface that can read repository instructions must follow the same tracked protocol. A surface without GitHub/host access may report the exact missing capability but may not reconstruct state from guesses.

## Context-budget protocol

- keep raw logs and large datasets outside chat;
- checkpoint artifact references/hashes instead of content dumps;
- load only the active phase/task working set;
- query completed findings from ledgers only when dependency requires them;
- never rely on transcript summaries as the sole copy of a fact;
- checkpoint before high-context transitions.

## Failure recovery matrix

- chat/context limit -> latest checkpoint, new context;
- abrupt Codex stop -> reconcile journaled in-progress step;
- RCD unavailable -> GitHub control state, runtime facts `UNAVAILABLE`;
- GitHub unavailable -> local host checkpoint, repository writes blocked if remote truth required;
- `main` changed -> `DRIFT_DETECTED`, refresh dependent evidence;
- Production changed -> runtime baseline invalidated until reverified;
- branch conflict -> registry/parent SHA reconciliation;
- interrupted test -> task remains `VERIFYING`;
- interrupted deploy -> release-recovery route, never blind retry;
- corrupt checkpoint -> `RESUME_BLOCKED`;
- scientific leakage -> invalidate experiment/retire holdout;
- unexpected subproblem -> bounded child task plus handoff.

## Continuity certification

Model Excellence v3 cannot start until Phase -1 reaches `CONTINUITY_CERTIFIED`.

Certification requires:

- static contract and schemas;
- deterministic CLI validation;
- atomic/checksummed checkpoints;
- task DAG/workstream bound;
- branch/worktree registry;
- evidence/decision/scientific state contracts;
- exact resume intent across Project Sources and Codex instructions;
- GitHub pointer/mission mirror;
- cold-resume test from a fresh process/context;
- interruption/reconciliation test;
- drift/corruption/capability-unavailable negative tests;
- exact-artifact regression verification.

If any required gate fails, terminal Phase -1 status is `CONTINUITY_NOT_CERTIFIED` until corrected.

## Safety and authority

Mission Control cannot authorize domain/model/Production changes. It records and routes them.

Only the existing owning WaterfallHunter skills may authorize their semantic boundaries, and only `release-production-certification` may issue Production readiness states.

`LIVE_TRADING_ENABLED=false` and no real-order policy remain outside Mission Control modification authority.
