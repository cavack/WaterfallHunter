# WaterfallHunter Skill System — Post-Review Errata

Date: 2026-08-27
Applies to: `docs/superpowers/specs/2026-08-27-wfh-skill-system-design.md` and `docs/superpowers/plans/2026-08-27-wfh-skill-system.md`
Status: authoritative clarification for post-review implementation semantics

The original design and implementation plan are retained as historical design/execution snapshots. This document records review-driven corrections without rewriting the sequence of work that was actually performed.

## Discovery surface

Canonical skill bodies remain under `skills/waterfallhunter/<skill-name>/SKILL.md`. Agent discovery is exposed through thin `.agents/skills/<skill-name>/SKILL.md` adapters. Each adapter loads the shared WaterfallHunter README and then the matching canonical skill. Adapters contain no independent workflow.

## Orchestrator readiness authority

Where the design or plan says the orchestrator produces a final readiness state, read that as: the orchestrator aggregates and reports engineering readiness evidence and may report the non-production states `NOT_READY`, `ANALYSIS_COMPLETE`, `CODE_READY`, and `MERGE_READY`.

The orchestrator does not declare `DEPLOY_READY`, `DEPLOYED_UNVERIFIED`, or `PRODUCTION_VERIFIED`. Declaration of those three states remains exclusively owned by `release-production-certification`.

## Skill content contract

The post-review content contract is intentionally split to avoid duplicating shared policy prose in every canonical skill while still making direct discovery safe:

- Every canonical `SKILL.md` must contain valid frontmatter, a precise trigger, scope/exclusions, context-discovery guidance where relevant, an explicit `## Protected Invariants` section, workflow, evidence/readiness behavior, verification, handoffs, and common failure modes.
- The shared `skills/waterfallhunter/README.md`, which every discovery adapter loads first, supplies the shared evidence taxonomy, external-tool policy, common stop/escalation conditions, routing/correct-invocation examples, freshness fallback, readiness ownership, and categorical safety boundary.
- `scripts/validate_wfh_skills.py` enforces both the per-skill and shared portions, plus the discovery adapters.

This supersedes any interpretation that all shared policy text must be duplicated verbatim into each skill body.

## Frontmatter validation

The validator accepts a deliberately strict YAML subset for repository skill metadata: plain scalar `name` and `description` fields only. Unsupported or quoted YAML syntax, duplicate fields, unknown fields, malformed delimiters, and malformed quoted values are rejected instead of normalized.

## Readiness ownership validation

Tests allow ordinary references to production state names in non-release skills but reject language that grants declaration/certification authority near those states. The release skill must contain all three production states and identify itself as the sole authority.

## Placeholder validation

The executable placeholder gate is the repository validator over the intended skill artifacts. The self-referential grep shown in the historical implementation plan is not an authoritative executable gate because it can match its own validation prose. The current acceptance claim is: no unintended placeholder prose in validated skill artifacts.

## External evidence fallback

GitHub, CodeRabbit, Sonar, Sentry, and similar integrations remain optional evidence sources. When remote PR/issue evidence is unavailable, the agent records that limitation and continues with locally available branch/SHA/recent-commit evidence when the task is still locally verifiable.

## Live-order boundary

The current skill system must not authorize, design, implement, or enable live order placement. User consent plus ordinary release gates is not sufficient under the current repository policy. Any future execution capability requires a separately reviewed safety design and explicit repository-policy change before ordinary release gates may apply.
