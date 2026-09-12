# STRICT scientific validation and calibration

This Phase 6 pipeline decides whether a provenance-complete STRICT cohort is
scientifically complete enough to be presented to the owner for review. It
never promotes a feature, displays a probability, or enables an order path.

## Input contract

`scripts/validate_strict_scientific_model.py` consumes a JSON object matching
`strict_scientific_validation_request_v1`:

- the upstream strict dataset manifest SHA-256 and exact source revision;
- the fixed 24-hour TP2 target horizon;
- outcome-complete rows with immutable source-row hashes;
- `signal_class=STRICT`, `strategy_profile=strict_score_v2`, and
  `score_version=score_v2` on every row;
- feature observation time at or before signal time and label observation time
  exactly one declared horizon after signal time;
- explicit regime labels;
- canonical-symbol identity for concentration auditing;
- complete, realized execution-cost net utility.

Experimental rows, mixed horizons, future features, modeled/partial costs,
duplicate IDs, non-finite numbers, or labels unavailable at report generation
are rejected rather than imputed.

## Versioned policy

`strict_scientific_validation_policy_v1` is hash-bound. It requires:

- at least six weeks and 100 STRICT rows;
- chronological 60% development, 15% independent calibration, and 25%
  untouched holdout partitions;
- a one-day purge/embargo at boundaries;
- calibration labels must be observable before the holdout embargo boundary;
- three expanding walk-forward development folds;
- at least 20 calibration rows and 30 holdout rows;
- both target classes, at least two holdout regimes, and at least 10 holdout
  rows per represented regime;
- 1,000 deterministic moving-block bootstrap resamples.

Changing any value changes the policy hash and must be treated as a versioned
scientific decision, never a silent threshold edit.

## Candidate and selection contract

The auditable registry contains a constant-prevalence baseline and a
regularized one-feature logistic challenger over the existing evidence score.
Candidate selection uses only aggregated chronological development OOS metrics:
Brier score, then log loss, then lower complexity. Holdout data cannot select,
tune, or reorder candidates.

The selected candidate is refit on eligible development labels. A monotonic
isotonic calibrator is fit only on the independent calibration partition. The
final holdout is opened once for Brier, log loss, ECE, PR-AUC, precision at the
top decile, complete-cost net utility, drawdown/tail loss, multi-regime and
symbol-concentration breakdowns, and moving-block bootstrap confidence intervals.
Owner review is blocked if holdout Brier does not beat the development
prevalence baseline, holdout net utility is non-positive, or its 95% block
bootstrap interval crosses zero.

## Outputs and authority

Insufficient evidence produces a hash-bound report and model card with
`DO_NOT_PROMOTE`. Complete evidence produces `OWNER_REVIEW_REQUIRED`, not an
approval. Both always retain:

```text
execution_mode=SIGNAL_ONLY
promotion_allowed=false
probability_display_allowed=false
live_execution_allowed=false
```

Only a separate explicit `FEATURE_PROMOTION_APPROVAL` may authorize a later
simulated-product change. Live trading remains outside the system contract.

Example invocation:

```bash
PYTHONPATH=backend/src:. python scripts/validate_strict_scientific_model.py \
  --input /path/to/strict-scientific-input.json \
  --output /path/to/strict-scientific-report.json
```

The output is written through a same-directory `.partial` file, `fsync`, and
atomic replace. An insufficient report is still persisted, then exits with code
2 so automation cannot mistake `DO_NOT_PROMOTE` for a passing certification.
