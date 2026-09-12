# WaterfallHunter — ChatGPT Project Router v2

GitHub `cavack/WFH-ORG` is the canonical source. This Drive/Project Sources bundle is routing and installation metadata only; it intentionally does not duplicate canonical skill bodies.

## Canonical TWFH resume intent

When the user says exactly `ادامه کار گروهی`, resolve the `TWFH` project and repository `cavack/WFH-ORG` through continuity protocol `wfh_mission_continuity_v1` and Council route `mission_continuity` **before** ordinary WaterfallHunter task routing. Read `TWFH-RESUME.md`, locate the durable active mission and latest valid checkpoint, reconcile drift/interrupted steps, and continue only from its exact `next_action`. Never restart a broad audit or reconstruct missing state from transcript guesses.

Canonical mapping: `phrase=ادامه کار گروهی | project=TWFH | repository=cavack/WFH-ORG | protocol=wfh_mission_continuity_v1 | route=mission_continuity`

## Mandatory workflow

1. Read `PROJECT-SOURCE-MANIFEST.json`. For a certified bundle require `source_worktree_dirty=false`, freeze `source_commit_sha` as the canonical target for the entire audit, and verify that SHA exists in GitHub. If runtime provenance is absent, resolve one target SHA once and record it before continuing.
2. Read this router and `01-WFH-SKILL-CATALOG-v2.md`.
3. Select the smallest relevant Council route and canonical skill set.
4. Fetch every selected `skills/waterfallhunter/<skill>/SKILL.md` completely from that same frozen `source_commit_sha`; do not re-resolve a moving branch between skill fetches.
5. Classify material findings as `VERIFIED_FACT`, `REPRODUCED_DEFECT`, `INFERENCE`, `DEBT`, or `PROPOSAL`.
6. Treat tool/plugin/MCP presence and authorization as separate facts. Missing optional capability is `UNAVAILABLE`, never guessed evidence. Resume observations must meet each checkpointed minimum authorization; capability presence alone is insufficient. Evaluate structured preconditions only from validated state or independently observed evidence; unsupported or unverifiable structured preconditions fail closed as `RESUME_BLOCKED`.
7. Preserve protected model, lifecycle, anti-chase, provenance, persistence-before-notification, scientific-validation, and execution-policy invariants unless separately authorized through their owning skills.
8. Route code completion through `verification-regression` and production states only through `release-production-certification`.

## Council v2 role map

- `chief_orchestrator` → `engineering-orchestrator`
- `architecture_adversary` → `repository-architecture-auditor`
- `market_evidence_forensics` → `market-data-evidence-quality`
- `strategy_owner` → `strategy-score-lifecycle`
- `quant_validation_lead` → `scientific-backtest-validation`
- `false_positive_hunter` → `market-data-evidence-quality + scientific-backtest-validation`
- `false_negative_hunter` → `strategy-score-lifecycle + scientific-backtest-validation`
- `runtime_lead` → `runtime-reliability-performance`
- `backend_data_lead` → `backend-data-architecture`
- `contract_guardian` → `api-contract-schema-guardian`
- `decision_ux_lead` → `frontend-dashboard-ux`
- `security_lead` → `security-supply-chain`
- `incident_telemetry_lead` → `observability-incident-response`
- `skill_system_curator` → `skill-system-curator`
- `capability_scout` → `skill-system-curator`
- `adversarial_prompt_tester` → `skill-system-curator + verification-regression`
- `research_librarian` → `market-data-evidence-quality + scientific-backtest-validation`
- `regression_lead` → `verification-regression`
- `release_certifier` → `release-production-certification`

## Council v2 routes

- `deep_audit`: `chief_orchestrator → architecture_adversary → market_evidence_forensics → runtime_lead → backend_data_lead → contract_guardian → security_lead → incident_telemetry_lead → regression_lead`
- `model_optimization`: `chief_orchestrator → market_evidence_forensics → strategy_owner → quant_validation_lead → false_positive_hunter → false_negative_hunter → regression_lead → release_certifier`
- `runtime_incident`: `chief_orchestrator → incident_telemetry_lead → runtime_lead → backend_data_lead → regression_lead → release_certifier`
- `frontend_contract`: `chief_orchestrator → contract_guardian → decision_ux_lead → regression_lead`
- `security_review`: `chief_orchestrator → security_lead → regression_lead → release_certifier`
- `production_release`: `chief_orchestrator → regression_lead → release_certifier`
- `skill_system_audit`: `chief_orchestrator → capability_scout → skill_system_curator → adversarial_prompt_tester → regression_lead`
- `data_integrity`: `chief_orchestrator → market_evidence_forensics → backend_data_lead → contract_guardian → regression_lead`
- `browser_e2e`: `chief_orchestrator → contract_guardian → decision_ux_lead → regression_lead`
- `dependency_upgrade`: `chief_orchestrator → security_lead → regression_lead → release_certifier`
- `release_recovery`: `chief_orchestrator → incident_telemetry_lead → runtime_lead → regression_lead → release_certifier`
- `external_research`: `chief_orchestrator → research_librarian → market_evidence_forensics → quant_validation_lead → regression_lead`
- `mission_continuity`: `chief_orchestrator → capability_scout → skill_system_curator → regression_lead`

## Safety

`LIVE_TRADING_ENABLED=false` remains mandatory. Missing market data is unavailable evidence, not directional evidence. Only canonical backend decision logic may determine ranking/eligibility. CI success alone is not production verification.
