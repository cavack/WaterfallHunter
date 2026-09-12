## Problem

Describe the observed defect or research question. Include affected runtime/model surfaces.

## Evidence / reproduction

Provide the exact command, dataset/runtime window, and pre-fix result. Missing evidence is `UNAVAILABLE`, not success.

## Root cause and classification

Classification: `CORRECTNESS | DATA | RUNTIME | MODEL | POLICY | UI | DOCS`

Explain the causal mechanism and why neighboring hypotheses were excluded.

## RED test

Record the focused failing test and exact pre-fix result.

## Implementation

Describe the smallest coherent change. Separate correctness fixes from calibration challengers.

## GREEN and regression results

Record targeted, neighboring, full-suite, frontend/build, security, hygiene, and documentation results as applicable.

## Runtime and signal-funnel impact

State measured or expected effects on cadence, freshness, resources, stage counts, decisions, and dominant blockers. Label unmeasured effects explicitly.

## Calibration and scientific status

State whether weights, thresholds, gates, or lifecycle semantics changed. For a challenger, include version, dataset provenance, walk-forward/holdout status, and promotion status.

## Documentation changed

List README/model/decision/dashboard/API/runbook/diagram/ledger changes required by this behavior.

## Safety

- [ ] `LIVE_TRADING_ENABLED=false` remains unchanged.
- [ ] No credentials, runtime databases, evidence packets, logs, backups, or generated datasets are committed.
- [ ] No strategy threshold or execution gate is promoted without documented validation.
- [ ] Only canonical `ENTRY_READY` remains proactively actionable; no parallel score/ranking is presented as an entry signal.

## Validation

- [ ] Backend tests pass.
- [ ] Frontend typecheck and production build pass.
- [ ] Dependency audits pass.
- [ ] Docker Compose validation/build passes when container files changed.
- [ ] Documentation/configuration are updated when behavior changes.
- [ ] `docs/PROJECT_HANDOFF.md` is updated when architecture, operations, deployment, data ownership, or decision semantics change.

## Operational impact

Document migrations, environment changes, deployment considerations, or state compatibility concerns. Use `None` when there is no operational impact.

## Rollback notes

Describe the exact rollback boundary, data/schema compatibility, and any evidence that must be preserved.

## Exact tested commit

`<full 40-character SHA>`
