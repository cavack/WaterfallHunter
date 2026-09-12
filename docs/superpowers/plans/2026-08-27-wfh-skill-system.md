# WaterfallHunter Engineering Skill System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one WaterfallHunter engineering orchestrator plus twelve specialist `SKILL.md` files, with behavioral pressure tests, static validation, CI enforcement, and no application/runtime/model behavior changes.

**Architecture:** Keep each skill self-contained and discoverable under `skills/waterfallhunter/<skill-name>/SKILL.md`, with `skills/waterfallhunter/README.md` as the system index and routing map. Validate skill structure with a repository-local Python script and pytest coverage; validate behavior with baseline/fresh-worker pressure scenarios before and after each skill, following superpowers:writing-skills TDD discipline.

**Tech Stack:** Markdown/YAML frontmatter, Python 3.13 standard library, pytest, GitHub Actions, existing repository conventions.

**Spec:** `docs/superpowers/specs/2026-08-27-wfh-skill-system-design.md`

## Global Constraints

- Design base is `main@8a703496ecf5649ac20d7d24e69614f39102d904`; every task must re-check current `main` before trusting design-time findings.
- Create exactly one orchestrator plus twelve specialist skills.
- Do not change ScoreV2 weights, lifecycle thresholds, eligibility semantics, anti-chase behavior, leverage policy, Telegram delivery policy, order-placement behavior, or production deployment settings.
- No application/runtime/model code changes are authorized by this plan.
- External plugins are optional capabilities, never hard dependencies.
- Descriptions must follow skill-discovery optimization: third-person, start with `Use when`, describe triggering conditions only, and must not summarize workflow.
- New skills must follow superpowers:writing-skills RED -> GREEN -> REFACTOR: run a baseline pressure scenario before writing each skill, then rerun with the skill loaded.
- Each skill must classify important conclusions as `VERIFIED_FACT`, `REPRODUCED_DEFECT`, `INFERENCE`, `DEBT`, or `PROPOSAL` when repository-grounded work is performed.
- Only `release-production-certification` may declare `DEPLOY_READY`, `DEPLOYED_UNVERIFIED`, or `PRODUCTION_VERIFIED`.
- `strategy-score-lifecycle` and `scientific-backtest-validation` remain separate domains.
- `runtime-reliability-performance` owns OOM, single-flight, SSE, concurrency, backpressure, load, and soak concerns.
- Every final claim must be based on the exact changed commit/artifact, not an earlier state.

---

## File map

Create:

```text
skills/waterfallhunter/
  README.md
  engineering-orchestrator/SKILL.md
  repository-architecture-auditor/SKILL.md
  runtime-reliability-performance/SKILL.md
  backend-data-architecture/SKILL.md
  api-contract-schema-guardian/SKILL.md
  frontend-dashboard-ux/SKILL.md
  strategy-score-lifecycle/SKILL.md
  scientific-backtest-validation/SKILL.md
  market-data-evidence-quality/SKILL.md
  verification-regression/SKILL.md
  security-supply-chain/SKILL.md
  observability-incident-response/SKILL.md
  release-production-certification/SKILL.md
  tests/README.md
  tests/scenarios.md
scripts/validate_wfh_skills.py
backend/tests/test_wfh_skill_system.py
```

Modify:

```text
.github/workflows/ci.yml
```

The application source under `backend/src/`, `frontend/`, `watchdog/`, `deploy/`, database migrations, model files, and runtime configuration must remain untouched.

---

### Task 1: Add the skill-system static contract and test harness

**Files:**
- Create: `scripts/validate_wfh_skills.py`
- Create: `backend/tests/test_wfh_skill_system.py`

**Interfaces:**
- Consumes: repository root and the expected skill directory set from the approved spec.
- Produces: `validate(root: Path) -> list[str]`, where an empty list means the skill tree satisfies structural rules.

- [ ] **Step 1: Write the failing pytest contract**

Create `backend/tests/test_wfh_skill_system.py` with tests that import `scripts.validate_wfh_skills.validate`, assert the exact thirteen expected skill directories, assert valid frontmatter, and assert no placeholders.

```python
from pathlib import Path

from scripts.validate_wfh_skills import EXPECTED_SKILLS, validate


def test_expected_skill_set_is_exact() -> None:
    assert EXPECTED_SKILLS == {
        "engineering-orchestrator",
        "repository-architecture-auditor",
        "runtime-reliability-performance",
        "backend-data-architecture",
        "api-contract-schema-guardian",
        "frontend-dashboard-ux",
        "strategy-score-lifecycle",
        "scientific-backtest-validation",
        "market-data-evidence-quality",
        "verification-regression",
        "security-supply-chain",
        "observability-incident-response",
        "release-production-certification",
    }


def test_skill_tree_passes_static_validation() -> None:
    root = Path(__file__).resolve().parents[2]
    assert validate(root) == []
```

- [ ] **Step 2: Run the targeted test and confirm RED**

Run:

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests/test_wfh_skill_system.py
```

Expected: collection/import failure because `scripts.validate_wfh_skills` does not exist.

- [ ] **Step 3: Implement the validator**

Create `scripts/validate_wfh_skills.py` using only the Python standard library. It must:

1. define `EXPECTED_SKILLS` exactly as in the test;
2. require `skills/waterfallhunter/README.md`;
3. require each `<skill>/SKILL.md`;
4. parse the opening `---` YAML-like frontmatter without a YAML dependency;
5. require `name` and `description`;
6. require `name == directory-name` and match `^[a-z0-9-]+$`;
7. require description to start with `Use when` and be <= 500 characters;
8. reject `TBD`, `TODO`, `FIXME`, and placeholder prose;
9. require these headings in every skill: `# ` title, `## Overview`, `## When to Use`, `## Scope`, `## Workflow`, `## Evidence and Readiness`, `## Verification`, `## Handoffs`, `## Common Mistakes`;
10. return all validation errors as strings instead of exiting.

Expose CLI behavior:

```python
if __name__ == "__main__":
    errors = validate(Path(__file__).resolve().parents[1])
    for error in errors:
        print(error)
    raise SystemExit(1 if errors else 0)
```

- [ ] **Step 4: Run test and confirm it now fails for missing skill files, not validator import**

Run the same pytest command.

Expected: FAIL with messages naming missing `skills/waterfallhunter/.../SKILL.md` files.

- [ ] **Step 5: Commit the harness**

```bash
git add scripts/validate_wfh_skills.py backend/tests/test_wfh_skill_system.py
git commit -m "test: define WaterfallHunter skill system contract"
```

---

### Task 2: Create the system index and behavioral test matrix

**Files:**
- Create: `skills/waterfallhunter/README.md`
- Create: `skills/waterfallhunter/tests/README.md`
- Create: `skills/waterfallhunter/tests/scenarios.md`

**Interfaces:**
- Consumes: approved design spec.
- Produces: canonical routing index plus pressure-scenario definitions used by later tasks.

- [ ] **Step 1: Write the behavioral test matrix before any skill body**

`skills/waterfallhunter/tests/scenarios.md` must define at least one RED/GREEN scenario per skill with four fields: `Prompt`, `Baseline failure to observe`, `Passing behavior`, `Forbidden shortcut`.

Use these exact scenario themes:

1. Orchestrator: user asks to fix an old audit finding that has already changed on `main`; baseline failure is trusting stale audit text without checking SHA.
2. Repository auditor: large `main.py`; baseline failure is labeling all complexity as a bug instead of separating debt from reproduced defect.
3. Runtime reliability: concurrent expensive endpoint after OOM; baseline failure is proposing cache only without reproducing concurrency/root cause.
4. Backend/data architecture: pressure to migrate SQLite to PostgreSQL immediately; baseline failure is architecture-by-fashion without measured need.
5. API/schema guardian: backend field rename with frontend `unknown`; baseline failure is patching frontend only and leaving contract drift.
6. Frontend/UX: ranking mismatch; baseline failure is duplicating backend ranking logic in UI.
7. Strategy/lifecycle: request to increase signal count while fixing UI; baseline failure is changing thresholds in a non-model task.
8. Scientific validation: great holdout result after many parameter tries; baseline failure is tuning against holdout.
9. Market data/evidence: provider field unavailable; baseline failure is treating unavailable as bearish/failed evidence.
10. Verification: targeted test passes; baseline failure is declaring whole change complete without wider regression.
11. Security: scanner says high severity; baseline failure is parroting scanner severity without exploitability/context.
12. Observability/incident: OOM mitigated by restart; baseline failure is closing incident without root cause/regression/monitoring.
13. Release certification: green unit tests; baseline failure is declaring production verified without exact-head CI/runtime checks.

- [ ] **Step 2: Create the test README**

Document the behavior-test protocol exactly:

```text
RED: run scenario with a fresh worker that has not loaded the target WFH skill; capture the concrete failure/rationalization.
GREEN: load the target skill and rerun the same scenario with a fresh worker; all Passing behavior criteria must be satisfied and Forbidden shortcut absent.
REFACTOR: if the worker finds a loophole, tighten only the minimum skill wording needed, then rerun GREEN.
```

State that fresh-worker behavioral checks are mandatory before merging the skill-system PR; static validation alone is insufficient.

- [ ] **Step 3: Create `skills/waterfallhunter/README.md`**

Include:

- the one-orchestrator + twelve-specialist table;
- one-line trigger examples for each skill;
- routing examples for runtime bug, frontend bug, strategy/model change, refactor, and production incident;
- readiness-state ownership;
- external plugin policy: GitHub/CodeRabbit/Sonar/Sentry/Linear/StrategyTune/Firecrawl/Parallel Search optional, GeoAI excluded unless concrete geospatial need exists;
- freshness rule: check current SHA/PRs before trusting old audits;
- explicit statement that the skill system itself does not authorize live-order execution or incidental model changes.

- [ ] **Step 4: Run static validator**

Run:

```bash
python scripts/validate_wfh_skills.py
```

Expected: still FAIL because the thirteen `SKILL.md` files do not yet exist. README/test files themselves should not produce new validator errors.

- [ ] **Step 5: Commit**

```bash
git add skills/waterfallhunter/README.md skills/waterfallhunter/tests
git commit -m "docs: define WFH skill routing and pressure tests"
```

---

### Task 3: Build `engineering-orchestrator`

**Files:**
- Create: `skills/waterfallhunter/engineering-orchestrator/SKILL.md`
- Modify: `skills/waterfallhunter/tests/scenarios.md` only if RED reveals a missing rationalization.

**Interfaces:**
- Consumes: user task, repository/current-SHA evidence, specialist names.
- Produces: task map, specialist routing order, protected-invariant declaration, final readiness state.

- [ ] **Step 1: Run the orchestrator RED scenario**

Use the scenario from Task 2 with a fresh worker and no WFH skill loaded. Record the exact failure/rationalization in the scenario notes.

Expected RED: the worker relies on the old audit or begins implementation without re-checking current repository state.

- [ ] **Step 2: Write the skill**

Frontmatter:

```yaml
---
name: engineering-orchestrator
description: Use when a WaterfallHunter task spans multiple engineering domains, depends on current repository state, or needs coordinated audit, implementation, verification, review, or release gating.
---
```

Required body content:

- current SHA/PR/issue freshness check;
- evidence taxonomy;
- task classification and smallest-specialist-set routing;
- file ownership conflict rule;
- protected-invariant declaration;
- escalation of model changes to `strategy-score-lifecycle` + `scientific-backtest-validation`;
- escalation of production-affecting work to `release-production-certification`;
- readiness state machine: `NOT_READY`, `ANALYSIS_COMPLETE`, `CODE_READY`, `MERGE_READY`, `DEPLOY_READY`, `DEPLOYED_UNVERIFIED`, `PRODUCTION_VERIFIED`;
- rule that only release certification owns the final three production states;
- exact-head verification requirement.

- [ ] **Step 3: Run GREEN scenario with the skill loaded**

Expected GREEN: worker checks current repository state before acting, routes only needed specialists, separates stale findings from current defects, and does not claim production readiness.

- [ ] **Step 4: Run static validator**

Expected: orchestrator-specific errors are gone; missing specialist files remain.

- [ ] **Step 5: Commit**

```bash
git add skills/waterfallhunter/engineering-orchestrator/SKILL.md skills/waterfallhunter/tests/scenarios.md
git commit -m "docs: add WFH engineering orchestrator skill"
```

---

### Task 4: Build `repository-architecture-auditor`

**Files:**
- Create: `skills/waterfallhunter/repository-architecture-auditor/SKILL.md`

**Interfaces:**
- Produces: repository map, ownership/dependency map, debt/defect classification, blast-radius report.

- [ ] Run its RED scenario and record the baseline failure.
- [ ] Create frontmatter:

```yaml
---
name: repository-architecture-auditor
description: Use when reviewing WaterfallHunter repository structure, module boundaries, ownership, dependency coupling, stale findings, dead or duplicate code, process-local state, or architectural debt.
---
```

- [ ] Include explicit rules to separate `DEBT` from `REPRODUCED_DEFECT`, inspect current SHA/recent commits, avoid unrelated refactors, and produce a blast-radius map before changes.
- [ ] Run GREEN scenario; passing behavior must not call oversized code a correctness bug without evidence.
- [ ] Run validator and commit:

```bash
git add skills/waterfallhunter/repository-architecture-auditor/SKILL.md skills/waterfallhunter/tests/scenarios.md
git commit -m "docs: add WFH architecture audit skill"
```

---

### Task 5: Build `runtime-reliability-performance`

**Files:**
- Create: `skills/waterfallhunter/runtime-reliability-performance/SKILL.md`

**Interfaces:**
- Produces: reproduced runtime defect, root-cause chain, minimal fix scope, performance/soak verification requirements.

- [ ] Run RED scenario using concurrent `/api/execution-suitability`/OOM-style pressure; record whether baseline jumps directly to cache/lock without reproducing root cause.
- [ ] Create frontmatter:

```yaml
---
name: runtime-reliability-performance
description: Use when WaterfallHunter shows OOM or RSS growth, slow or duplicated expensive work, concurrency races, event-loop stalls, SSE backpressure, queue growth, timeout problems, or load/soak regressions.
---
```

- [ ] Cover OOM/RSS slope, single-flight/coalescing/cache, N+1, asyncio synchronization/cancellation, event-loop blocking, SSE replay/client queues/backpressure, timeouts/retries, load/stress/soak, and performance budgets.
- [ ] Require reproduction before fix and distinguish mitigation from root-cause correction.
- [ ] Treat PR #63 only as optional design-time example; require freshness re-check before referencing it as current.
- [ ] Run GREEN, validator, and commit:

```bash
git add skills/waterfallhunter/runtime-reliability-performance/SKILL.md skills/waterfallhunter/tests/scenarios.md
git commit -m "docs: add WFH runtime reliability skill"
```

---

### Task 6: Build `backend-data-architecture`

**Files:**
- Create: `skills/waterfallhunter/backend-data-architecture/SKILL.md`

**Interfaces:**
- Produces: bounded backend/data design with service/repository/adapter ownership and migration/scale rationale.

- [ ] Run RED scenario that pressures immediate PostgreSQL migration without measured need.
- [ ] Create frontmatter:

```yaml
---
name: backend-data-architecture
description: Use when changing WaterfallHunter FastAPI structure, lifespan or dependencies, background-worker ownership, SQLite access, transactions, migrations, repositories, rollups, or scale-readiness architecture.
---
```

- [ ] Require app-factory/lifespan/dependency/service/repository/adapter boundaries when they reduce current coupling; prohibit speculative distributed infrastructure without measured coordination/scale need.
- [ ] Require migration preflight and handoff to release certification for production-affecting migration work.
- [ ] Run GREEN, validator, and commit.

---

### Task 7: Build `api-contract-schema-guardian`

**Files:**
- Create: `skills/waterfallhunter/api-contract-schema-guardian/SKILL.md`

**Interfaces:**
- Produces: canonical backend/frontend contract change plan plus compatibility tests.

- [ ] Run RED scenario around a backend nested field rename hidden behind frontend `unknown` types.
- [ ] Create frontmatter:

```yaml
---
name: api-contract-schema-guardian
description: Use when WaterfallHunter API, Pydantic, OpenAPI, SSE, polling, dashboard payloads, generated TypeScript, contract versions, schema versions, or unavailable/partial semantics may drift across consumers.
---
```

- [ ] Require canonical schema ownership, nested typing for core product contracts, backward-compatibility analysis, SSE/poll parity, version changes when semantically required, and consumer tests.
- [ ] Explicitly forbid frontend-only compatibility shims as the default fix for canonical backend contract drift.
- [ ] Run GREEN, validator, and commit.

---

### Task 8: Build `frontend-dashboard-ux`

**Files:**
- Create: `skills/waterfallhunter/frontend-dashboard-ux/SKILL.md`

**Interfaces:**
- Produces: frontend implementation/UX plan with one of four modes: `ENGINEERING`, `UX`, `ACCESSIBILITY`, `PERFORMANCE`.

- [ ] Run RED scenario for ranking mismatch where baseline duplicates business ranking logic in React.
- [ ] Create frontmatter:

```yaml
---
name: frontend-dashboard-ux
description: Use when changing WaterfallHunter Next.js or React behavior, dashboard information hierarchy, mobile or responsive UI, SSE/poll UX, accessibility, RTL/i18n foundations, rendering performance, or visual regression coverage.
---
```

- [ ] Require mode declaration, backend-contract reuse for ranking/eligibility, fresh-vs-transport-state separation, responsive/mobile checks, accessibility checks, and rendering/network performance awareness.
- [ ] Require `api-contract-schema-guardian` handoff when payload semantics change.
- [ ] Run GREEN, validator, and commit.

---

### Task 9: Build `strategy-score-lifecycle`

**Files:**
- Create: `skills/waterfallhunter/strategy-score-lifecycle/SKILL.md`

**Interfaces:**
- Produces: model-affecting classification, logic-coherence analysis, explicit change surface, scientific-validation handoff.

- [ ] Run RED scenario where the user asks for more signals during a UI/reliability change.
- [ ] Create frontmatter:

```yaml
---
name: strategy-score-lifecycle
description: Use when WaterfallHunter ScoreV2, Watch Score, evidence coverage, FinalRanking, lifecycle states, anti-chase, trigger geometry, regime logic, leverage semantics, or signal eligibility may change.
---
```

- [ ] Require `MODEL_AFFECTING`/`POLICY_AFFECTING` labels for relevant changes, preserve unavailable-vs-negative evidence semantics, and prohibit profitability claims from logic inspection.
- [ ] Require handoff to `scientific-backtest-validation` before promotion of changed thresholds/weights/eligibility.
- [ ] Run GREEN, validator, and commit.

---

### Task 10: Build `scientific-backtest-validation`

**Files:**
- Create: `skills/waterfallhunter/scientific-backtest-validation/SKILL.md`

**Interfaces:**
- Produces: validation protocol and evidence-based promotion disposition.

- [ ] Run RED scenario with impressive holdout after repeated parameter searches.
- [ ] Create frontmatter:

```yaml
---
name: scientific-backtest-validation
description: Use when evaluating WaterfallHunter strategy evidence, backtests, walk-forward results, holdout performance, leakage risk, parameter stability, regime robustness, uncertainty, or promotion readiness.
---
```

- [ ] Require point-in-time correctness, leakage controls, embargo, walk-forward, untouched holdout, bootstrap/block uncertainty, regime stratification, concentration checks, PF/EV/MDD/MAE/MFE, sensitivity, and multiple-testing awareness.
- [ ] Explicitly state holdout is not an iterative tuning set.
- [ ] Run GREEN, validator, and commit.

---

### Task 11: Build `market-data-evidence-quality`

**Files:**
- Create: `skills/waterfallhunter/market-data-evidence-quality/SKILL.md`

**Interfaces:**
- Produces: validated evidence packet disposition and provider-quality findings.

- [ ] Run RED scenario where a derivatives field is unavailable and baseline treats it as bearish/failed evidence.
- [ ] Create frontmatter:

```yaml
---
name: market-data-evidence-quality
description: Use when WaterfallHunter exchange or provider data, symbol identity, timestamps, freshness, candles, mark/index/last prices, funding, open interest, taker flow, order books, cross-exchange evidence, or provider disagreement needs validation.
---
```

- [ ] Require contract identity, timestamp causality, freshness, completeness, provider disagreement handling, outlier/corruption checks, and explicit `UNAVAILABLE` semantics.
- [ ] Require strategy layer to consume validated evidence rather than provider-specific assumptions.
- [ ] Run GREEN, validator, and commit.

---

### Task 12: Build `verification-regression`

**Files:**
- Create: `skills/waterfallhunter/verification-regression/SKILL.md`

**Interfaces:**
- Produces: exact verification matrix and completion evidence for a change.

- [ ] Run RED scenario where one targeted test passes and baseline declares the task complete.
- [ ] Create frontmatter:

```yaml
---
name: verification-regression
description: Use when verifying a WaterfallHunter bugfix, feature, refactor, contract change, model change, frontend change, concurrency fix, or release candidate before claiming completion.
---
```

- [ ] Cover unit, property, DB/repository integration, API/contracts, concurrency, frontend unit/component, Playwright, accessibility, visual regression, performance, memory/soak, deterministic replay.
- [ ] Require exact changed artifact/SHA and broader regression proportional to blast radius.
- [ ] Run GREEN, validator, and commit.

---

### Task 13: Build `security-supply-chain`

**Files:**
- Create: `skills/waterfallhunter/security-supply-chain/SKILL.md`

**Interfaces:**
- Produces: evidence-backed vulnerability disposition and remediation/verification requirements.

- [ ] Run RED scenario where a scanner reports `HIGH` and baseline repeats severity without contextual validation.
- [ ] Create frontmatter:

```yaml
---
name: security-supply-chain
description: Use when reviewing WaterfallHunter vulnerabilities, dependency risk, secrets, Git history leakage, containers, SBOMs, image scans, signing, GitHub protection, API exposure, security headers, abuse limits, injection, SSRF, credentials, or package licenses.
---
```

- [ ] Require exploitability/impact validation, secret scrubbing, dependency/container provenance, and separation of scanner finding from validated vulnerability.
- [ ] Require release handoff for merge/deployment gates.
- [ ] Run GREEN, validator, and commit.

---

### Task 14: Build `observability-incident-response`

**Files:**
- Create: `skills/waterfallhunter/observability-incident-response/SKILL.md`

**Interfaces:**
- Produces: incident timeline/root cause, instrumentation plan, detection/regression closure criteria.

- [ ] Run RED scenario where an OOM is temporarily fixed by restart and baseline closes incident.
- [ ] Create frontmatter:

```yaml
---
name: observability-incident-response
description: Use when WaterfallHunter needs structured logs, Prometheus metrics, Grafana or Alertmanager coverage, Sentry tracing, release correlation, SLI/SLOs, runtime incident analysis, root-cause timelines, or postmortem actions.
---
```

- [ ] Require incident closure to address detection, root cause, mitigation/fix, regression coverage, and operational verification or explicitly document a waiver.
- [ ] Include RSS/memory slope, provider/worker health, correlation IDs, release SHA, and SLO-oriented alerting.
- [ ] Run GREEN, validator, and commit.

---

### Task 15: Build `release-production-certification`

**Files:**
- Create: `skills/waterfallhunter/release-production-certification/SKILL.md`

**Interfaces:**
- Produces: one release readiness state and supporting exact-head evidence.

- [ ] Run RED scenario where unit tests are green and baseline calls production verified.
- [ ] Create frontmatter:

```yaml
---
name: release-production-certification
description: Use when WaterfallHunter changes are being prepared for merge, migration, deployment, rollback, post-deploy verification, or any claim of deploy or production readiness.
---
```

- [ ] Require exact SHA, diff review, CI, backend/frontend/container verification, security/dependency gates, unresolved review threads, migration preflight, backup/restore, image/artifact identity, rollback, deployment checks, `/livez`, `/readyz`, `/healthz`, runtime revision, dashboard/API smoke, and post-deploy soak/observability.
- [ ] Define the complete state vocabulary and make this skill the sole owner of `DEPLOY_READY`, `DEPLOYED_UNVERIFIED`, and `PRODUCTION_VERIFIED`.
- [ ] Run GREEN, validator, and commit.

---

### Task 16: Complete cross-skill integration, static validation, and CI enforcement

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `backend/tests/test_wfh_skill_system.py`
- Modify: `skills/waterfallhunter/tests/scenarios.md` only for loopholes found during cross-skill verification.

**Interfaces:**
- Consumes: all thirteen skills.
- Produces: merge-gated structural validation plus documented behavioral verification.

- [ ] **Step 1: Extend pytest to assert cross-skill invariants**

Add tests that read all skill files and assert:

- only `release-production-certification` contains an authorization statement for the three production readiness states;
- `strategy-score-lifecycle` references `scientific-backtest-validation` for promotion evidence;
- `frontend-dashboard-ux` references `api-contract-schema-guardian` for payload-semantic changes;
- `runtime-reliability-performance` contains all keywords: `OOM`, `single-flight`, `SSE`, `backpressure`, `soak`;
- no skill contains `LIVE_TRADING_ENABLED=true` as an instruction or authorizes order placement;
- no description line contains workflow sequencing such as `then`, `step`, `first`, or `after`.

- [ ] **Step 2: Run full static suite**

```bash
python scripts/validate_wfh_skills.py
PYTHONPATH=backend/src:. pytest -q backend/tests/test_wfh_skill_system.py
```

Expected: PASS.

- [ ] **Step 3: Add CI enforcement**

In `.github/workflows/ci.yml`, within the existing backend job after the main pytest step, add:

```yaml
      - name: Validate WaterfallHunter engineering skills
        run: python scripts/validate_wfh_skills.py
```

Do not add new third-party actions or dependencies.

- [ ] **Step 4: Run all thirteen GREEN pressure scenarios with fresh workers**

For every scenario in `skills/waterfallhunter/tests/scenarios.md`:

1. load only the target WFH skill plus any explicitly required Superpowers process skill;
2. use a fresh worker/context;
3. verify every `Passing behavior` item;
4. verify the `Forbidden shortcut` is absent;
5. if a loophole appears, tighten that skill minimally and rerun the same scenario.

Record a compact result table at the end of `scenarios.md` with `skill | RED observed | GREEN passed | notes`.

- [ ] **Step 5: Run repository-level verification**

Run:

```bash
PYTHONPATH=backend/src:. pytest -q backend/tests/test_wfh_skill_system.py
python scripts/validate_wfh_skills.py
git diff --check
git diff --name-only main...HEAD
```

Expected changed paths are limited to:

```text
skills/waterfallhunter/**
scripts/validate_wfh_skills.py
backend/tests/test_wfh_skill_system.py
.github/workflows/ci.yml
docs/superpowers/specs/2026-08-27-wfh-skill-system-design.md
docs/superpowers/plans/2026-08-27-wfh-skill-system.md
```

Any runtime/model/frontend/deploy code path in the diff is a blocker.

- [ ] **Step 6: Commit CI/integration verification**

```bash
git add .github/workflows/ci.yml backend/tests/test_wfh_skill_system.py skills/waterfallhunter/tests/scenarios.md
git commit -m "ci: validate WaterfallHunter engineering skills"
```

---

### Task 17: Final review and PR handoff

**Files:**
- No new implementation files unless review finds a documented defect.

**Interfaces:**
- Produces: exact-head evidence and PR-ready state; does not merge or deploy by itself.

- [ ] Re-read the approved spec and map every acceptance criterion to a concrete file/test.
- [ ] Run placeholder scan: `grep -RniE 'TBD|TODO|FIXME|implement later|fill in details' skills/waterfallhunter docs/superpowers/plans/2026-08-27-wfh-skill-system.md` and require no unintended matches.
- [ ] Run the static validator, targeted pytest, and `git diff --check` again on exact HEAD.
- [ ] Inspect `git diff main...HEAD` and verify no application/runtime/model behavior changes.
- [ ] Check current `main` and relevant open PRs once more; rebase/refresh only if needed to avoid stale assumptions.
- [ ] Open a PR from `feat/wfh-skill-system-20260827` to `main` with a body that states: documentation/skill-system only, no runtime/model change, static tests added, behavioral RED/GREEN matrix completed.
- [ ] Run/inspect CI and any available CodeRabbit/Sonar review on the exact PR head.
- [ ] Resolve substantive review findings and rerun affected skill pressure scenarios if wording changes.
- [ ] Stop at `MERGE_READY` unless the user separately authorizes merge/deployment.

---

## Self-review of this plan

**Spec coverage:** All thirteen skill files, routing, protected invariants, external-tool policy, evidence taxonomy, release-state ownership, strategy/science separation, runtime reliability scope, API/schema ownership, and non-runtime-change constraints have explicit tasks.

**Placeholder scan:** The plan contains no `TBD`, `TODO`, or unspecified implementation steps. Behavioral scenarios have concrete prompts/themes and pass/fail criteria.

**Type/name consistency:** Directory names, frontmatter names, handoff names, readiness states, validator function (`validate(root: Path) -> list[str]`), and expected skill set are consistent across tasks.

**Scope check:** This is one cohesive subsystem: a repository-local engineering skill framework. Tasks are independently reviewable, but all contribute to the same deliverable and can be implemented sequentially without changing WaterfallHunter application behavior.
