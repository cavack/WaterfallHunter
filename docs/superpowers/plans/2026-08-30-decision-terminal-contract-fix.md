# Decision Terminal Contract Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore canonical Structure/Timing evidence into Entry Decision and make the dashboard distinguish systemic pipeline degradation from ordinary market gate failures.

**Architecture:** Fix the data loss at the producer boundary in `MultiExchangeValidator`, then extend the existing additive Decision Terminal diagnostics contract with systemic-availability health. Keep all trading semantics unchanged. React consumes the canonical diagnostics and only adds presentation helpers for counts, trade-plan availability, advisory status, and data freshness.

**Tech Stack:** Python 3.13, pytest, FastAPI/Pydantic, TypeScript/Next.js/React, generated dashboard types.

**Spec:** User-reproduced Production screenshot and live `/api/candidates` evidence on `1dd3aef777ed1ce3d87eeba06a4f3e527ee670fd`.

## Global Constraints

- Preserve ScoreV2 weights and evidence meaning.
- Preserve lifecycle transitions and anti-chase threshold/behavior.
- Preserve strict/experimental eligibility boundaries.
- Preserve `LIVE_TRADING_ENABLED=false` and Telegram delivery disabled.
- Missing evidence remains unavailable; never synthesize market data.
- Dashboard business semantics remain backend-canonical.

---

### Task 1: Restore Entry Decision candle-feature contract

**Files:**
- Modify: `backend/src/waterfallhunter/core/multi_exchange_validator.py`
- Test: `backend/tests/test_multi_exchange_validator.py`

- [ ] Write a failing integration regression around `cross_check_symbol` proving valid candle-analysis fields survive into `metrics.candle_features` and no Structure/Timing availability signal is lost.
- [ ] Run the targeted test and verify RED for omitted `valid`/timing fields.
- [ ] Add the minimum projection fields required by existing Entry Decision consumers.
- [ ] Run validator + entry-decision neighboring tests and verify GREEN.

### Task 2: Publish systemic pipeline degradation in the canonical terminal contract

**Files:**
- Modify: `backend/src/waterfallhunter/core/decision_terminal.py`
- Modify: `backend/src/waterfallhunter/core/dashboard_stream.py`
- Modify: `scripts/generate_dashboard_types.py` only if generator support is required
- Generated: `frontend/generated/dashboard-contract.ts`
- Modify: `frontend/lib/dashboard-contract.ts`
- Test: `backend/tests/test_decision_terminal.py`, dashboard stream/type tests, `frontend/tests/dashboard-contract.test.ts`

- [ ] Add RED tests requiring `pipeline_degraded` and bounded systemic unavailable reasons only when an availability reason affects the full evaluated universe.
- [ ] Extend Pydantic/runtime validation additively without changing `decision_terminal_v1` semantics.
- [ ] Regenerate TypeScript and update runtime frontend validation.
- [ ] Run backend/contract tests GREEN.

### Task 3: Correct dashboard presentation

**Files:**
- Create: `frontend/lib/decision-terminal-ui.ts`
- Modify: `frontend/components/decision-terminal.tsx`
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/tests/dashboard-contract.test.ts`
- Modify: `frontend/tsconfig.contract-test.json`

- [ ] Add RED TypeScript tests for complete blocked/other counts, stale candidate presentation from canonical policy, trade-plan presence, and pending advisory confidence suppression.
- [ ] Implement pure presentation helpers; no ranking/scoring logic.
- [ ] Render pipeline-degraded as an error state, not a market warning.
- [ ] Replace five `$—` boxes with a compact no-plan message when no canonical plan exists.
- [ ] Show pending advisory without misleading `0%` confidence.
- [ ] Separate stream transport badge from candidate-data freshness badge and mark stale table rows/cells.
- [ ] Run contract test, typecheck, build and browser smoke.

### Task 4: Verification and release

**Files:** exact final diff only.

- [ ] Run targeted regression matrix, full backend suite, frontend contract/typecheck/build, `git diff --check`.
- [ ] Re-read final diff and confirm protected invariants unchanged.
- [ ] Commit/push branch, open PR, inspect review/CI on exact head and resolve actionable findings.
- [ ] Merge only after required gates are green.
- [ ] Use the existing authorized release workflow; preserve certified recovery artifacts and SIGNAL_ONLY boundaries.
- [ ] Verify Production API no longer shows systemic false `STRUCTURE_UNAVAILABLE`/`TIMING_UNAVAILABLE`, dashboard counts reconcile, endpoints/DB/safety remain healthy, then perform risk-proportional soak before production certification.
