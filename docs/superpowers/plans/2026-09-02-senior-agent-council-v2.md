# Senior Agent Council v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve WaterfallHunter's repository-local skill system into Council v2 with a canonical skill-system curator, explicit capability/authority discovery, cross-system contract validation, adversarial behavioral tests, stronger validation-only hooks, and a Google Drive Project Sources export that keeps GitHub as the canonical source of truth.

**Architecture:** Preserve the existing thirteen canonical WaterfallHunter skills as domain authorities, add one meta-skill that audits the skill system itself, and make Council routing/validation consume machine-readable contracts rather than prose conventions. Extend the existing deterministic Python validators and Council CLI instead of introducing a second runtime or external service. Export only a lightweight ChatGPT Project overlay to Google Drive; canonical `SKILL.md` bodies remain in GitHub.

**Tech Stack:** Python 3.13, Markdown/YAML-frontmatter skill files, JSON Council manifest, Git hooks, pytest, GitHub Actions, ChatGPT/GitHub connector, Google Drive connector.

**Spec:** `docs/superpowers/specs/2026-09-02-senior-agent-council-v2.md`

## Global Constraints

- Repository: `cavack/wfh`.
- Base branch: `main`; implementation branch: `feat/senior-agent-council-v2-20260902`.
- Preserve `LIVE_TRADING_ENABLED=false`; no live-order design, implementation, or enablement.
- Preserve current protected model policy: `ENTRY_READY >= 78`, `FORMING >= 55`, Anti-Chase `1.2 ATR` unless a separately authorized model-promotion workflow changes it.
- Missing/stale evidence remains `UNAVAILABLE` rather than bearish/bullish evidence.
- Frontend never becomes a second source of scoring/ranking truth.
- Persistence-before-notification and immutable signal provenance remain unchanged.
- Council/skill changes must not deploy or mutate Production.
- `verification-regression` is required before completion claims; only `release-production-certification` owns production readiness states.
- Google Drive export must be a routing/install overlay; canonical skill bodies remain authoritative in GitHub.

---

### Task 1: Add RED contracts for Council v2 and skill-system self-audit

**Files:**
- Modify: `backend/tests/test_wfh_council.py`
- Modify: `backend/tests/test_wfh_council_hooks.py`
- Modify: `skills/waterfallhunter/tests/scenarios.md`
- Modify: `scripts/validate_wfh_skills.py`

**Interfaces:**
- Consumes: current `validate_manifest()`, `route_task()`, `doctor()`, and `validate()` behavior.
- Produces: failing tests that define `skill-system-curator`, `skill_system_audit` route, role/skill cross-reference validation, capability authority states, and adapter/catalog/README consistency.

- [ ] **Step 1: Add failing Council manifest tests**

Add focused tests equivalent to:

```python
def test_v2_requires_skill_system_curator_and_route(repo_root, manifest):
    roles = {role["id"]: role for role in manifest["roles"]}
    assert roles["skill_system_curator"]["skills"] == ["skill-system-curator"]
    assert manifest["routes"]["skill_system_audit"] == [
        "chief_orchestrator",
        "skill_system_curator",
        "adversarial_prompt_tester",
        "regression_lead",
    ]


def test_capability_authority_is_explicit(manifest):
    capabilities = manifest["capabilities"]
    assert capabilities["github_connector"]["authority"] in {
        "READ_ONLY",
        "READ_WRITE_REPO",
    }
    assert capabilities["remote_desktop_commander_mcp"]["production_mutation"] is False
```

- [ ] **Step 2: Add failing skill-validator cross-reference tests**

Require the validator to reject: a catalog skill absent from `EXPECTED_SKILLS`, an adapter without a canonical skill, a Council role referencing an unknown skill, and a canonical skill missing the shared output-contract headings.

- [ ] **Step 3: Add adversarial behavioral scenarios**

Append scenarios for: conflicting specialists editing the same semantic boundary, optional tool unavailable, stale external web claim conflicting with current repo evidence, model-change smuggling, stale PR head, partial CI being mislabeled merge-ready, and MCP write-scope escalation.

- [ ] **Step 4: Run focused RED tests**

Run:

```bash
PYTHONPATH="$PWD:$PWD/backend/src" python -m pytest -q \
  backend/tests/test_wfh_council.py \
  backend/tests/test_wfh_council_hooks.py
python scripts/validate_wfh_skills.py
```

Expected: FAIL only on the newly required v2 contracts.

- [ ] **Step 5: Commit RED evidence**

```bash
git add backend/tests/test_wfh_council.py backend/tests/test_wfh_council_hooks.py \
  skills/waterfallhunter/tests/scenarios.md scripts/validate_wfh_skills.py
git commit -m "test: define Council v2 skill-system contracts"
```

### Task 2: Add canonical `skill-system-curator` and standardize all skill contracts

**Files:**
- Create: `skills/waterfallhunter/skill-system-curator/SKILL.md`
- Create: `.agents/skills/skill-system-curator/SKILL.md`
- Modify: `skills/waterfallhunter/README.md`
- Modify: `scripts/validate_wfh_skills.py`
- Modify: all existing `skills/waterfallhunter/*/SKILL.md` where the standardized contract is missing or ambiguous.

**Interfaces:**
- Consumes: shared evidence taxonomy and protected invariants from `skills/waterfallhunter/README.md`.
- Produces: fourteen canonical skills with consistent sections for Inputs, Required Evidence, Tool Preference, Output Contract, Stop/Escalation Conditions, Verification, and Handoffs.

- [ ] **Step 1: Create curator canonical skill**

The new skill must include this behavioral contract:

```markdown
## Input Contract

Receive the current repository SHA/branch, requested audit scope, canonical skill root, Council manifest path, and available capability inventory.

## Required Evidence

Inspect the current README/catalog/manifest, every affected canonical skill, matching discovery adapters, validator rules, and behavioral scenarios before proposing edits.

## Output Contract

Return a deterministic audit table with `skill`, `finding`, `classification`, `evidence`, `action`, `owner`, and `verification`; never silently rewrite domain semantics.

## Stop and Escalation Conditions

Stop when a proposed edit changes a protected domain invariant, changes production authority, or requires a capability whose authorization cannot be established. Route domain-semantic changes to the owning specialist.
```

The workflow must explicitly test for trigger overlap, contradictory instructions, stale tool assumptions, unnecessary verbosity, missing failure examples, handoff loops, and adapter/canonical drift.

- [ ] **Step 2: Create the thin discovery adapter**

Use the exact existing adapter delegation pattern, pointing to `../../../skills/waterfallhunter/skill-system-curator/SKILL.md` and containing no independent workflow.

- [ ] **Step 3: Standardize existing canonical skills**

For each of the thirteen existing skills, preserve domain-specific content while adding concise standardized subsections where absent:

```markdown
## Input Contract
## Required Evidence
## Tool Preference
## Output Contract
## Stop and Escalation Conditions
```

Do not duplicate the shared README verbatim; each skill states only domain-specific requirements plus the categorical live-order safety sentence required by the validator.

- [ ] **Step 4: Extend static validator**

Set `EXPECTED_SKILLS` to include `skill-system-curator`. Extend required headings and cross-reference checks so README index, canonical directories, adapters, Council manifest role skills, and catalog files cannot diverge silently.

- [ ] **Step 5: Run skill validation**

```bash
python scripts/validate_wfh_skills.py
```

Expected: exit 0 with no output.

- [ ] **Step 6: Commit skill-system changes**

```bash
git add skills/waterfallhunter .agents/skills scripts/validate_wfh_skills.py
git commit -m "feat: add skill-system curator and standard contracts"
```

### Task 3: Upgrade Council manifest, routes, and capability authority model

**Files:**
- Modify: `.agents/wfh-council/manifest.json`
- Modify: `.agents/wfh-council/COUNCIL.md`
- Modify: `.agents/wfh-council/TOOLS.md`
- Modify: `scripts/wfh_council.py`
- Modify: `backend/tests/test_wfh_council.py`

**Interfaces:**
- Consumes: fourteen canonical skills and existing Council v1 roles.
- Produces: `wfh_agent_council_v2`, explicit capability states, deterministic routes, role ownership, and conflict/escalation validation.

- [ ] **Step 1: Upgrade manifest contract**

Change `contract_version` to `wfh_agent_council_v2`. Add roles:

```json
{
  "id": "skill_system_curator",
  "skills": ["skill-system-curator"],
  "production_authority": false
},
{
  "id": "capability_scout",
  "skills": ["skill-system-curator"],
  "production_authority": false
},
{
  "id": "adversarial_prompt_tester",
  "skills": ["skill-system-curator", "verification-regression"],
  "production_authority": false
},
{
  "id": "research_librarian",
  "skills": ["market-data-evidence-quality", "scientific-backtest-validation"],
  "production_authority": false
}
```

Add routes `skill_system_audit`, `data_integrity`, `browser_e2e`, `dependency_upgrade`, and `release_recovery` with minimal specialist sets. Keep `release_certifier` as the sole `production_authority=true` role.

- [ ] **Step 2: Add explicit capability records**

Represent each optional capability as an object with `required`, `authority`, `production_mutation`, and `evidence_role`. Example:

```json
"github_connector": {
  "required": false,
  "authority": "READ_WRITE_REPO",
  "production_mutation": false,
  "evidence_role": "repository_state"
}
```

No credential material is stored.

- [ ] **Step 3: Extend `validate_manifest()`**

Reject unknown skills, duplicate role IDs, ambiguous production authority, malformed capability records, routes that omit required owners, and any capability that claims production mutation authority outside the release path.

- [ ] **Step 4: Extend `doctor()` output**

Return capability status using:

```text
AVAILABLE
AUTHORIZED_READ
AUTHORIZED_WRITE
UNAVAILABLE
BLOCKED
```

Local executables may be `AVAILABLE`; connected MCP/plugin authorization is supplied by the caller/environment and must not be guessed from executable presence.

- [ ] **Step 5: Update Council and Tool docs**

Document precedence: current repo facts > runtime facts for runtime claims > official external docs for external contracts > secondary research as hypothesis sources. Explicitly state that CodeRabbit/Sonar/CodeQL are reviewers/evidence sources, not completion authorities.

- [ ] **Step 6: Run Council focused tests**

```bash
PYTHONPATH="$PWD:$PWD/backend/src" python -m pytest -q backend/tests/test_wfh_council.py
```

Expected: PASS.

- [ ] **Step 7: Commit Council v2 contract**

```bash
git add .agents/wfh-council scripts/wfh_council.py backend/tests/test_wfh_council.py
git commit -m "feat: upgrade WaterfallHunter Council to v2"
```

### Task 4: Strengthen hooks and exact-artifact validation

**Files:**
- Modify: `.githooks/pre-commit`
- Modify: `.githooks/pre-push`
- Modify: `backend/tests/test_wfh_council_hooks.py`
- Modify: `scripts/install_wfh_council_hooks.sh` only if installer behavior must change.

**Interfaces:**
- Consumes: `scripts/validate_wfh_skills.py` and Council v2 validation CLI.
- Produces: opt-in validation-only hooks that detect staged/current-tree drift without deployment or Production mutation.

- [ ] **Step 1: Add RED hook tests**

Require pre-commit to validate staged skill/manifest changes and pre-push to validate the immutable pushed Git object rather than mutable worktree content. Add a test proving hooks reject Council/skill drift while allowing unrelated code when validators pass.

- [ ] **Step 2: Implement minimum hook changes**

Pre-commit runs fast static Council/skill validation against the staged index. Pre-push resolves each local SHA from stdin and validates an isolated temporary Git tree/archive. Neither hook runs deployment, SSH mutation, or Production commands.

- [ ] **Step 3: Verify hook suite**

```bash
PYTHONPATH="$PWD:$PWD/backend/src" python -m pytest -q backend/tests/test_wfh_council_hooks.py
```

Expected: PASS.

- [ ] **Step 4: Commit hooks**

```bash
git add .githooks backend/tests/test_wfh_council_hooks.py scripts/install_wfh_council_hooks.sh
git commit -m "fix: enforce Council v2 validation on exact Git objects"
```

### Task 5: Expand behavioral pressure matrix and audit every canonical skill

**Files:**
- Modify: `skills/waterfallhunter/tests/scenarios.md`
- Create: `docs/engineering/WFH-SKILL-AUDIT-v2.md`
- Modify: affected `skills/waterfallhunter/*/SKILL.md` only when a concrete audit finding warrants it.

**Interfaces:**
- Consumes: fourteen canonical skills, Council v2 routes, current tool/capability docs.
- Produces: evidence-backed audit with `KEEP`, `TIGHTEN`, `MERGE/REMOVE`, or `ADD` disposition per skill and pressure scenario.

- [ ] **Step 1: Audit all fourteen skills**

For each skill score: trigger precision, responsibility isolation, evidence freshness, tool assumptions, output determinism, stop conditions, handoff correctness, safety invariants, overlap/contradiction risk, and test coverage.

- [ ] **Step 2: Run behavioral RED/GREEN matrix**

Use fresh-context runs when the execution environment supports them. When a fresh-agent harness is unavailable, record that limitation and do not fabricate RED/GREEN evidence; keep repository static tests separate from conversational behavioral evidence.

- [ ] **Step 3: Apply only evidence-backed wording changes**

Prefer deleting duplication and ambiguity to adding prose. Do not merge domain skills merely because they share safety boilerplate; ownership boundaries remain explicit.

- [ ] **Step 4: Write audit report**

`WFH-SKILL-AUDIT-v2.md` must include one row per canonical skill and a final system-level section for Council, adapters, validators, hooks, tools/MCP assumptions, and unresolved gaps.

- [ ] **Step 5: Re-run static validation and focused Council tests**

```bash
python scripts/validate_wfh_skills.py
PYTHONPATH="$PWD:$PWD/backend/src" python -m pytest -q \
  backend/tests/test_wfh_council.py \
  backend/tests/test_wfh_council_hooks.py
```

Expected: PASS.

- [ ] **Step 6: Commit audit refinements**

```bash
git add skills/waterfallhunter docs/engineering/WFH-SKILL-AUDIT-v2.md
git commit -m "docs: complete Council v2 skill audit"
```

### Task 6: Build ChatGPT Project Sources export contract

**Files:**
- Create: `docs/chatgpt-project/00-WFH-CHATGPT-ROUTER-v2.md`
- Create: `docs/chatgpt-project/01-WFH-SKILL-CATALOG-v2.md`
- Create: `docs/chatgpt-project/02-WFH-CAPABILITY-MAP-v2.md`
- Create: `docs/chatgpt-project/03-WFH-SKILL-AUDIT-SUMMARY-v2.md`
- Create: `docs/chatgpt-project/PROJECT-INSTRUCTIONS-v2.txt`
- Create: `docs/chatgpt-project/INSTALL-FA-v2.md`
- Create: `docs/chatgpt-project/PROJECT-SOURCE-MANIFEST.json`
- Create: `scripts/export_chatgpt_project_sources.py`
- Create/Modify: focused tests in `backend/tests/test_wfh_council.py` or a new `backend/tests/test_chatgpt_project_export.py`.

**Interfaces:**
- Consumes: canonical GitHub paths, current branch/SHA, Council v2 routes, skill catalog, capability map.
- Produces: deterministic lightweight Project Sources bundle with hashes; no duplicated canonical skill bodies.

- [ ] **Step 1: Add export RED tests**

Require generated bundle to contain exactly the seven overlay files, current canonical GitHub repo/path metadata, `wfh_agent_council_v2`, all fourteen skill names/paths, and SHA-256 entries for every exported file.

- [ ] **Step 2: Implement deterministic exporter**

Security-review implementation note: `export_chatgpt_project_sources.py` writes only to the fixed repository-local `.work/chatgpt-project-sources-v2` directory, normalizes LF newlines, computes SHA-256 over UTF-8 bytes, records source Git provenance, and writes `PROJECT-SOURCE-MANIFEST.json` last. It accepts no caller-controlled destination path and refuses unexpected stale content or escaped symlink targets. Canonical `SKILL.md` bodies are not copied into the export.

- [ ] **Step 3: Author Project Sources documents**

Router v2 must require current GitHub SHA resolution before work, smallest relevant skill set, full canonical `SKILL.md` retrieval, evidence taxonomy, capability authorization checks, `verification-regression`, and release-certification ownership. `INSTALL-FA-v2.md` must explain how to add the Drive folder to ChatGPT Project Sources and copy `PROJECT-INSTRUCTIONS-v2.txt` into Project Instructions.

- [ ] **Step 4: Verify deterministic export**

```bash
python scripts/export_chatgpt_project_sources.py
find .work/chatgpt-project-sources-v2 -maxdepth 1 -type f -print
python -m pytest -q backend/tests/test_chatgpt_project_export.py
```

Expected: no diff.

- [ ] **Step 5: Run export tests**

```bash
PYTHONPATH="$PWD:$PWD/backend/src" python -m pytest -q backend/tests/test_chatgpt_project_export.py
```

Expected: PASS.

- [ ] **Step 6: Commit Project Sources export**

```bash
git add docs/chatgpt-project scripts/export_chatgpt_project_sources.py \
  backend/tests/test_chatgpt_project_export.py
git commit -m "feat: add deterministic ChatGPT Project Sources export"
```

### Task 7: Full verification, PR, and Google Drive publication

**Files:**
- No model/runtime decision files should change in this task.
- Google Drive destination: create or update a folder named `WFH ChatGPT Project Sources v2`.

**Interfaces:**
- Consumes: exact branch head after Tasks 1-6.
- Produces: verified PR and Drive overlay folder whose file hashes match the repository export manifest.

- [ ] **Step 1: Run repository-local validators**

```bash
python scripts/validate_wfh_skills.py
python scripts/wfh_council.py validate --json
python scripts/wfh_council.py doctor --json
python scripts/verify_repository_hygiene.py
git diff --check main...HEAD
```

Expected: validation exits 0; doctor may report optional capabilities `UNAVAILABLE` without failing required local-tool readiness.

- [ ] **Step 2: Run focused and full relevant regression**

```bash
PYTHONPATH="$PWD:$PWD/backend/src" python -m pytest -q \
  backend/tests/test_wfh_council.py \
  backend/tests/test_wfh_council_hooks.py \
  backend/tests/test_chatgpt_project_export.py
PYTHONPATH="$PWD:$PWD/backend/src" python -m pytest -q
```

Expected: all tests PASS; existing known warnings are reported separately.

- [ ] **Step 3: Verify exact changed artifact and protected invariants**

Inspect `git diff --stat main...HEAD`, `git diff --check`, and the final diff. Confirm no scoring, lifecycle, anti-chase, leverage, notification ordering, live-trading, or Production-deploy semantics changed.

- [ ] **Step 4: Open PR**

Create a PR against `main` with exact tested SHA, RED→GREEN evidence, skill audit dispositions, capability/authority changes, Project Sources export description, and safety statement.

- [ ] **Step 5: Wait for and inspect exact-head CI/reviews**

Require repository status checks configured on protected `main`, plus available CodeQL/Sonar/CodeRabbit evidence. Resolve actionable findings through the owning skill and rerun proportional verification on the new exact head.

- [ ] **Step 6: Publish Drive overlay**

Using the Google Drive connector, create/update `WFH ChatGPT Project Sources v2` and upload the seven generated overlay files. Do not upload canonical skill bodies. Verify uploaded names and content hashes against `PROJECT-SOURCE-MANIFEST.json`.

- [ ] **Step 7: Report completion state**

Report repository `CODE_READY` or `MERGE_READY` only when justified by exact-head evidence. Do not claim `DEPLOY_READY`/`PRODUCTION_VERIFIED` because this change does not require a Production runtime deployment. Provide the Google Drive folder reference and the exact steps for attaching it to ChatGPT Project Sources.
