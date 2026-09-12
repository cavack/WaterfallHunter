# WaterfallHunter Engineering Skill System — Design

Date: 2026-08-27
Repository: `cavack/wfh`
Design base: `main@8a703496ecf5649ac20d7d24e69614f39102d904`
Status: DESIGN REVIEW

## Purpose

Create a reusable, repository-grounded engineering skill system for WaterfallHunter that can audit, diagnose, change, verify, review, and release the project without mixing unrelated responsibilities or silently changing trading/model semantics.

The system consists of one orchestrator plus twelve specialist skills. It is deliberately optimized for the current architecture and failure history of WaterfallHunter: FastAPI + long-lived background workers + managed SQLite, Next.js dashboard, SSE/polling, exchange evidence pipelines, ScoreV2/lifecycle logic, scientific validation, Docker/CI, and production observability.

## Non-goals

This skill system does not itself change ScoreV2 weights, lifecycle thresholds, signal eligibility, leverage policy, Telegram delivery policy, exchange execution behavior, or production deployment settings.

It does not treat historical audit findings as current facts. Every run must rediscover the current repository state and current SHA.

It does not introduce live order placement.

## Core architecture

```text
User task
   |
   v
WFH Engineering Orchestrator
   |
   +--> Repository & Architecture Auditor
   +--> Runtime Reliability & Performance Engineer
   +--> Backend & Data Architecture Engineer
   +--> API Contract & Schema Guardian
   +--> Frontend Engineering & Dashboard UX
   +--> Strategy, Score & Lifecycle Validator
   +--> Scientific Backtest & Model Validation
   +--> Market Data & Evidence Quality Engineer
   +--> Verification & Regression Engineer
   +--> Security & Supply-Chain Engineer
   +--> Observability & Incident Response
   +--> Release, Migration & Production Certification
```

The orchestrator owns routing and cross-skill invariants. Specialist skills own analysis and execution only inside their explicit domain.

## Shared execution protocol

Every skill must begin with the following sequence unless the task is purely explanatory and does not depend on repository state:

1. Resolve repository, target branch, current `main` SHA, and requested working branch.
2. Inspect relevant open PRs/issues and recent commits before trusting earlier findings.
3. Identify source-of-truth files and runtime boundaries relevant to the task.
4. Classify every important statement as one of:
   - `VERIFIED_FACT`
   - `REPRODUCED_DEFECT`
   - `INFERENCE`
   - `DEBT`
   - `PROPOSAL`
5. Declare protected invariants and blast radius before changing code.
6. Prefer regression-first work for reproduced defects: RED -> minimal fix -> GREEN -> wider regression.
7. Never silently alter strategy/model semantics as part of infrastructure, UX, performance, or refactor work.
8. Verify the exact changed artifact/commit, not an earlier local state.
9. Re-read the final diff before declaring completion.
10. Never equate `tests pass` with `production verified`.

## Shared protected invariants

Unless the user explicitly authorizes a separate model/strategy change, the following are protected from incidental modification:

- ScoreV2 weights and evidence semantics
- lifecycle transition semantics
- strict/experimental eligibility boundaries
- anti-chase behavior
- signal metadata/provenance contracts
- immutable signal-ledger behavior
- persistence-before-notification ordering
- scientific holdout/walk-forward rules
- production execution policy

Any task requiring a protected-invariant change must be routed through the Strategy/Scientific specialists and explicitly marked `MODEL_AFFECTING` or `POLICY_AFFECTING`.

## Skill 0 — WFH Engineering Orchestrator

### Responsibility

Route each task to the smallest set of specialists needed, establish execution order, prevent conflicting edits, and produce the final readiness state.

### Required behavior

- Build a task map before modifying files.
- Detect stale audit findings by checking current `main` and relevant PRs.
- Separate independent workstreams from sequential dependencies.
- Prevent simultaneous ownership of the same file unless one specialist is explicitly primary.
- Escalate model-affecting changes to Strategy + Scientific Validation.
- Escalate production-affecting changes to Release Certification.

### Final states

- `NOT_READY`
- `ANALYSIS_COMPLETE`
- `CODE_READY`
- `MERGE_READY`
- `DEPLOY_READY`
- `DEPLOYED_UNVERIFIED`
- `PRODUCTION_VERIFIED`

## Skill 1 — WFH Repository & Architecture Auditor

Owns:

- repository map and dependency map
- architectural boundaries and ownership
- oversized/multi-responsibility modules
- duplicate/dead/legacy code
- stale issues and stale audit findings
- process-local/global state discovery
- change blast-radius analysis
- architectural debt classification

Must distinguish structural debt from reproduced correctness defects.

## Skill 2 — WFH Runtime Reliability & Performance Engineer

Owns:

- OOM/RSS growth
- single-flight/coalescing/caching
- N+1 report paths
- asyncio races, locks, semaphores, cancellation
- event-loop blocking
- SSE replay/buffer/client queues/backpressure
- slow-client amplification
- timeout/retry behavior
- load, stress, and soak investigation
- performance budgets

Current known P0 context at design time: PR #63 is still an open draft containing a regression test for concurrent `/api/execution-suitability` builds, while production implementation is not yet included in that PR. This must be rechecked on every future invocation.

## Skill 3 — WFH Backend & Data Architecture Engineer

Owns:

- FastAPI application structure
- app factory/lifespan/dependency boundaries
- service/repository/adapter decomposition
- background worker ownership
- managed SQLite access patterns
- transaction boundaries
- migrations and data-access architecture
- rollups/precomputation
- scale-readiness design

Rule: do not migrate from SQLite to PostgreSQL merely for architectural fashion. Require measured scale/coordination need before introducing a distributed persistence layer.

## Skill 4 — WFH API Contract & Schema Guardian

Owns:

- Pydantic request/response/event contracts
- `contract_version` and `schema_version`
- nested dashboard schemas
- OpenAPI compatibility
- generated TypeScript types
- SSE/polling contract parity
- backward compatibility and migration rules
- unavailable/fail/partial semantics

Goal: eliminate hidden frontend/backend semantic drift and avoid untyped `JsonObject` boundaries for core product contracts.

## Skill 5 — WFH Frontend Engineering & Dashboard UX

Sub-modes:

- `ENGINEERING`
- `UX`
- `ACCESSIBILITY`
- `PERFORMANCE`

Owns:

- Next.js/React implementation
- dashboard state architecture
- normalized updates/selectors
- SSE client and polling fallback UX
- responsive/mobile behavior
- information hierarchy
- visual consistency
- accessibility and keyboard behavior
- RTL/i18n foundation when requested
- rendering/GC/network efficiency
- visual regression coverage

Must not duplicate ranking/eligibility logic that should be canonical in backend contracts.

## Skill 6 — WFH Strategy, Score & Lifecycle Validator

Owns:

- ScoreV2
- Watch Score and coverage semantics
- FinalRanking semantics
- WATCH/FUEL-RICH/PRE-TRIGGER/ARMED/TRIGGERED/EXHAUSTED/INVALIDATED behavior where present
- anti-chase
- evidence gates
- regime/relative-weakness logic
- leverage recommendation semantics
- trigger/lifecycle causality

It answers: `Is the decision logic internally correct and coherent?`

It must not claim profitability from logic inspection alone.

## Skill 7 — WFH Scientific Backtest & Model Validation

Owns:

- point-in-time correctness
- leakage controls
- walk-forward validation
- embargo
- holdout discipline
- block/bootstrap uncertainty
- regime stratification
- concentration controls
- PF/EV/MDD/MAE/MFE
- sensitivity and parameter stability
- multiple-testing/selection bias
- promotion evidence

It answers: `Does the evidence support an edge robustly enough to justify model promotion?`

Holdout must not be used for iterative parameter selection.

## Skill 8 — WFH Market Data & Evidence Quality Engineer

Owns:

- exchange/provider data contracts
- symbol/contract identity
- timestamps and causal ordering
- freshness/staleness
- candle completeness
- mark/index/last price consistency
- funding/OI/taker-flow/derivatives evidence
- order-book and execution-observation quality
- cross-exchange confirmation
- provider disagreement
- fail-closed unavailable semantics
- outlier/data-corruption detection

The strategy layer must consume validated evidence rather than provider-specific raw assumptions.

## Skill 9 — WFH Verification & Regression Engineer

Owns the verification matrix:

- unit tests
- property-based tests
- repository/DB integration tests
- API/contract tests
- concurrency/race regression
- frontend unit/component tests
- Playwright E2E
- accessibility checks
- screenshot/visual regression
- performance benchmarks
- memory/soak tests
- deterministic replay

For reproduced bugs, prefer explicit RED evidence before implementation when practical.

## Skill 10 — WFH Security & Supply-Chain Engineer

Owns:

- CodeQL/Sonar/static security review
- dependency audit
- secrets/history leakage
- container hardening
- SBOM/image scanning/signing
- GitHub permissions/protection
- API exposure and security headers
- request limits and abuse surfaces
- SSRF/injection/path handling
- credential handling and scrubbing
- third-party package/license risk

Security findings must be evidence-backed and ranked by exploitability/impact rather than tool severity alone.

## Skill 11 — WFH Observability & Incident Response

Owns:

- structured logging
- Prometheus metrics
- Grafana/Alertmanager
- Sentry/error tracing when configured
- correlation IDs and release SHA tagging
- RSS/memory slope
- provider and worker health
- SLI/SLO design
- incident timelines
- root-cause analysis
- postmortem actions
- regression tests derived from incidents

An incident is not closed until detection, root cause, mitigation/fix, regression coverage, and operational verification are addressed or explicitly waived.

## Skill 12 — WFH Release, Migration & Production Certification

Owns final gates for:

- exact SHA and clean diff
- CI status
- backend/frontend/container verification
- dependency/security gates
- review-thread status
- migration preflight
- backup/restore readiness
- image/artifact identity
- rollback path
- deployment
- `/livez`, `/readyz`, `/healthz`
- runtime revision
- dashboard/API smoke tests
- post-deploy soak/observability

Only this skill may declare `DEPLOY_READY`, `DEPLOYED_UNVERIFIED`, or `PRODUCTION_VERIFIED`.

## Skill interaction rules

### Runtime bug

```text
Orchestrator
 -> Runtime Reliability
 -> Verification
 -> Observability (when production/runtime evidence is involved)
 -> Release Certification (if deployed)
```

### Frontend/dashboard bug

```text
Orchestrator
 -> API Contract Guardian (if payload/semantics involved)
 -> Frontend Engineering & Dashboard UX
 -> Verification
```

### Strategy/model change

```text
Orchestrator
 -> Market Data & Evidence Quality
 -> Strategy, Score & Lifecycle Validator
 -> Scientific Backtest & Model Validation
 -> Verification
 -> Release Certification
```

### Refactor

```text
Orchestrator
 -> Repository & Architecture Auditor
 -> Backend & Data Architecture OR Frontend Engineering
 -> API Contract Guardian
 -> Verification
```

### Production incident

```text
Orchestrator
 -> Observability & Incident Response
 -> Runtime Reliability / Backend / Frontend specialist as indicated
 -> Verification
 -> Release Certification
```

## Skill file layout

Canonical repository source will be one skill per directory with one authoritative body:

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
```

If a consuming environment requires a different discovery directory, use a thin index/adapter rather than maintaining duplicate skill bodies.

## Skill content contract

Each `SKILL.md` must contain:

1. name and precise trigger description
2. scope and exclusions
3. required inputs/context discovery
4. protected invariants
5. step-by-step workflow
6. evidence taxonomy
7. tooling guidance
8. stop/escalation conditions
9. verification checklist
10. output/readiness states
11. handoff rules to other WFH skills
12. examples of correct invocation and common failure modes

Skills must be operational instructions, not long essays. Domain details should be included only when they alter behavior.

## External tools and plugin policy

External tools are optional capabilities, not hard runtime dependencies of the skill system.

- GitHub: repository/PR/CI source of truth when connected
- CodeRabbit/Sonar/CodeQL: review/security evidence when available
- Sentry/Prometheus/Grafana: runtime/incident evidence when available
- Linear: optional issue/work tracking
- StrategyTune or equivalent: optional independent research/backtest support
- Firecrawl/Parallel Search/web research: only for external documentation/research, not as a substitute for repository evidence
- Skillquiver/Duende-style skill libraries: useful for skill design patterns when available; never required to run WFH logic
- GeoAI-style skills: excluded from the core set unless a concrete geospatial/infrastructure-latency requirement appears

Unavailable plugins must never block core repository work.

## Anti-patterns prohibited across the skill system

- trusting an old audit without checking current SHA
- fixing a symptom without reproducing or identifying the causal path
- adding infrastructure complexity without measured need
- mixing model changes into reliability/refactor PRs
- duplicating backend ranking/eligibility logic in frontend
- calling a partial Watch Score a calibrated probability
- selecting parameters on holdout data
- declaring success from a single targeted test
- merging/deploying while exact-head checks are unknown
- treating unavailable evidence as a negative market signal
- claiming production verification without runtime checks

## Implementation acceptance criteria

The skill-system implementation is complete when:

1. all thirteen `SKILL.md` files exist with no duplicated authoritative workflows;
2. `skills/waterfallhunter/README.md` documents routing and invocation examples;
3. the orchestrator contains deterministic routing and escalation rules;
4. each specialist has explicit scope/exclusions/protected invariants;
5. strategy and scientific validation remain separate domains;
6. runtime reliability includes OOM, single-flight, SSE, concurrency, and soak concerns;
7. API/schema ownership is explicit;
8. release certification is the sole owner of production-readiness states;
9. all skill files pass a consistency/self-review for contradictory instructions, stale hard-coded findings, placeholders, and unsafe implicit authorizations;
10. no application/runtime/model code is changed by the skill-system PR unless separately approved.

## Self-review checklist for this design

- No `TBD` or placeholder requirements.
- Thirteen components are explicitly named and bounded.
- Skill overlap has a primary owner and handoff rule.
- Model/strategy and scientific-validation responsibilities are separated.
- Production-readiness authority is centralized.
- Current PR #63 is described only as design-time context and explicitly requires freshness re-check.
- External plugins are optional, so missing integrations cannot make the system unusable.
- No implementation or production change is authorized by this design document alone.
