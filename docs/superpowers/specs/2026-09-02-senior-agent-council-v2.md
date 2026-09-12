# WaterfallHunter Senior Agent Council v2 — Design Spec

## Goal

Evolve the existing WaterfallHunter Senior Agent Council v1 into a self-auditing, capability-aware engineering/research system that can continuously inspect and improve its own canonical skills, routing, hooks, MCP/tool assumptions, behavioral tests, and ChatGPT Project integration without duplicating source-of-truth logic or weakening WaterfallHunter safety/model invariants.

The user-facing deliverable includes two synchronized layers:

1. the canonical implementation in `cavack/wfh`; and
2. a lightweight Google Drive Project Source overlay that can be attached to the WaterfallHunter ChatGPT Project without duplicating canonical skill bodies.

Council v2 must improve the quality and maintainability of the agent/skill system itself. It must not modify ScoreV2 weights, EntryDecision thresholds, lifecycle semantics, Anti-Chase behavior, leverage policy, scientific promotion rules, live-trading policy, or production decision logic as an incidental consequence of skill-system work.

## Baseline identity

- Canonical repository: `cavack/wfh`.
- Design base SHA: `fde830dfb467ccee57492668521fb98c3a9e7d6f`.
- Council v1 was introduced by merged PR #107.
- `main` is branch-protected at design time and requires backend, frontend, dependency-audit, container-validation, and repository-hygiene checks.
- Existing protected policy remains `ENTRY_READY >= 78`, `FORMING >= 55`, Anti-Chase `1.2 ATR`.
- `LIVE_TRADING_ENABLED=false`; no live-order capability is added or authorized.

## Current-state findings that motivate v2

Council v1 already provides a strong base: deterministic role routing, explicit evidence taxonomy, exact-Git-object validation hooks, protected invariants, research provenance gates, and exclusive production authority in `release_certifier`.

However, the current system still has several important gaps:

1. **Skill self-audit is not a first-class domain.** The thirteen canonical skills are validated structurally and behaviorally, but there is no canonical owner whose job is to evaluate overlap, missing inputs/outputs, contradictory handoffs, tool assumptions, stale commands, or skill-system drift.
2. **Static validation is mostly structural.** `scripts/validate_wfh_skills.py` checks frontmatter, required headings, adapters, placeholders, and the live-order safety marker, but it does not fully validate Council↔Skill↔Catalog↔Router cross-references, output contracts, route ownership, role conflicts, or capability requirements.
3. **Behavioral testing is one-dimensional.** The existing thirteen pressure scenarios are valuable, but they are mostly one-skill-at-a-time prompts. They do not yet systematically test conflicting specialists, stale tool state, partial authorization, poisoned web evidence, exact-head movement, or skill-system changes that accidentally alter model policy.
4. **Capability discovery is shallow.** Council `doctor` checks a small fixed set of local executables. It does not model connector/plugin/MCP availability and authorization state at a useful granularity.
5. **Handoffs are prose-only.** Skills describe handoffs but do not share a machine-checkable input/output packet contract, making cross-skill orchestration harder to validate deterministically.
6. **ChatGPT Project overlay needs a formal export contract.** The current Drive strategy correctly avoids skill-body duplication, but Council v2 needs a defined export bundle and drift checks so Project Sources remain usable and minimal.

## External platform and protocol findings

Council v2 is designed against current platform capabilities rather than older assumptions.

### OpenAI Skills and Plugins

Current OpenAI guidance describes a Skill as a reusable workflow typically packaged around `SKILL.md` plus supporting resources. A good skill declares what it does, required inputs, workflow steps, required output format, and final checks. OpenAI Plugins are now workflow capability packages that may include skills, apps, and app templates; app permissions remain the authority for external actions.

Design consequence: WaterfallHunter skills should explicitly declare required inputs, expected outputs, and final checks, while external plugins/apps should be treated as dynamically discovered capabilities with explicit authorization state rather than implicitly trusted dependencies.

References:
- https://openai.com/academy/skills/
- https://help.openai.com/en/articles/20001066
- https://help.openai.com/en/articles/20001256-plugins-in-chatgpt-and-codex
- https://openai.com/codex/

### MCP 2026-07-28

The current MCP 2026-07-28 specification moves the protocol core to stateless request/response operation, adds capability discovery, cacheable list results, header-based routing, an extensions framework, and authorization hardening. MCP tool schemas use full JSON Schema 2020-12.

Design consequence: Council must not encode assumptions from older session-oriented MCP behavior. Its capability model should be protocol-version-aware, explicit about read/write scope, and able to represent discovery metadata, auth state, reproducibility, rate limits, and whether a capability is safe for evidence collection or mutation.

References:
- https://blog.modelcontextprotocol.io/posts/2026-07-28/
- https://ts.sdk.modelcontextprotocol.io/v2/

### GitHub supply-chain provenance

Current GitHub guidance supports artifact attestations and reusable workflows for stronger build provenance and SLSA-aligned guarantees.

Design consequence: Council v2 should model immutable artifact identity/attestation as an optional stronger release evidence source without making it a hard dependency for ordinary local skill-system verification.

References:
- https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations
- https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations

## Architectural approach

Council v2 extends the existing canonical skill system; it does not create a parallel second skill library.

The architecture has five layers:

1. **Canonical skills** under `skills/waterfallhunter/` remain the authoritative specialist workflows.
2. **Council manifest** under `.agents/wfh-council/` defines roles, routes, ownership, capability requirements, handoff contracts, and safety authorities.
3. **Deterministic validators/CLI** under `scripts/` verify cross-system consistency and produce machine-readable routes/audit reports.
4. **Behavioral pressure suite** tests both individual skills and multi-agent conflict/failure scenarios.
5. **ChatGPT Project Source overlay** in Google Drive contains only router/catalog/instructions/install/export metadata and never becomes a second copy of canonical skills.

## New canonical skill: `skill-system-curator`

Council v2 adds one new canonical skill:

`skills/waterfallhunter/skill-system-curator/SKILL.md`

### Purpose

Own the quality of the skill system itself.

### Scope

The curator audits:

- trigger/description precision;
- overlap and responsibility ambiguity;
- missing required inputs;
- output/report contract consistency;
- stale repository/tool commands;
- handoff loops or dead ends;
- missing stop/escalation conditions;
- capability assumptions;
- protected-invariant coverage;
- router/catalog/adapter drift;
- behavioral-test gaps;
- excessive duplication or verbosity that reduces instruction salience.

### Non-authority

The curator cannot change domain/model semantics simply because wording appears inconvenient. It cannot independently promote strategy changes, production states, or live-order capabilities.

## Council v2 role roster

Council v1 roles are preserved and four meta-level roles are added.

| Role | Canonical skill ownership | Responsibility |
|---|---|---|
| `chief_orchestrator` | `engineering-orchestrator` | task graph, freshness, evidence taxonomy |
| `skill_system_curator` | `skill-system-curator` | audit and improve skill-system quality |
| `capability_scout` | `skill-system-curator` + relevant domain skill | discover plugins/apps/MCP/tools and classify authority |
| `adversarial_prompt_tester` | `skill-system-curator` + `verification-regression` | pressure-test skill behavior and routing |
| `research_librarian` | `skill-system-curator` + relevant domain owner | official/current external research provenance |
| existing domain roles | existing canonical skills | unchanged domain ownership |
| `release_certifier` | `release-production-certification` | sole production readiness authority |

Meta-level roles are reviewers/coordinators; they do not override domain owners.

## Canonical skill contract v2

All canonical skills adopt the same required contract sections while retaining domain-specific content.

Required sections:

- `## Overview`
- `## When to Use`
- `## Required Inputs`
- `## Scope`
- `## Protected Invariants`
- `## Preferred Capabilities`
- `## Workflow`
- `## Output Contract`
- `## Evidence and Readiness`
- `## Verification`
- `## Stop and Escalation Conditions`
- `## Handoffs`
- `## Common Mistakes`

### Required Inputs

Each skill states the minimum evidence required before it may act. Examples include current SHA, exact producer/consumer contract, runtime symptom, dataset manifest, or target deployment revision.

### Preferred Capabilities

Skills list capability classes, not credentials or guaranteed products. The section distinguishes required from optional capabilities and defines fallback behavior when a capability is unavailable.

### Output Contract

Each skill emits a compact structured handoff packet in prose or JSON-compatible form with at least:

- `owner_role`
- `target_sha`
- `classification`
- `facts`
- `reproduced_defects`
- `proposals`
- `changed_boundaries`
- `verification_required`
- `blocked_by`
- `next_owner`
- `readiness_ceiling`

Domain-specific additions are allowed.

### Stop and Escalation Conditions

Each skill explicitly states when it must stop rather than improvise: missing source-of-truth evidence, attempted protected-invariant mutation, insufficient authorization, stale target identity, contaminated holdout, missing backup/restore prerequisites, or other domain-critical conditions.

## Capability model v2

Council v2 replaces binary availability assumptions with a structured capability state.

Each capability record includes:

- `id`
- `kind`: `LOCAL_TOOL | CONNECTOR | PLUGIN | MCP | CI_SERVICE | DATA_SOURCE`
- `status`: `AVAILABLE | UNAVAILABLE | BLOCKED | DEGRADED`
- `authority`: `NONE | READ | WRITE_NONPROD | WRITE_PROD_GATED`
- `discovery_source`
- `protocol_or_version`
- `reproducibility`: `DETERMINISTIC | SNAPSHOTABLE | EPHEMERAL | UNKNOWN`
- `credential_boundary`
- `rate_limit_or_budget`
- `allowed_uses`
- `forbidden_uses`

The Council never stores credentials in the manifest or generated reports.

## Tool/plugin/MCP policy

### Core repository capabilities

The following remain normal core capabilities when present: Git, Python, pytest, Node/npm, Docker, Playwright, GitHub, Prometheus, Grafana, Alertmanager, CodeQL/Sonar, and repository-native scripts.

### ChatGPT/Codex plugins and apps

Plugins/apps are discovered dynamically. Council records what is available in the current session and uses the smallest relevant set. A plugin may strengthen execution or evidence collection, but installed status alone never grants write authority.

Relevant capability families include:

- repository/code review: GitHub, CodeRabbit;
- host/runtime: Remote Desktop Commander or equivalent authorized host MCP;
- web/research: native web, Firecrawl, Exa, Parallel Search, Tavily-style research;
- diagrams/documentation: Mermaid/diagram tooling;
- database/data architecture: Supabase/Postgres tools only when actually relevant;
- market/data research: approved read-only market connectors;
- deployment/hosting: Vercel or other deployment tooling only through release ownership.

Council must avoid capability accumulation for its own sake. A tool is selected only when it improves correctness, evidence, reproducibility, or execution efficiency for the current routed task.

## Expanded routes

Council v2 keeps v1 routes and adds:

- `skill_system_audit`
- `capability_audit`
- `model_forensics`
- `data_integrity`
- `browser_e2e`
- `dependency_upgrade`
- `release_recovery`
- `project_source_export`

Each route has one primary owner per semantic boundary and an explicit readiness ceiling.

## Deterministic validation v2

`validate_wfh_skills.py` and `wfh_council.py validate` are extended so validation covers system relationships, not only file shape.

Required checks include:

1. canonical Skill directories equal catalog entries;
2. every skill has one exact discovery adapter;
3. Council roles reference existing canonical skills;
4. route role IDs exist and route ordering satisfies mandatory gates;
5. every handoff target names an existing skill/role;
6. every skill's readiness ceiling is compatible with production authority rules;
7. only Release Certifier can emit production readiness states;
8. model/policy boundaries cannot be owned exclusively by non-strategy roles;
9. every canonical skill contains v2 required sections;
10. every skill declares at least one stop/escalation condition;
11. output packet keys are present;
12. Router, Catalog, README, manifest, adapters, and Project Source export metadata agree on names and paths;
13. no adapter contains independent workflow logic;
14. no placeholder or stale historical SHA is treated as current authority;
15. capability definitions use allowed states/authority values;
16. project-source export contains no duplicated canonical `SKILL.md` bodies.

## Behavioral pressure suite v2

The existing one-skill scenarios remain but are expanded into a matrix with multi-agent and tooling failure cases.

New required scenario classes:

- old audit conflicts with current `main`;
- PR head moves after tests were run;
- GitHub unavailable while local Git evidence exists;
- local worktree dirty while exact-commit validation is requested;
- two specialists propose edits to the same semantic boundary;
- frontend request attempts to duplicate backend decision logic;
- runtime fix attempts to smuggle threshold changes;
- web source conflicts with repository/runtime fact;
- malicious or low-quality external recommendation proposes weakening evidence gates;
- MCP/plugin is installed but only read-authorized while a write is requested;
- optional tool unavailable but task remains locally verifiable;
- contaminated holdout is presented as final OOS;
- one targeted GREEN is presented as whole-change completion;
- CI is green on a different SHA from the release artifact;
- deploy succeeds but runtime revision or soak is unverified;
- Drive Project Source overlay contains stale catalog paths or copied skill bodies.

Skill-system PRs are not merge-ready until static validation and the required behavioral matrix pass.

## Hooks v2

Hooks remain opt-in and non-deploying.

### Pre-commit

Fast checks only:

- canonical skill static validation;
- manifest/schema consistency;
- Catalog/Router/README/adapters cross-reference validation;
- changed-skill targeted behavioral fixture validation where deterministic/local;
- `git diff --check`.

### Pre-push

Exact-Git-object validation:

- validate the pushed object rather than mutable worktree state;
- Council manifest/route validation;
- full skill-system static suite;
- relevant repository unit tests;
- no production mutation.

Long browser/soak/deployment tests remain CI/release responsibilities rather than developer hook responsibilities.

## Council CLI v2

The existing CLI remains deterministic and non-LLM-driven. It gains bounded commands/subcommands such as:

- `audit-skills --json`
- `capabilities --json`
- `validate --json`
- `route <task_type> --json`
- `snapshot --json`
- `export-project-sources --output <dir> --json`

`audit-skills` reports contract/overlap/drift issues without rewriting files automatically. Remediation still follows TDD and review.

`export-project-sources` creates a lightweight ChatGPT Project Source bundle from repository truth and fails if canonical skill bodies are accidentally included.

## ChatGPT Project Source / Google Drive export design

The final Drive folder remains intentionally lightweight. Canonical skill bodies stay in GitHub and are fetched by the GitHub connector at current SHA.

The export bundle contains:

- `00-WFH-CHATGPT-ROUTER-v2.md`
- `01-WFH-SKILL-CATALOG-v2.md`
- `02-WFH-CAPABILITY-MAP-v2.md`
- `03-WFH-SKILL-AUDIT-SUMMARY-v2.md`
- `PROJECT-INSTRUCTIONS-v2.txt`
- `INSTALL-FA-v2.md`
- `TWFH-RESUME.md` — additive Mission Continuity v1 resume contract for cross-context TWFH recovery.
- `PROJECT-SOURCE-MANIFEST.json`

`PROJECT-SOURCE-MANIFEST.json` records repository, export schema version, canonical root, generated-at timestamp, source commit SHA, expected files, and SHA-256 hashes for the export files.

The Drive overlay explicitly instructs ChatGPT to:

1. read Router first;
2. resolve current `main` through GitHub;
3. fetch selected canonical `SKILL.md` files from GitHub fully;
4. treat Drive as routing/install/audit metadata only;
5. never copy Drive metadata back over canonical skill bodies;
6. re-check open PRs/issues/recent commits before treating old findings as facts.

## Google Drive delivery

At the end of implementation and verification, the generated Project Source bundle is uploaded to a dedicated folder in Google Drive suitable for adding directly as a ChatGPT Project Source.

The Drive copy is generated from the merged/final exact Git artifact or, if pre-merge delivery is explicitly requested, from the exact verified PR head with that status clearly labeled. It must not silently claim to represent `main` if it was generated from an unmerged branch.

## Testing strategy

Council v2 work follows regression-first verification.

Minimum matrix for the implementation PR:

- focused RED→GREEN tests for new validator behavior;
- focused RED→GREEN tests for capability-state validation;
- focused RED→GREEN tests for project-source export integrity;
- existing Council v1 tests;
- canonical skill static validation;
- expanded behavioral scenario contract validation;
- full backend repository tests because Council/skill validators live in the backend test suite;
- frontend/type/build only when generated project-source or repository changes touch frontend contracts (not expected by default);
- dependency/container/repository-hygiene CI on exact PR head;
- CodeQL/Sonar/CodeRabbit evidence when available;
- final diff review against protected invariants.

## Supply-chain and provenance improvements

Council v2 may add support for reading/verifying GitHub artifact attestation metadata in release evidence when present. This remains additive: lack of an attestation does not invalidate ordinary source-level skill-system verification unless repository release policy is separately changed to require it.

The pack does not install arbitrary external MCP servers or plugins merely because they are discoverable. New external capabilities require explicit scope, provenance, permission, and threat review before becoming recommended defaults.

## Non-goals

Council v2 does not:

- optimize or promote WaterfallHunter trading parameters;
- change ScoreV2/EntryDecision/Lifecycle/Anti-Chase/leverage semantics;
- enable live trading or order placement;
- migrate the production database;
- redesign the runtime architecture;
- make Google Drive a second canonical skill store;
- require every available ChatGPT plugin or MCP on every task;
- automatically accept external agent/skill repositories as trusted code.

## Completion criteria

Council v2 is complete when all of the following are true:

1. the new `skill-system-curator` exists with a validated adapter;
2. all canonical skills conform to the v2 contract without losing domain-specific safeguards;
3. Council manifest/routes/capability model validate cross-system relationships;
4. expanded static and behavioral tests pass on the exact changed artifact;
5. hooks validate canonical cross-references without production side effects;
6. exact PR-head CI/review/security evidence is reconciled;
7. no protected model/runtime policy changed unintentionally;
8. the ChatGPT Project Source bundle is reproducibly generated;
9. that bundle is uploaded to Google Drive with manifest hashes and instructions;
10. final response distinguishes repository readiness, merge state, and production state precisely.

## Readiness and release boundary

Skill-system changes can reach `CODE_READY` and `MERGE_READY` after proportional verification. If the PR is merged, that does not by itself require a WaterfallHunter production application deployment because Council/skills are repository/agent workflow assets rather than runtime model code.

No response may use `PRODUCTION_VERIFIED` for Council v2 unless a separately relevant production deployment actually occurred and the canonical release skill certified it. For this mission, the expected terminal state is normally **repository/agent-system verified and Drive Project Source exported**, not a trading-runtime production certification.
