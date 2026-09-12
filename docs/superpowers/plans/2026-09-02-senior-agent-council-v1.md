# WaterfallHunter Senior Agent Council v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add and execute a deterministic senior-agent Council pack that routes WaterfallHunter engineering/model-research work through canonical skills, validates safety/ownership, snapshots current evidence and produces an auditable model-improvement campaign without weakening production policy.

**Architecture:** Existing `skills/waterfallhunter/*` and `.agents/skills/*` remain canonical specialist bodies. A thin machine-readable Council manifest plus a stdlib Python CLI provides routing, capability detection, evidence snapshots and validation; documentation provides operating contracts; opt-in git hooks call deterministic validation only.

**Tech Stack:** Python 3.13-compatible stdlib, JSON, Git, Bash, pytest, existing WaterfallHunter CI/tooling, GitHub connector, Remote Desktop Commander, Docker/Prometheus runtime evidence.

**Spec:** `docs/superpowers/specs/2026-09-02-senior-agent-council-v1.md`

## Global Constraints

- Base SHA: `a3b45dc13158878fa3f64fddb0a12a7631b85a3c`.
- Preserve `ENTRY_READY=78`, `FORMING=55`, Anti-Chase=`1.2 ATR` unless a later separately validated strategy change is promoted.
- Preserve ScoreV2/lifecycle/eligibility/provenance/persistence-before-notification/scientific validation semantics.
- `LIVE_TRADING_ENABLED=false`; no real orders and no order-placement capability.
- Missing/stale data stays unavailable; no directional imputation.
- Every production-code behavior starts with a failing test.
- Council validation is non-deploying; only `release-production-certification` can issue production readiness states.

---
### Task 1: Council manifest contract and validator

**Files:**
- Create: `.agents/wfh-council/manifest.json`
- Create: `backend/tests/test_wfh_council.py`
- Create: `scripts/wfh_council.py`

**Interfaces:**
- Produces: `load_manifest(path) -> dict`, `validate_manifest(repo_root, manifest) -> list[str]`.
- Consumes: canonical skill paths under `skills/waterfallhunter/` and `.agents/skills/`.

- [ ] **Step 1: Write RED tests** for unique role IDs, canonical skill existence, the required model-optimization route, sole production authority, explicit no-live-order invariant and rejection of an invented/missing skill.

```python
manifest = council.load_manifest(REPO / ".agents/wfh-council/manifest.json")
assert council.validate_manifest(REPO, manifest) == []
assert manifest["routes"]["model_optimization"][0] == "chief_orchestrator"
assert manifest["production_authority_role"] == "release_certifier"
assert manifest["protected_invariants"]["live_order_placement"] == "FORBIDDEN"
```

- [ ] **Step 2: Run `PYTHONPATH="$PWD:$PWD/backend/src" pytest -q backend/tests/test_wfh_council.py` and confirm RED because the manifest/CLI do not exist.**
- [ ] **Step 3: Create the manifest** with all thirteen canonical skill owners plus FP/FN adversarial reviewer personas and routes for `deep_audit`, `model_optimization`, `runtime_incident`, `frontend_contract`, `security_review`, and `production_release`.
- [ ] **Step 4: Implement only stdlib manifest loading/validation**; no network calls or runtime mutation.
- [ ] **Step 5: Re-run the focused test and confirm GREEN.**
- [ ] **Step 6: Run `python scripts/validate_wfh_skills.py` and `git diff --check`.**
### Task 2: Deterministic routing and capability doctor

**Files:**
- Modify: `scripts/wfh_council.py`
- Modify: `backend/tests/test_wfh_council.py`

**Interfaces:**
- Produces: `route_task(manifest, task_type) -> list[dict]`, `doctor(repo_root) -> dict` and CLI subcommands `route`, `doctor`, `validate` with optional `--json`.
- Consumes: manifest from Task 1 plus local executable discovery via `shutil.which`.

- [ ] **Step 1: Write RED tests** proving model work routes through Market Data → Strategy → Quant → adversarial review → Regression → Release, production release includes the sole release certifier, unknown routes fail, and optional missing tools are reported `UNAVAILABLE` rather than fatal.

```python
roles = [x["role"] for x in council.route_task(manifest, "model_optimization")]
assert roles.index("strategy_owner") < roles.index("quant_validation_lead")
assert roles[-1] == "release_certifier"
assert council.doctor(REPO)["repo"]["git_sha"]
```

- [ ] **Step 2: Run the focused tests and confirm RED.**
- [ ] **Step 3: Implement deterministic route/doctor commands** with no shell interpolation and no credential inspection.
- [ ] **Step 4: Add `--json` output and stable exit codes: `0=valid`, `2=contract error`, `3=required capability unavailable`.**
- [ ] **Step 5: Re-run focused tests and confirm GREEN.**
### Task 3: Research-evidence snapshot and blocker classification

**Files:**
- Modify: `scripts/wfh_council.py`
- Modify: `backend/tests/test_wfh_council.py`

**Interfaces:**
- Produces: `summarize_research_evidence(research_dir) -> dict` and CLI `research-snapshot --research-dir PATH --json`.
- Consumes: existing contracts such as `DATASET_AUDIT.json`, `OOS_VALIDATION.json`, `BEST_DEVELOPMENT_CONFIG.json`, `PRODUCTION_VS_CHALLENGERS.json`, `GATE_REJECTION_FUNNEL.json`, `OUTCOME_INTEGRITY.json` when present.

- [ ] **Step 1: Build a temporary fixture** with development-only span, blocked OOS and no champion; write RED tests that the summary emits `NO_PROMOTION_EVIDENCE`, preserves hashes/metrics, and never converts sparse performance into a champion.

```python
summary = council.summarize_research_evidence(tmp_path)
assert summary["promotion_disposition"] == "NO_PROMOTION_EVIDENCE"
assert "insufficient_oos_evidence" in summary["blockers"]
assert summary["dataset"]["span_days"] == 15.9
```

- [ ] **Step 2: Run focused tests and confirm RED.**
- [ ] **Step 3: Implement defensive JSON loading** with explicit `MISSING_ARTIFACT`/`INVALID_ARTIFACT` statuses and no domain inference when fields are absent.
- [ ] **Step 4: Summarize gate/funnel/outcome limitations without altering or re-ranking the stored trials.**
- [ ] **Step 5: Re-run focused tests and confirm GREEN.**
### Task 4: Council operating docs and research registry

**Files:**
- Create: `.agents/wfh-council/COUNCIL.md`
- Create: `.agents/wfh-council/TOOLS.md`
- Create: `.agents/wfh-council/RESEARCH.md`
- Modify: `backend/tests/test_wfh_council.py`

**Interfaces:**
- Produces: stable human/agent runbook references; no executable model logic.
- Consumes: manifest role IDs and canonical skill names from Task 1.

- [ ] **Step 1: Add RED documentation-contract tests** requiring every manifest role to appear in `COUNCIL.md`, each required/optional tool class to appear in `TOOLS.md`, and research hypotheses to contain `mechanism`, `point_in_time_requirement`, `falsifier`, and `promotion_gate` labels.
- [ ] **Step 2: Run focused tests and confirm RED.**
- [ ] **Step 3: Write `COUNCIL.md`** with intake → evidence taxonomy → owner assignment → adversarial review → regression → release flow and explicit terminal outcomes.
- [ ] **Step 4: Write `TOOLS.md`** covering GitHub, Remote Desktop Commander, web research, CodeRabbit, Mermaid, Docker, Prometheus/Grafana/Alertmanager, pytest, Playwright, CodeQL/Sonar and optional market-data connectors without credentials.
- [ ] **Step 5: Write `RESEARCH.md`** with preregistered challenger families: order flow, basis/mark divergence, leverage stress, liquidity/cascade pressure, relative weakness/regime interactions and optional attention features.
- [ ] **Step 6: Re-run focused tests and confirm GREEN.**
### Task 5: Opt-in local git hooks

**Files:**
- Create: `.githooks/pre-commit`
- Create: `.githooks/pre-push`
- Create: `scripts/install_wfh_council_hooks.sh`
- Create: `backend/tests/test_wfh_council_hooks.py`

**Interfaces:**
- Pre-commit consumes `scripts/wfh_council.py validate` and `scripts/validate_wfh_skills.py`.
- Pre-push additionally runs the Council focused tests and repository hygiene check; neither hook deploys or edits tracked files.

- [ ] **Step 1: Write RED subprocess tests** in a temporary git repository proving the installer sets `core.hooksPath=.githooks`, repeated install is idempotent, hooks are executable, and source text contains no deploy/live-trading mutation command.

```python
subprocess.run(["bash", str(INSTALLER)], cwd=temp_repo, check=True)
assert git(temp_repo, "config", "--get", "core.hooksPath") == ".githooks"
```

- [ ] **Step 2: Run `pytest -q backend/tests/test_wfh_council_hooks.py` and confirm RED.**
- [ ] **Step 3: Implement POSIX shell hooks** with `set -eu`, repository-root resolution and bounded validation commands only.
- [ ] **Step 4: Implement the idempotent installer** using repository-local git config; never global config.
- [ ] **Step 5: Re-run hook tests and confirm GREEN.**
### Task 6: Safe host/repository snapshot command

**Files:**
- Modify: `scripts/wfh_council.py`
- Modify: `backend/tests/test_wfh_council.py`

**Interfaces:**
- Produces: CLI `snapshot --repo-root PATH [--research-dir PATH] [--production-revision REV] --json`.
- Consumes: local git identity, optional research summary from Task 3 and caller-supplied production revision/runtime facts. It does not inspect secret values or invoke Docker itself.

- [ ] **Step 1: Write RED tests** proving snapshot differentiates repository HEAD from production revision, records evidence classifications, carries the research disposition and emits `LIVE_TRADING_ENABLED=false` as a required policy assertion rather than reading credentials.
- [ ] **Step 2: Run focused tests and confirm RED.**
- [ ] **Step 3: Implement a stable JSON snapshot schema** with `generated_at`, `repo`, `runtime`, `research`, `protected_invariants`, `readiness` and `unknowns`.
- [ ] **Step 4: Require the caller to supply mutable runtime observations** such as production revision; mark omitted runtime facts `UNAVAILABLE` instead of guessing.
- [ ] **Step 5: Re-run focused tests and confirm GREEN.**
### Task 7: Execute Council v1 against current host evidence

**Files:**
- Runtime artifact only: `/srv/waterfallhunter/research/agent_council/20260902/COUNCIL_BASELINE.json`
- Runtime artifact only: `/srv/waterfallhunter/research/agent_council/20260902/MODEL_PROBLEM_LEDGER.json`
- Runtime artifact only: `/srv/waterfallhunter/research/agent_council/20260902/CHALLENGER_PREREGISTRATION.json`

**Interfaces:**
- Consumes: current repo SHA, current production revision/health/RSS/freshness, 2026-08-31 rapid-delivery artifacts and current main contracts.
- Produces: evidence-only baseline, problem/solution ledger and preregistered next experiments. These artifacts are not production configuration.

- [ ] **Step 1: Run `python scripts/wfh_council.py validate --json` and `doctor --json` from the exact worktree head.**
- [ ] **Step 2: Run `research-snapshot` on `/srv/waterfallhunter/research/rapid_delivery/20260831` and preserve the JSON result.**
- [ ] **Step 3: Collect current runtime facts read-only:** production revision, `/livez`, `/readyz`, `/healthz`, relevant Prometheus memory/freshness metrics, restart count and protected live-trading state.
- [ ] **Step 4: Generate `COUNCIL_BASELINE.json`** with exact hashes/revisions and explicit `VERIFIED_FACT`/`INFERENCE` classifications.
- [ ] **Step 5: Generate `MODEL_PROBLEM_LEDGER.json`** separating data sufficiency, model geometry, runtime freshness, evidence gaps, false-positive/false-negative hypotheses and already-resolved historical defects.
- [ ] **Step 6: Generate `CHALLENGER_PREREGISTRATION.json`** with frozen development hypotheses, requested causal fields, splits, embargo, cost metrics, abort/falsification criteria and an untouched-final-evaluation rule.
- [ ] **Step 7: Do not change `entry_policy_v1` during this task.**
### Task 8: Verification, independent review and GitHub integration

**Files:** exact final diff only.

- [ ] Run `PYTHONPATH="$PWD:$PWD/backend/src" pytest -q backend/tests/test_wfh_council.py backend/tests/test_wfh_council_hooks.py`.
- [ ] Run `python scripts/wfh_council.py validate --json` and `python scripts/validate_wfh_skills.py`.
- [ ] Run `python scripts/verify_repository_hygiene.py` and `git diff --check`.
- [ ] Run the repository-level backend suite if the Council changes touch shared test/import behavior; otherwise record why the focused scope is sufficient and still inspect CI on the exact pushed head.
- [ ] Re-read the exact diff and prove no ScoreV2/EntryDecision/lifecycle/Anti-Chase/leverage/notification/live-trading production semantics changed.
- [ ] Check `coderabbit --version` and authenticated agent status; if available, run CodeRabbit on the committed Council diff and address only evidence-backed issues through new RED→GREEN cycles.
- [ ] Commit the implementation in small reviewable commits, push `feat/senior-agent-council-v1-20260902`, open a dedicated PR and inspect exact-head CI/reviews.
- [ ] Merge only after required checks/reviews pass. Since Council v1 is tooling/docs/tests and no runtime model policy changes, do not redeploy Production merely to install the pack.
- [ ] Install the opt-in hooks in the canonical host repository after merge and re-run Council validation from the canonical merged SHA.

## Post-pack scientific continuation

After Council v1 is installed, execute `model_optimization` as a separate evidence campaign. The first gate is to establish a promotion-grade point-in-time dataset with sufficient span/outcomes and realistic net-R costs. If that gate is not met, terminate honestly as `NO_PROMOTION_EVIDENCE`; do not open final holdout and do not lower `78/55/1.2 ATR` to manufacture signals.

If the data gate becomes valid, preregister a bounded challenger set, run development/walk-forward/embargo and adversarial FP/FN analyses, select stable Pareto candidates without final holdout, then open one untouched final evaluation. Only a stable OOS winner proceeds to shadow and normal release certification.