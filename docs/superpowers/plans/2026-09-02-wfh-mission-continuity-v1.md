# WaterfallHunter Mission Control & Continuity Protocol v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ادامه کار گروهی` a deterministic cross-context resume intent for TWFH and build durable mission/checkpoint state that survives chat or Codex interruption without transcript dependence.

**Architecture:** Keep the repository contract static and small; use a standard-library mission-control engine for atomic local state/checkpoints; mirror compact state to GitHub issues; expose the resume contract through ChatGPT Project Sources and root `AGENTS.md`. Resume fails closed on corruption, drift, incomplete step reconciliation, missing capabilities, or ambiguous active mission state.

**Tech Stack:** Python 3.13 standard library, Git/GitHub CLI for optional remote sync, JSON/Markdown contracts, pytest, existing WaterfallHunter Council/Project Source exporter.

**Spec:** `docs/superpowers/specs/2026-09-02-wfh-mission-continuity-v1.md`

## Global Constraints

- Canonical project: `TWFH` / `cavack/wfh`.
- Canonical resume phrase: `ادامه کار گروهی`.
- Initial mission: `WFH-ME-V3-20260902`.
- Contract: `wfh_mission_continuity_v1`.
- Chat/transcript is never the sole durable mission state.
- Maximum three independent `IN_PROGRESS` workstreams by default.
- Interrupted steps become `RECONCILIATION_REQUIRED`, never blind retry.
- Checkpoints are atomic and SHA-256 content-addressed.
- Tool presence/authorization and remote-sync status remain explicit.
- No ScoreV2/lifecycle/eligibility/Anti-Chase/runtime/Production semantics change.
- `LIVE_TRADING_ENABLED=false`; no live order placement or Telegram send.

---
## File Structure

- Create `scripts/wfh_mission_control.py` — state validation, atomic IO, checkpoints, task/workstream rules, resume disposition.
- Create `scripts/wfh_mission_github.py` — compact GitHub pointer/mission rendering and optional `gh` synchronization.
- Create `scripts/wfh_mission.py` — CLI: `init`, `validate`, `begin-step`, `finish-step`, `checkpoint`, `resume`, `sync-github`.
- Create `docs/mission-control/README.md` and JSON schema documents.
- Create root `AGENTS.md` — short Codex entry map, not a second manual.
- Create `docs/chatgpt-project/TWFH-RESUME.md` — ChatGPT Project/Work resume overlay.
- Modify Council manifest/docs — add `mission_continuity` route and resume-intent mapping.
- Modify Router/Project Instructions/Install guide — resolve the resume intent before ordinary WFH routing.
- Modify Project Source exporter/tests — export and hash `TWFH-RESUME.md`.
- Create mission-control, resume, GitHub-mirror and CLI tests.

### Task 1: Static Resume Contract and Council Route

**Files:** `AGENTS.md`, `docs/chatgpt-project/TWFH-RESUME.md`, `.agents/wfh-council/manifest.json`, `.agents/wfh-council/COUNCIL.md`, Router/Project Instructions, Council/resume-contract tests.

**Produces:** exact mapping `ادامه کار گروهی -> TWFH -> cavack/wfh -> active mission -> latest checkpoint`, route `mission_continuity = chief_orchestrator -> capability_scout -> skill_system_curator -> regression_lead`.

- [ ] Write failing tests asserting phrase/project/repository/protocol/route parity across all static surfaces and that only existing Council roles are used.
- [ ] Run `PYTHONPATH=.:backend/src pytest -q backend/tests/test_wfh_council.py backend/tests/test_wfh_project_resume_contract.py`; expect missing-file/route failures.
- [ ] Add the manifest mapping, route, concise Codex `AGENTS.md`, `TWFH-RESUME.md`, Router and Project Instructions clauses.
- [ ] Run focused tests, `python scripts/wfh_council.py validate --json`, `python scripts/validate_wfh_skills.py`, and `git diff --check`.
- [ ] Commit `feat: define TWFH cross-context resume intent`.

---
### Task 2: Mission State, Validation and Atomic IO Core

**Files:** create `scripts/wfh_mission_control.py`, `backend/tests/test_wfh_mission_control.py`, and schemas for mission state, task graph, branch registry, scientific state.

**Produces:** `normalize_resume_phrase(str)`, `validate_mission_bundle(Path)`, `atomic_write_json(Path, dict)`, `initialize_mission(...)`, workstream/task validation and monotonic scientific-state validation.

- [ ] Write RED tests for missing mission id, malformed SHA, >3 `IN_PROGRESS` workstreams, child `COMPLETE` without handoff, invalid evidence class, and cleanup after simulated atomic-write failure.
- [ ] Run `PYTHONPATH=.:backend/src pytest -q backend/tests/test_wfh_mission_control.py`; confirm RED.
- [ ] Implement explicit state allowlists, confined mission paths, same-directory temp write + flush/fsync + `os.replace`.
- [ ] Add RED→GREEN test proving an opened final holdout cannot be changed back to unopened; retirement is separate.
- [ ] Run focused tests and `git diff --check`; commit `feat: add durable mission state contracts`.

### Task 3: Content-Addressed Checkpoint Engine and Resume Projection

**Files:** modify mission core; create `backend/tests/test_wfh_mission_resume.py` and checkpoint schema.

**Produces:** `create_checkpoint(...)`, `verify_latest_checkpoint(...)`, `render_resume_markdown(...)`, `resume_guard(...)`.

- [ ] RED: create a checkpoint, mutate one byte, assert `RESUME_BLOCKED/checkpoint_hash_mismatch`.
- [ ] RED: initialize/checkpoint, invoke a fresh Python interpreter, assert exact mission/task/next action is recovered without transcript state.
- [ ] Implement canonical JSON hashing, monotonic `CP-000001` ids and `LATEST_CHECKPOINT.json` with id/path/hash/mission id.
- [ ] Implement concise `RESUME.md`: SHAs, phase/task, do-not-repeat, open blockers, active branch/worktree/PR, in-progress operation, exact next action/preconditions and explicit “do not” constraints.
- [ ] Run GREEN and commit `feat: add content-addressed mission checkpoints`.

---
### Task 4: Interrupted-Step Journal, Drift Guard and Resume Disposition

**Files:** modify mission core and resume tests.

**Produces:** `begin_step(...)`, `finish_step(...)`, `classify_resume(...)`.

- [ ] RED abrupt-stop test: journal a command, omit completion, checkpoint, fresh-process resume must return `RECONCILIATION_REQUIRED` with exact command/step and no automatic retry.
- [ ] RED drift tests: observed `main` or Production revision differing from checkpoint must return `DRIFT_DETECTED` and name invalidated scope.
- [ ] Implement journal fields: pre-step SHA, expected effects, capabilities, retry policy, reconciliation procedure and status.
- [ ] Add unavailable-capability negative test: required unavailable evidence/tool returns `RESUME_BLOCKED`, never guessed state.
- [ ] Run GREEN and commit `feat: add interruption-safe resume guard`.

### Task 5: Agent CLI

**Files:** create `scripts/wfh_mission.py` and `backend/tests/test_wfh_mission_cli.py`.

**Produces:** `init`, `validate`, `checkpoint`, `begin-step`, `finish-step`, `resume`; canonical entry is `resume --intent "ادامه کار گروهی" --json`.

- [ ] RED CLI tests: wrong phrase nonzero; canonical phrase JSON; corrupt pointer nonzero; no explicit mission dir resolves configured `ACTIVE_MISSION.json`.
- [ ] Implement argparse CLI; confine all writable mission paths under allowed mission root and reject traversal/symlink escape.
- [ ] Run CLI tests plus `python scripts/wfh_mission.py --help` and a fresh-process smoke.
- [ ] Commit `feat: add TWFH mission resume CLI`.

---
### Task 6: GitHub Durable Control-Plane Mirror

**Files:** create `scripts/wfh_mission_github.py`, `backend/tests/test_wfh_mission_github.py`; modify CLI.

**Produces:** compact pointer/mission body renderers and `sync-github`; uses `gh api` only when explicitly requested and available.

- [ ] RED renderer tests: pointer body includes mission id/mission issue/latest checkpoint/hash; mission body includes task/next action/branch/SHA and excludes secret-like keys.
- [ ] RED unavailable-`gh` test: remote sync becomes `UNAVAILABLE` while local checkpoint stays valid.
- [ ] Implement stable hidden machine marker plus human summary; issue updates are idempotent and checkpoint comments immutable.
- [ ] Implement `sync-github` with explicit issue numbers and no implicit Production/repository mutation authority.
- [ ] Run GREEN and commit `feat: mirror mission checkpoints to GitHub control plane`.

### Task 7: Project Source Export and Cross-Surface Contract

**Files:** modify Project Source exporter/tests/install guide/checked-in manifest.

**Produces:** export bundle includes `TWFH-RESUME.md` and manifest hash/provenance covers it.

- [ ] RED exporter tests: add `TWFH-RESUME.md` to expected files and require install guide cold-resume test with `ادامه کار گروهی`.
- [ ] Refactor repetitive exporter copy/hash blocks into iteration over `OVERLAY_FILES` without weakening fixed destination/path confinement.
- [ ] Regenerate tracked manifest semantics with the added overlay; preserve source SHA/ref/dirty provenance and no Skill body duplication.
- [ ] Run Project Source, Council and Skill suites; export twice and compare byte-for-byte for determinism.
- [ ] Commit `feat: export TWFH resume contract to ChatGPT Project Sources`.

---
### Task 8: Bootstrap Active Mission and GitHub Anchors

**Files:** runtime only under `/srv/waterfallhunter/research/mission-control/`; GitHub issues only.

**Produces:** active mission `WFH-ME-V3-20260902`, local pointer, mission state/DAG/registry and initial checkpoint; stable GitHub pointer issue `[MISSION][POINTER] TWFH Active Mission` and mission issue `[MISSION] WFH-ME-V3-20260902 — Model Excellence v3`.

- [ ] Re-resolve current `origin/main`, Production revision, active PRs and branch/worktree state immediately before bootstrap.
- [ ] Initialize host mission state and DAG; mark accepted Council v2/current completed work in `do_not_repeat`; register this continuity worktree as Phase -1.
- [ ] Create or find stable pointer/mission GitHub issues; store issue numbers in mission control state.
- [ ] Create/sync `CP-000001`; verify local and GitHub-facing summaries name the same mission/checkpoint/task/next action.
- [ ] Create another checkpoint immediately before PR/review so an abrupt context/usage limit is recoverable.

### Task 9: Chaos/Recovery Certification and Repository Integration

**Files:** tests as needed; runtime artifact `CONTINUITY_CERTIFICATION.json`.

**Produces:** `CONTINUITY_CERTIFIED` only after required recovery scenarios and merged-artifact cold resume pass.

- [ ] Run chaos matrix: fresh process, abrupt step, corrupt/stale checkpoint, main drift, Production drift, >3 workstreams, missing capability, holdout monotonicity, incomplete child handoff.
- [ ] Run all mission/Council/skill/export tests, full backend, repository hygiene and `git diff --check` on exact head.
- [ ] Push PR; run CodeRabbit when authorized/available plus exact-head CI/CodeQL/Sonar; triage valid findings via RED→GREEN.
- [ ] Merge only on exact-head green required gates. Do not deploy WaterfallHunter application containers for this tooling-only change.
- [ ] Verify merged main; perform cold resume on merged code; then and only then write `CONTINUITY_CERTIFIED` and update GitHub mission checkpoint.
- [ ] Generate clean Project Sources bundle from merged main and update the TWFH Google Drive source folder where authorized; record any unavailable ChatGPT UI-only settings mutation explicitly.
- [ ] Final checkpoint sets exact next action to `re-baseline current main/Production and start Model Excellence v3 Phase 0`.

---
## Self-Review Result

- Spec coverage: durable state, exact resume phrase, DAG, workstream bound, branch registry, interruption journal, GitHub mirror, ChatGPT/Codex surfaces, scientific lock, drift guard, chaos tests and certification all map to Tasks 1–9.
- Placeholder scan: no TODO/TBD/“implement later” instruction remains.
- Interface consistency: every public function/command is defined by the task that creates it before later tasks consume it.
- Scope: one subsystem only — Mission Control/Continuity. Model optimization is intentionally excluded until `CONTINUITY_CERTIFIED`.
- Safety: no model, signal, Telegram, order-placement or Production runtime semantics are modified by this plan.
