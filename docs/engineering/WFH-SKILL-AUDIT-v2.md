# WaterfallHunter Skill System Audit v2

## Scope and evidence

Audit target: the fourteen canonical repository-local skills, discovery adapters, Council manifest/docs/CLI, validation hooks, static validator, pressure scenarios, and ChatGPT Project Sources overlay on `feat/senior-agent-council-v2-20260902`, based on `main` `fde830dfb467ccee57492668521fb98c3a9e7d6f`.

Evidence classes follow the repository taxonomy. Current source and executable tests are `VERIFIED_FACT`; historical behavioral transcripts are retained as historical evidence; external skill/MCP guidance is used only to inform structure and is not WaterfallHunter runtime/model truth.

## External authoring/research inputs

- OpenAI developer documentation exposes Skills as versioned project resources and accepts skill directory/zip content. This supports treating skill artifacts as versioned, auditable units rather than prompt fragments.
- The open Agent Skills format describes `SKILL.md` as the required entrypoint and emphasizes progressive disclosure: discovery metadata first, full instructions on activation, supporting resources on demand.
- Current Agent Skills authoring guidance emphasizes concise, triggerable workflows, real execution/evals, deterministic scripts for checks agents might otherwise guess, and keeping large details out of the always-loaded skill body.
- MCP specification release `2026-07-28` includes authorization hardening and capability-oriented protocol evolution. Council v2 therefore models capability availability separately from authorization and grants no production mutation authority merely because an MCP/plugin exists.

These sources justify system-structure changes only. They do not authorize changes to ScoreV2, lifecycle, EntryDecision, Anti-Chase, leverage, or production policy.

## Canonical skill disposition

| Skill | Disposition | Finding / action | Verification |
|---|---|---|---|
| engineering-orchestrator | TIGHTEN | Strong freshness/scope owner; added explicit input/evidence/tool/output/stop contract. | static validator + Council regression |
| repository-architecture-auditor | TIGHTEN | Correct debt-vs-defect boundary retained; standardized evidence/output/stop contract. | static validator |
| runtime-reliability-performance | TIGHTEN | Root-cause vs containment discipline retained; explicit capability/evidence contract added. | static + Council regression |
| backend-data-architecture | TIGHTEN | Evidence-gated scale/migration stance retained; standardized contract added. | static validator |
| api-contract-schema-guardian | TIGHTEN | Canonical-producer and SSE/poll parity semantics retained; explicit output/stop contract added. | static validator |
| frontend-dashboard-ux | TIGHTEN | Backend-only decision authority retained; standardized transport/evidence handoff contract added. | static validator |
| strategy-score-lifecycle | TIGHTEN | Model/policy ownership remains isolated; no threshold or eligibility semantics changed. | static validator + protected-invariant manifest test |
| scientific-backtest-validation | TIGHTEN | Holdout/WFO/promotion discipline retained; explicit evidence/output contract added. | static validator + Council research tests |
| market-data-evidence-quality | TIGHTEN | `UNAVAILABLE` semantics and identity/freshness ownership retained. | static validator + protected-invariant manifest test |
| verification-regression | TIGHTEN | Exact-artifact/blast-radius ownership retained; standardized stop/output contract added. | focused Council/hook/export tests |
| security-supply-chain | TIGHTEN | Scanner-vs-validated-risk distinction retained; tool authorization/output contract added. | static validator |
| observability-incident-response | TIGHTEN | Recovery-vs-closure distinction retained; standardized evidence/output contract added. | static validator |
| release-production-certification | TIGHTEN | Sole production-readiness authority retained unchanged. | manifest exclusivity test |
| skill-system-curator | ADD | New meta-owner for skill triggers, overlap, handoffs, adapters, validator coverage, capability assumptions, and Project Source packaging. It cannot override domain owners. | canonical/adaptor inventory + Council route tests |

No domain skill is removed or merged. Their overlap is mostly shared safety/evidence policy, while their semantic decision ownership remains distinct; merging them would make trigger selection and authority boundaries less precise.

## System-level findings

| Area | Classification | Finding | v2 disposition |
|---|---|---|---|
| self-audit ownership | DEBT → addressed | v1 had no canonical owner for auditing the skill system itself. | add `skill-system-curator` |
| capability discovery | DEBT → addressed | v1 listed tools but did not encode authorization separately from presence. | explicit capability records/statuses |
| role/tool drift | DEBT → addressed | prose/manifest/tool lists could diverge without a single validation contract. | Council v2 manifest validation + skill validator |
| skill size/context | VERIFIED_FACT | all canonical skills are far below the 500-line progressive-disclosure ceiling. | validator now rejects >500 lines |
| exact Git hooks | VERIFIED_FACT | pre-commit validates staged index; pre-push validates archived pushed SHA rather than mutable worktree. | retain and expand v2 regression set |
| behavioral tests | PARTIAL EVIDENCE | thirteen historical pressure scenarios exist; two historical RED baselines did not reproduce the shortcut. | add curator/system scenarios; do not fabricate fresh-context runs when no subagent harness is available |
| Project Sources | DEBT → addressed | previous overlay had no Council v2 capability map/hash manifest. | deterministic seven-file v2 overlay with SHA-256 |
| domain semantics | VERIFIED_FACT | this v2 change does not require model/runtime decision changes. | preserve `78 / 55 / 1.2 ATR`, signal-only policy |

## Behavioral-validation limitation

Repository-local static/focused tests can prove file/manifest/hook/export contracts, but they cannot prove how a fresh independent model behaves after skill activation. The existing RED/GREEN transcripts remain historical evidence. This execution environment does not expose a fresh subagent harness, so Council v2 records that behavioral rerun as `UNAVAILABLE` rather than manufacturing GREEN evidence.

## Final recommendation

Keep the fourteen-skill architecture. Use Council roles for adversarial personas and capability scouting rather than creating more canonical skills unless a future role gains a stable, distinct, repeatable workflow. Prefer deterministic validators/scripts over prose for inventory, hashes, routes, and capability contracts. Keep Drive as a lightweight router/catalog/instructions overlay and always fetch canonical skill bodies from the current GitHub target SHA.
