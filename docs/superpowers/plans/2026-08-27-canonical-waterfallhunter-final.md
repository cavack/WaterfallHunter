# Canonical WaterfallHunter Final Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace competing scores/states with one canonical entry-decision engine, integrate cascade evidence, repair durable Telegram delivery, simplify the dashboard, and provide a clean-install deployment path.

**Architecture:** Existing discovery/market-data modules remain inputs. A new pure decision module converts canonical metrics into a versioned decision packet. Main loop persists decision events and attaches the latest decision to dashboard candidates. Research subsystems remain separate. Telegram consumes persisted ENTRY_READY events through the durable outbox worker.

**Tech Stack:** Python 3.12/FastAPI/Pydantic/SQLite, Next.js 16/React/TypeScript/Tailwind, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-27-canonical-waterfallhunter-final-design.md`

## Global Constraints
- SIGNAL_ONLY; `LIVE_TRADING_ENABLED=false` hard fail-closed.
- SHORT, linear USDT perpetual domain rules remain fixed.
- Missing/stale evidence is explicit unavailable.
- One public `entry_readiness` score only.
- ENTRY_READY events are persistent and transition explicitly.
- At most 3 ENTRY_READY and 6 FORMING cards on main dashboard.
- Telegram notification only; no exchange execution path.
- All production behavior changes are test-first.

---
### Task 1: Canonical decision contract and pure engine
**Files:** create `backend/src/waterfallhunter/core/entry_decision.py`; create `backend/tests/test_entry_decision.py`.
**Produces:** `EntryDecisionPolicy`, `build_entry_decision(metrics, candidate_status, now)` returning `entry_decision_v1`.
- [ ] Write failing tests for FORMING, ENTRY_READY, LATE, NO_TRADE, stale hard-block, and reason-code determinism.
- [ ] Run focused tests and verify RED.
- [ ] Implement minimum pure engine using existing watch/strict/cascade/anti-chase/execution evidence.
- [ ] Run focused tests and full backend regression.
- [ ] Commit.

### Task 2: Cascade evidence adapter inside current evidence path
**Files:** create `backend/src/waterfallhunter/core/cascade_intelligence.py`; create `backend/tests/test_cascade_intelligence.py`; modify `multi_exchange_validator.py`.
**Produces:** `build_cascade_evidence(metrics)` with explicit available/partial/unavailable components.
- [ ] Test CVD/sell-flow, OI/funding/crowding, order-book pressure, optional liquidation packet, coverage/freshness.
- [ ] Verify RED, implement, verify GREEN.
- [ ] Attach packet to metrics without fabricating liquidation data.
- [ ] Full regression and commit.

### Task 3: Persistent decision events and no disappearing signals
**Files:** add migration `0006_entry_decisions.sql`; create `core/entry_decision_store.py`; tests; modify `main.py` and schema contract.
**Produces:** append-only decision events plus current decision projection.
- [ ] Test ENTRY_READY persistence and explicit ACTIVE/LATE/INVALIDATED/EXPIRED transitions.
- [ ] Verify RED, implement migration/store, integrate evaluation loop.
- [ ] Ensure lifecycle downgrade cannot erase decision history.
- [ ] Regression/migration tests and commit.
### Task 4: Telegram durable delivery repair
**Files:** modify `core/notifier.py`, notification transport/worker integration in `main.py`, config/health tests, `.env.example`.
**Produces:** validated Telegram transport and durable ENTRY_READY delivery.
- [ ] Reproduce current disabled durable-delivery state in a focused test.
- [ ] Verify RED for configured credentials with disabled worker.
- [ ] Implement Telegram transport adapter with HTTP status classification, startup credential probe, durable worker startup, health exposure, and safe logs.
- [ ] Keep interactive bot separate from signal delivery.
- [ ] Verify retries/dead-letter/disabled-reason tests and commit.

### Task 5: AI advisory on canonical evidence
**Files:** modify `core/ai_veto.py`, tests, candidate output.
**Produces:** advisory packet consuming decision/cascade/derivatives/microstructure/structure evidence.
- [ ] Add failing tests that prompt material includes canonical evidence and AI failure never changes decision.
- [ ] Implement advisory-only behavior and explicit UNAVAILABLE reason.
- [ ] Verify and commit.

### Task 6: Decision-first dashboard
**Files:** modify dashboard contract/generator, `frontend/app/page.tsx`, `score-card.tsx`; create decision components/table; move research panels behind Research section.
**Produces:** max 3 ENTRY_READY, max 6 FORMING, recently changed, compact searchable/paginated all-candidates table.
- [ ] Add backend contract tests for decision fields and limits.
- [ ] Verify RED, expose canonical decision projection.
- [ ] Replace competing public ranking/watch score with entry-readiness cards and exact signal details.
- [ ] Fix percent/unit labels and clear unavailable/block reasons.
- [ ] Run typecheck/build and commit.

### Task 7: Automatic validation and systemic-zero diagnostics
**Files:** modify `core/signal_funnel.py`; add validation summary worker/service/tests.
**Produces:** critical-gate systemic-zero alarms and rolling canonical decision validation summary.
- [ ] Add failing tests for primary/composite/stage systemic-zero detection.
- [ ] Implement bounded diagnostics and expose them only in research/health.
- [ ] Add automatic recorded-evidence validation entry point using the canonical decision engine.
- [ ] Verify and commit.
### Task 8: Clean install and single-runtime deployment
**Files:** create `deploy/install-clean.sh`, `deploy/preflight.sh`, `deploy/verify-runtime.sh`, `docs/clean-install.md`; update compose/env documentation.
**Produces:** reproducible Ubuntu install from repo + `.env` + optional DB backup.
- [ ] Add shell/static tests for destructive-scope guards and required environment checks.
- [ ] Implement preflight, backup/import, scoped legacy cleanup, build/up/migrate, runtime verification and rollback metadata.
- [ ] Validate scripts with `bash -n`, compose config and a non-destructive rehearsal path.
- [ ] Commit.

### Task 9: Final integration verification and release handoff
**Files:** changelog/release notes as needed.
**Produces:** deploy-ready branch/PR with evidence.
- [ ] Run complete backend test suite.
- [ ] Run frontend typecheck and production build.
- [ ] Run Docker Compose config/build and container smoke tests in isolated project namespace.
- [ ] Exercise Telegram transport against configured production credentials with a harmless verification message only if explicitly marked verification-safe; otherwise validate `getMe`/chat config without sending a signal.
- [ ] Verify migration from a copied production DB, not the live DB.
- [ ] Run clean-install dry rehearsal against an isolated directory/project name.
- [ ] Push branch, open PR, record exact commit SHA and clean-install commands.
- [ ] Stop at deploy-ready; do not replace production automatically.
