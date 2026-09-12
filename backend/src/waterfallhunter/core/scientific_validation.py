"""Fail-closed scientific validation for STRICT signal-only evidence."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from waterfallhunter.core.signal_metadata import canonical_sha256


SHA256_PATTERN = r"^[0-9a-f]{64}$"
DAY_SECONDS = 86_400
MAX_VALIDATION_ROWS = 100_000


class ScientificValidationRow(BaseModel):
    """One outcome-complete STRICT observation with point-in-time provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: str = Field(min_length=1)
    signal_class: Literal["STRICT"]
    strategy_profile: Literal["strict_score_v2"]
    score_version: Literal["score_v2"]
    signal_triggered_at: int = Field(ge=0, strict=True)
    feature_observed_at: int = Field(ge=0, strict=True)
    label_observed_at: int = Field(ge=0, strict=True)
    predictive_evidence_score: float = Field(ge=0, le=100, allow_inf_nan=False)
    target_tp2_hit_within_horizon: bool
    canonical_symbol: str = Field(min_length=1)
    regime: str = Field(min_length=1)
    net_utility_r: float = Field(ge=-1_000, le=1_000, allow_inf_nan=False)
    execution_costs_complete: Literal[True]
    execution_cost_basis: Literal["REALIZED"]
    source_row_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _time_order(self) -> "ScientificValidationRow":
        if self.feature_observed_at > self.signal_triggered_at:
            raise ValueError("feature evidence cannot be observed after the signal")
        if self.label_observed_at <= self.signal_triggered_at:
            raise ValueError("label observation must follow the signal")
        material = self.model_dump(mode="json", exclude={"source_row_sha256"})
        if self.source_row_sha256 != canonical_sha256(material):
            raise ValueError("scientific source row hash mismatch")
        return self


def scientific_source_manifest_sha256(
    *,
    source_revision: str,
    generated_at: int,
    target_horizon_seconds: int,
    rows: tuple[ScientificValidationRow, ...],
) -> str:
    ordered = sorted(rows, key=lambda row: (row.signal_triggered_at, row.signal_id))
    return canonical_sha256(
        {
            "contract_version": "strict_scientific_source_manifest_v1",
            "source_revision": source_revision,
            "generated_at": generated_at,
            "target_horizon_seconds": target_horizon_seconds,
            "source_row_sha256": [row.source_row_sha256 for row in ordered],
        }
    )


class ScientificValidationPolicy(BaseModel):
    """Versioned sufficiency rules; changes require a new policy hash/version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["strict_scientific_validation_policy_v1"] = (
        "strict_scientific_validation_policy_v1"
    )
    minimum_observation_weeks: int = Field(default=6, ge=1)
    minimum_total_rows: int = Field(default=100, ge=1)
    minimum_calibration_rows: int = Field(default=20, ge=1)
    minimum_holdout_rows: int = Field(default=30, ge=1)
    minimum_walk_forward_test_rows: int = Field(default=10, ge=1)
    minimum_holdout_regimes: int = Field(default=2, ge=2)
    minimum_holdout_rows_per_regime: int = Field(default=10, ge=1)
    minimum_selected_holdout_rows: int = Field(default=10, ge=1)
    maximum_selected_symbol_share: float = Field(default=0.60, gt=0, le=1)
    walk_forward_folds: int = Field(default=3, ge=2)
    development_fraction: float = Field(default=0.60, gt=0, lt=1)
    calibration_fraction: float = Field(default=0.15, gt=0, lt=1)
    holdout_fraction: float = Field(default=0.25, gt=0, lt=1)
    embargo_seconds: int = Field(default=DAY_SECONDS, ge=DAY_SECONDS)
    bootstrap_iterations: int = Field(default=1_000, ge=100)
    bootstrap_block_size: int = Field(default=5, ge=2)
    policy_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _valid_policy(self, info: ValidationInfo) -> "ScientificValidationPolicy":
        fractions = (
            self.development_fraction
            + self.calibration_fraction
            + self.holdout_fraction
        )
        if not math.isclose(fractions, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("scientific split fractions must sum to one")
        if self.walk_forward_folds < 2:
            raise ValueError("at least two walk-forward folds are required")
        if self.bootstrap_iterations < 100:
            raise ValueError("at least 100 bootstrap iterations are required")
        if self.bootstrap_block_size < 2:
            raise ValueError("bootstrap blocks must preserve temporal dependence")
        material = self.model_dump(mode="json", exclude={"policy_hash"})
        if (
            not (info.context or {}).get("skip_hash")
            and self.policy_hash != canonical_sha256(material)
        ):
            raise ValueError("scientific validation policy hash mismatch")
        return self

    @classmethod
    def v1(cls) -> "ScientificValidationPolicy":
        provisional = cls.model_validate(
            {"policy_hash": "0" * 64},
            context={"skip_hash": True},
        )
        material = provisional.model_dump(mode="json", exclude={"policy_hash"})
        return cls.model_validate(
            {**material, "policy_hash": canonical_sha256(material)}
        )

    def require_integrity(self) -> None:
        material = self.model_dump(mode="json", exclude={"policy_hash"})
        if self.policy_hash != canonical_sha256(material):
            raise ValueError("SCIENTIFIC_VALIDATION_POLICY_TAMPERED")


class ScientificValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["strict_scientific_validation_request_v1"] = (
        "strict_scientific_validation_request_v1"
    )
    source_dataset_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    source_revision: str = Field(min_length=40, max_length=64)
    generated_at: int = Field(ge=0, strict=True)
    target_horizon_seconds: Literal[86_400]
    rows: tuple[ScientificValidationRow, ...] = Field(max_length=MAX_VALIDATION_ROWS)

    @model_validator(mode="after")
    def _dataset_integrity(self) -> "ScientificValidationRequest":
        if len(self.source_revision) not in {40, 64} or any(
            character not in "0123456789abcdef" for character in self.source_revision
        ):
            raise ValueError("source_revision must be a 40- or 64-character lowercase hash")
        identifiers = [row.signal_id for row in self.rows]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("scientific validation signal IDs must be unique")
        source_hashes = [row.source_row_sha256 for row in self.rows]
        if len(set(source_hashes)) != len(source_hashes):
            raise ValueError("scientific validation source row hashes must be unique")
        if any(row.label_observed_at > self.generated_at for row in self.rows):
            raise ValueError("dataset cannot contain labels observed after generated_at")
        horizons = {
            row.label_observed_at - row.signal_triggered_at for row in self.rows
        }
        if horizons and horizons != {self.target_horizon_seconds}:
            raise ValueError("every row must use the declared target horizon")
        expected_manifest = scientific_source_manifest_sha256(
            source_revision=self.source_revision,
            generated_at=self.generated_at,
            target_horizon_seconds=self.target_horizon_seconds,
            rows=self.rows,
        )
        if self.source_dataset_manifest_sha256 != expected_manifest:
            raise ValueError("scientific source dataset manifest hash mismatch")
        return self


_CANDIDATES = (
    {
        "model_id": "prevalence_baseline_v1",
        "family": "CONSTANT_PREVALENCE",
        "complexity_rank": 0,
    },
    {
        "model_id": "regularized_logistic_score_v1",
        "family": "REGULARIZED_LOGISTIC",
        "complexity_rank": 1,
        "l2_penalty": 0.10,
        "iterations": 1_000,
        "learning_rate": 0.05,
    },
)


def validate_strict_scientific_evidence(
    request: ScientificValidationRequest | dict[str, Any],
    *,
    policy: ScientificValidationPolicy | None = None,
) -> dict[str, Any]:
    packet = (
        request
        if isinstance(request, ScientificValidationRequest)
        else ScientificValidationRequest.model_validate(request)
    )
    active_policy = policy or ScientificValidationPolicy.v1()
    active_policy.require_integrity()
    rows = sorted(packet.rows, key=lambda row: (row.signal_triggered_at, row.signal_id))
    manifest = _dataset_manifest(packet, rows)
    split = _split_rows(rows, active_policy)
    reasons = _sufficiency_reasons(rows, split, active_policy)
    walk_forward = _walk_forward(split["development"], active_policy)
    reasons.extend(walk_forward["blocking_reasons"])

    result: dict[str, Any] = {
        "contract_version": "strict_scientific_validation_report_v1",
        "execution_mode": "SIGNAL_ONLY",
        "cohort": "STRICT",
        "source_dataset_manifest_sha256": packet.source_dataset_manifest_sha256,
        "derived_dataset_manifest": manifest,
        "validation_policy": active_policy.model_dump(mode="json"),
        "candidate_registry": [dict(candidate) for candidate in _CANDIDATES],
        "split_summary": _split_summary(split, active_policy),
        "walk_forward": walk_forward,
        "promotion_allowed": False,
        "feature_promotion_approval_required": True,
    }
    if reasons:
        result.update(
            {
                "evidence_gate_status": "INSUFFICIENT",
                "promotion_decision": "DO_NOT_PROMOTE",
                "blocking_reasons": sorted(set(reasons)),
                "champion_challenger": {
                    "available": False,
                    "selection_source": "WALK_FORWARD_DEVELOPMENT_OOS_ONLY",
                    "holdout_used_for_selection": False,
                },
                "independent_calibration": {"available": False},
                "untouched_holdout": {
                    "available": False,
                    "used_for_selection": False,
                },
                "model_card": _model_card_unavailable(packet, manifest, reasons),
            }
        )
        return _hash_report(result)

    champion_id = str(walk_forward["ranking"][0]["model_id"])
    calibration_start = int(split["boundaries"]["calibration_start"])
    train = [
        row
        for row in split["development"]
        if row.label_observed_at <= calibration_start - active_policy.embargo_seconds
    ]
    champion_spec = next(
        candidate for candidate in _CANDIDATES if candidate["model_id"] == champion_id
    )
    fitted = _fit_model(champion_spec, train)
    calibration_rows = _eligible_calibration_rows(split)
    raw_calibration = [_predict(fitted, row) for row in calibration_rows]
    calibrator = _fit_isotonic(raw_calibration, _labels(calibration_rows))
    calibrated_calibration = [_apply_isotonic(calibrator, value) for value in raw_calibration]
    calibration_metrics = _probability_metrics(
        _labels(calibration_rows),
        calibrated_calibration,
    )
    holdout_rows = split["holdout"]
    raw_holdout = [_predict(fitted, row) for row in holdout_rows]
    calibrated_holdout = [_apply_isotonic(calibrator, value) for value in raw_holdout]
    holdout_metrics = _probability_metrics(_labels(holdout_rows), calibrated_holdout)
    utility = _net_utility(holdout_rows, calibrated_holdout)
    bootstrap = _block_bootstrap(
        holdout_rows,
        calibrated_holdout,
        iterations=active_policy.bootstrap_iterations,
        block_size=active_policy.bootstrap_block_size,
        seed_material=f"{manifest['dataset_manifest_sha256']}:{champion_id}",
    )
    regime_audit = _regime_audit(holdout_rows, calibrated_holdout)
    concentration_audit = _concentration_audit(holdout_rows, calibrated_holdout)
    baseline_model = _fit_model(_CANDIDATES[0], train)
    baseline_holdout = [_predict(baseline_model, row) for row in holdout_rows]
    baseline_metrics = _probability_metrics(_labels(holdout_rows), baseline_holdout)
    performance_reasons = _performance_gate_reasons(
        holdout_metrics=holdout_metrics,
        baseline_metrics=baseline_metrics,
        utility=utility,
        bootstrap=bootstrap,
        regime_audit=regime_audit,
        concentration_audit=concentration_audit,
        policy=active_policy,
    )
    evidence_gate_status = (
        "COMPLETE_FOR_OWNER_REVIEW"
        if not performance_reasons
        else "PERFORMANCE_GATE_FAILED"
    )
    promotion_decision = (
        "OWNER_REVIEW_REQUIRED" if not performance_reasons else "DO_NOT_PROMOTE"
    )
    result.update(
        {
            "evidence_gate_status": evidence_gate_status,
            "promotion_decision": promotion_decision,
            "blocking_reasons": performance_reasons,
            "champion_challenger": {
                "available": True,
                "selection_source": "WALK_FORWARD_DEVELOPMENT_OOS_ONLY",
                "holdout_used_for_selection": False,
                "champion_model_id": champion_id,
                "ranking": walk_forward["ranking"],
            },
            "independent_calibration": {
                "available": True,
                "fit_source": "CALIBRATION_SPLIT_ONLY",
                "row_count": len(calibration_rows),
                "maximum_label_observed_at": max(
                    row.label_observed_at for row in calibration_rows
                ),
                "calibrator": calibrator,
                "metrics": calibration_metrics,
            },
            "untouched_holdout": {
                "available": True,
                "used_for_selection": False,
                "row_count": len(holdout_rows),
                "probability_metrics": holdout_metrics,
                "development_prevalence_baseline_metrics": baseline_metrics,
                "net_utility": utility,
                "block_bootstrap_confidence_intervals": bootstrap,
                "multi_regime_audit": regime_audit,
                "symbol_concentration_audit": concentration_audit,
            },
            "model_card": _model_card(
                packet,
                manifest,
                champion_id=champion_id,
                fitted=fitted,
                calibrator=calibrator,
                holdout_metrics=holdout_metrics,
                utility=utility,
                bootstrap=bootstrap,
                regime_audit=regime_audit,
                concentration_audit=concentration_audit,
                performance_reasons=performance_reasons,
            ),
        }
    )
    return _hash_report(result)


def _dataset_manifest(
    packet: ScientificValidationRequest,
    rows: list[ScientificValidationRow],
) -> dict[str, Any]:
    body = {
        "contract_version": "strict_scientific_dataset_manifest_v1",
        "source_dataset_manifest_sha256": packet.source_dataset_manifest_sha256,
        "source_revision": packet.source_revision,
        "cohort": "STRICT",
        "target": "tp2_hit_within_horizon",
        "target_horizon_seconds": packet.target_horizon_seconds,
        "generated_at": packet.generated_at,
        "row_count": len(rows),
        "signal_window": {
            "start": rows[0].signal_triggered_at if rows else None,
            "end": rows[-1].signal_triggered_at + 1 if rows else None,
            "boundary": "[start,end)",
        },
        "regime_counts": dict(sorted(Counter(row.regime for row in rows).items())),
        "symbol_counts": dict(
            sorted(Counter(row.canonical_symbol for row in rows).items())
        ),
        "signal_ids": [row.signal_id for row in rows],
        "source_row_hashes": [row.source_row_sha256 for row in rows],
        "rows_sha256": canonical_sha256(
            [row.model_dump(mode="json") for row in rows]
        ),
    }
    return {**body, "dataset_manifest_sha256": canonical_sha256(body)}


def _split_rows(
    rows: list[ScientificValidationRow],
    policy: ScientificValidationPolicy,
) -> dict[str, Any]:
    if not rows:
        return {
            "development": [],
            "calibration": [],
            "holdout": [],
            "boundaries": {},
            "total_rows": 0,
        }
    start = rows[0].signal_triggered_at
    end = rows[-1].signal_triggered_at + 1
    span = end - start
    development_end = start + int(span * policy.development_fraction)
    calibration_end = development_end + int(span * policy.calibration_fraction)
    calibration_start = development_end + policy.embargo_seconds
    holdout_start = calibration_end + policy.embargo_seconds
    return {
        "development": [row for row in rows if row.signal_triggered_at < development_end],
        "calibration": [
            row
            for row in rows
            if calibration_start <= row.signal_triggered_at < calibration_end
        ],
        "holdout": [row for row in rows if row.signal_triggered_at >= holdout_start],
        "boundaries": {
            "dataset_start": start,
            "dataset_end": end,
            "development_end": development_end,
            "calibration_start": calibration_start,
            "calibration_end": calibration_end,
            "holdout_start": holdout_start,
            "boundary_semantics": "[start,end)",
        },
        "total_rows": len(rows),
    }


def _sufficiency_reasons(
    rows: list[ScientificValidationRow],
    split: dict[str, Any],
    policy: ScientificValidationPolicy,
) -> list[str]:
    reasons: list[str] = []
    if len(rows) < policy.minimum_total_rows:
        reasons.append("STRICT_TOTAL_ROW_COUNT_BELOW_POLICY_MINIMUM")
    span = rows[-1].signal_triggered_at - rows[0].signal_triggered_at if rows else 0
    if span < policy.minimum_observation_weeks * 7 * DAY_SECONDS:
        reasons.append("STRICT_OBSERVATION_SPAN_BELOW_SIX_WEEKS")
    calibration = _eligible_calibration_rows(split)
    holdout = split["holdout"]
    if len(calibration) < policy.minimum_calibration_rows:
        reasons.append("INDEPENDENT_CALIBRATION_SPLIT_TOO_SMALL")
    if len(holdout) < policy.minimum_holdout_rows:
        reasons.append("UNTOUCHED_HOLDOUT_SPLIT_TOO_SMALL")
    if len({row.regime for row in holdout}) < policy.minimum_holdout_regimes:
        reasons.append("UNTOUCHED_HOLDOUT_LACKS_MULTI_REGIME_COVERAGE")
    regime_counts = Counter(row.regime for row in holdout)
    if regime_counts and min(regime_counts.values()) < policy.minimum_holdout_rows_per_regime:
        reasons.append("UNTOUCHED_HOLDOUT_REGIME_SAMPLE_TOO_SMALL")
    if calibration and len(set(_labels(calibration))) < 2:
        reasons.append("CALIBRATION_SPLIT_LACKS_BOTH_TARGET_CLASSES")
    if holdout and len(set(_labels(holdout))) < 2:
        reasons.append("HOLDOUT_SPLIT_LACKS_BOTH_TARGET_CLASSES")
    return reasons


def _eligible_calibration_rows(
    split: dict[str, Any],
) -> list[ScientificValidationRow]:
    calibration_end = split["boundaries"].get("calibration_end")
    if calibration_end is None:
        return []
    return [
        row
        for row in split["calibration"]
        if row.label_observed_at <= int(calibration_end)
    ]


def _walk_forward(
    development: list[ScientificValidationRow],
    policy: ScientificValidationPolicy,
) -> dict[str, Any]:
    if not development:
        return {
            "available": False,
            "folds": [],
            "ranking": [],
            "blocking_reasons": ["WALK_FORWARD_DEVELOPMENT_SPLIT_EMPTY"],
        }
    start = development[0].signal_triggered_at
    end = development[-1].signal_triggered_at + 1
    first_test = start + (end - start) // 2
    width = max(1, (end - first_test) // policy.walk_forward_folds)
    folds: list[dict[str, Any]] = []
    predictions: dict[str, list[tuple[int, bool, float, float]]] = {
        str(candidate["model_id"]): [] for candidate in _CANDIDATES
    }
    blockers: list[str] = []
    for index in range(policy.walk_forward_folds):
        test_start = first_test + index * width
        test_end = end if index == policy.walk_forward_folds - 1 else test_start + width
        fold, fold_blockers = _walk_forward_fold(
            development,
            policy=policy,
            index=index,
            test_start=test_start,
            test_end=test_end,
            predictions=predictions,
        )
        blockers.extend(fold_blockers)
        folds.append(fold)
    ranking = [] if blockers else _rank_walk_forward(predictions)
    return {
        "available": not blockers,
        "selection_contract": "CHRONOLOGICAL_EXPANDING_PURGED_EMBARGOED",
        "holdout_used_for_selection": False,
        "folds": folds,
        "ranking": ranking,
        "blocking_reasons": sorted(set(blockers)),
    }


def _walk_forward_fold(
    development: list[ScientificValidationRow],
    *,
    policy: ScientificValidationPolicy,
    index: int,
    test_start: int,
    test_end: int,
    predictions: dict[str, list[tuple[int, bool, float, float]]],
) -> tuple[dict[str, Any], list[str]]:
    train = [
        row
        for row in development
        if row.signal_triggered_at < test_start
        and row.label_observed_at <= test_start - policy.embargo_seconds
    ]
    test = [
        row
        for row in development
        if test_start <= row.signal_triggered_at < test_end
    ]
    blockers = _walk_forward_fold_blockers(train, test, policy)
    metrics = (
        []
        if blockers
        else _evaluate_walk_forward_candidates(train, test, predictions)
    )
    fold = {
        "fold": index + 1,
        "train_count": len(train),
        "test_count": len(test),
        "test_start": test_start,
        "test_end": test_end,
        "purge_seconds": policy.embargo_seconds,
        "embargo_seconds": policy.embargo_seconds,
        "maximum_train_label_observed_at": max(
            (row.label_observed_at for row in train),
            default=None,
        ),
        "candidate_metrics": metrics,
        "blocking_reasons": blockers,
    }
    return fold, blockers


def _walk_forward_fold_blockers(
    train: list[ScientificValidationRow],
    test: list[ScientificValidationRow],
    policy: ScientificValidationPolicy,
) -> list[str]:
    checks = (
        (len(test) < policy.minimum_walk_forward_test_rows, "WALK_FORWARD_TEST_FOLD_TOO_SMALL"),
        (len(train) < policy.minimum_walk_forward_test_rows, "WALK_FORWARD_TRAIN_FOLD_TOO_SMALL"),
        (bool(train) and len(set(_labels(train))) < 2, "WALK_FORWARD_TRAIN_LACKS_BOTH_TARGET_CLASSES"),
        (bool(test) and len(set(_labels(test))) < 2, "WALK_FORWARD_TEST_LACKS_BOTH_TARGET_CLASSES"),
    )
    return [reason for failed, reason in checks if failed]


def _evaluate_walk_forward_candidates(
    train: list[ScientificValidationRow],
    test: list[ScientificValidationRow],
    predictions: dict[str, list[tuple[int, bool, float, float]]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for candidate in _CANDIDATES:
        fitted = _fit_model(candidate, train)
        probability = [_predict(fitted, row) for row in test]
        results.append(
            {
                "model_id": candidate["model_id"],
                "metrics": _probability_metrics(_labels(test), probability),
            }
        )
        predictions[str(candidate["model_id"])].extend(
            (
                row.signal_triggered_at,
                row.target_tp2_hit_within_horizon,
                predicted,
                row.net_utility_r,
            )
            for row, predicted in zip(test, probability, strict=True)
        )
    return results


def _rank_walk_forward(
    predictions: dict[str, list[tuple[int, bool, float, float]]],
) -> list[dict[str, Any]]:
    ranking: list[dict[str, Any]] = []
    for candidate in _CANDIDATES:
        candidate_id = str(candidate["model_id"])
        ordered = sorted(predictions[candidate_id])
        probabilities = [item[2] for item in ordered]
        selected_utilities = [
            item[3]
            for item in ordered
            if item[2] >= 0.5
        ]
        ranking.append(
            {
                "model_id": candidate_id,
                "family": candidate["family"],
                "complexity_rank": candidate["complexity_rank"],
                "oos_row_count": len(ordered),
                "metrics": _probability_metrics(
                    [item[1] for item in ordered],
                    probabilities,
                ),
                "selected_net_utility_r": (
                    round(sum(selected_utilities) / len(selected_utilities), 8)
                    if selected_utilities
                    else None
                ),
            }
        )
    ranking.sort(
        key=lambda item: (
            item["metrics"]["brier_score"],
            item["metrics"]["log_loss"],
            item["complexity_rank"],
            item["model_id"],
        )
    )
    return ranking


def _fit_model(spec: dict[str, Any], rows: list[ScientificValidationRow]) -> dict[str, Any]:
    labels = [1.0 if row.target_tp2_hit_within_horizon else 0.0 for row in rows]
    prevalence = min(max(sum(labels) / len(labels), 1e-6), 1 - 1e-6)
    if spec["family"] == "CONSTANT_PREVALENCE":
        return {"family": spec["family"], "probability": prevalence}
    intercept = math.log(prevalence / (1 - prevalence))
    weight = 0.0
    learning_rate = float(spec["learning_rate"])
    l2_penalty = float(spec["l2_penalty"])
    for _ in range(int(spec["iterations"])):
        intercept_gradient = 0.0
        weight_gradient = 0.0
        for row, label in zip(rows, labels, strict=True):
            feature = (row.predictive_evidence_score - 50.0) / 50.0
            error = _sigmoid(intercept + weight * feature) - label
            intercept_gradient += error
            weight_gradient += error * feature
        intercept -= learning_rate * intercept_gradient / len(rows)
        weight -= learning_rate * (weight_gradient / len(rows) + l2_penalty * weight)
    return {
        "family": spec["family"],
        "intercept": round(intercept, 12),
        "score_weight": round(weight, 12),
        "l2_penalty": l2_penalty,
    }


def _predict(model: dict[str, Any], row: ScientificValidationRow) -> float:
    if model["family"] == "CONSTANT_PREVALENCE":
        return float(model["probability"])
    feature = (row.predictive_evidence_score - 50.0) / 50.0
    return _sigmoid(float(model["intercept"]) + float(model["score_weight"]) * feature)


def _sigmoid(value: float) -> float:
    if value >= 0:
        inverse = math.exp(-value)
        return 1.0 / (1.0 + inverse)
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def _fit_isotonic(probabilities: list[float], labels: list[bool]) -> dict[str, Any]:
    grouped: list[dict[str, float]] = []
    for probability, label in sorted(zip(probabilities, labels, strict=True)):
        if grouped and grouped[-1]["maximum_probability"] == probability:
            grouped[-1]["weight"] += 1
            grouped[-1]["positive"] += int(label)
        else:
            grouped.append(
                {
                    "maximum_probability": probability,
                    "weight": 1,
                    "positive": int(label),
                }
            )
    blocks: list[dict[str, float]] = []
    for group in grouped:
        blocks.append(group)
        while len(blocks) >= 2:
            left = blocks[-2]
            right = blocks[-1]
            if left["positive"] / left["weight"] <= right["positive"] / right["weight"]:
                break
            blocks[-2:] = [
                {
                    "maximum_probability": right["maximum_probability"],
                    "weight": left["weight"] + right["weight"],
                    "positive": left["positive"] + right["positive"],
                }
            ]
    return {
        "contract_version": "isotonic_probability_calibrator_v1",
        "blocks": [
            {
                "maximum_raw_probability": block["maximum_probability"],
                "calibrated_probability": block["positive"] / block["weight"],
                "sample_size": int(block["weight"]),
            }
            for block in blocks
        ],
    }


def _apply_isotonic(calibrator: dict[str, Any], value: float) -> float:
    blocks = calibrator["blocks"]
    for block in blocks:
        if value <= block["maximum_raw_probability"]:
            return float(block["calibrated_probability"])
    return float(blocks[-1]["calibrated_probability"])


def _labels(rows: list[ScientificValidationRow]) -> list[bool]:
    return [row.target_tp2_hit_within_horizon for row in rows]


def _probability_metrics(labels: list[bool], probabilities: list[float]) -> dict[str, Any]:
    if not labels or len(labels) != len(probabilities):
        raise ValueError("probability metrics require aligned non-empty observations")
    clipped = [min(max(value, 1e-12), 1 - 1e-12) for value in probabilities]
    numeric = [1.0 if label else 0.0 for label in labels]
    brier = sum((value - label) ** 2 for value, label in zip(clipped, numeric, strict=True)) / len(labels)
    log_loss = -sum(
        label * math.log(value) + (1 - label) * math.log(1 - value)
        for value, label in zip(clipped, numeric, strict=True)
    ) / len(labels)
    bins: list[list[tuple[float, float]]] = [[] for _ in range(10)]
    for probability, label in zip(clipped, numeric, strict=True):
        bins[min(int(probability * 10), 9)].append((probability, label))
    ece = 0.0
    calibration_bins = []
    for index, bucket in enumerate(bins):
        if not bucket:
            continue
        mean_probability = sum(item[0] for item in bucket) / len(bucket)
        observed_rate = sum(item[1] for item in bucket) / len(bucket)
        ece += len(bucket) / len(labels) * abs(mean_probability - observed_rate)
        calibration_bins.append(
            {
                "bin": index,
                "sample_size": len(bucket),
                "mean_probability": round(mean_probability, 8),
                "observed_rate": round(observed_rate, 8),
            }
        )
    rank_metrics = _tied_rank_metrics(clipped, numeric)
    return {
        "sample_size": len(labels),
        "positive_rate": round(sum(numeric) / len(numeric), 8),
        "brier_score": round(brier, 8),
        "log_loss": round(log_loss, 8),
        "expected_calibration_error": round(ece, 8),
        **rank_metrics,
        "calibration_bins": calibration_bins,
    }


def _tied_rank_metrics(
    probabilities: list[float],
    labels: list[float],
) -> dict[str, Any]:
    groups: dict[float, list[float]] = {}
    for probability, label in zip(probabilities, labels, strict=True):
        groups.setdefault(probability, []).append(label)
    ordered_groups = [groups[value] for value in sorted(groups, reverse=True)]
    positives = sum(labels)
    seen = 0
    true_positives = 0.0
    previous_recall = 0.0
    average_precision = 0.0
    for group in ordered_groups:
        seen += len(group)
        true_positives += sum(group)
        recall = true_positives / positives if positives else 0.0
        precision = true_positives / seen
        average_precision += (recall - previous_recall) * precision
        previous_recall = recall

    precision_k = max(1, math.ceil(len(labels) * 0.10))
    remaining = precision_k
    expected_positives = 0.0
    for group in ordered_groups:
        if remaining <= 0:
            break
        consumed = min(remaining, len(group))
        expected_positives += sum(group) * consumed / len(group)
        remaining -= consumed
    return {
        "pr_auc_average_precision": round(average_precision, 8),
        "precision_at_top_10pct": round(expected_positives / precision_k, 8),
        "precision_at_top_10pct_count": precision_k,
        "rank_tie_policy": "THRESHOLD_GROUP_EXPECTATION",
    }


def _net_utility(
    rows: list[ScientificValidationRow],
    probabilities: list[float],
) -> dict[str, Any]:
    selected = [
        row.net_utility_r
        for row, probability in zip(rows, probabilities, strict=True)
        if probability >= 0.5
    ]
    cumulative = 0.0
    peak = 0.0
    maximum_drawdown = 0.0
    for value in selected:
        cumulative += value
        peak = max(peak, cumulative)
        maximum_drawdown = max(maximum_drawdown, peak - cumulative)
    tail_count = max(1, math.ceil(len(selected) * 0.10)) if selected else 0
    tail = sorted(selected)[:tail_count]
    return {
        "selection_threshold": 0.5,
        "selected_count": len(selected),
        "mean_net_utility_r": (
            round(sum(selected) / len(selected), 8) if selected else None
        ),
        "maximum_drawdown_r": round(maximum_drawdown, 8),
        "bottom_10pct_mean_utility_r": (
            round(sum(tail) / len(tail), 8) if tail else None
        ),
        "cost_basis": "REALIZED",
        "complete_costs_required": True,
    }


def _block_bootstrap(
    rows: list[ScientificValidationRow],
    probabilities: list[float],
    *,
    iterations: int,
    block_size: int,
    seed_material: str,
) -> dict[str, Any]:
    n = len(rows)
    brier_samples: list[float] = []
    utility_samples: list[float] = []
    for iteration in range(iterations):
        indices: list[int] = []
        block = 0
        while len(indices) < n:
            upper = max(1, n - block_size + 1)
            digest = hashlib.sha256(
                f"{seed_material}:{iteration}:{block}".encode("utf-8")
            ).digest()
            start = int.from_bytes(digest[:8], "big") % upper
            indices.extend(range(start, min(start + block_size, n)))
            block += 1
        indices = indices[:n]
        labels = [rows[index].target_tp2_hit_within_horizon for index in indices]
        sampled_probabilities = [probabilities[index] for index in indices]
        brier_samples.append(
            sum(
                (probability - (1.0 if label else 0.0)) ** 2
                for label, probability in zip(
                    labels,
                    sampled_probabilities,
                    strict=True,
                )
            )
            / len(labels)
        )
        selected = [
            rows[index].net_utility_r
            for index, probability in zip(indices, sampled_probabilities, strict=True)
            if probability >= 0.5
        ]
        if selected:
            utility_samples.append(sum(selected) / len(selected))
    return {
        "method": "DETERMINISTIC_MOVING_BLOCK_BOOTSTRAP",
        "iterations": iterations,
        "block_size": block_size,
        "seed_sha256": hashlib.sha256(seed_material.encode("utf-8")).hexdigest(),
        "brier_score_95pct": _percentile_interval(brier_samples),
        "mean_net_utility_r_95pct": (
            _percentile_interval(utility_samples) if utility_samples else None
        ),
        "utility_resamples_with_selected_rows": len(utility_samples),
    }


def _percentile_interval(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    lower = ordered[int((len(ordered) - 1) * 0.025)]
    upper = ordered[int((len(ordered) - 1) * 0.975)]
    return {"lower": round(lower, 8), "upper": round(upper, 8)}


def _regime_audit(
    rows: list[ScientificValidationRow],
    probabilities: list[float],
) -> dict[str, Any]:
    grouped: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        grouped.setdefault(row.regime, []).append(index)
    return {
        "regime_count": len(grouped),
        "regimes": {
            regime: {
                "probability_metrics": _probability_metrics(
                    [rows[index].target_tp2_hit_within_horizon for index in indices],
                    [probabilities[index] for index in indices],
                ),
                "net_utility": _net_utility(
                    [rows[index] for index in indices],
                    [probabilities[index] for index in indices],
                ),
            }
            for regime, indices in sorted(grouped.items())
        },
    }


def _concentration_audit(
    rows: list[ScientificValidationRow],
    probabilities: list[float],
) -> dict[str, Any]:
    selected = [
        row.canonical_symbol
        for row, probability in zip(rows, probabilities, strict=True)
        if probability >= 0.5
    ]
    counts = Counter(selected)
    total = len(selected)
    shares = (
        {
            symbol: count / total
            for symbol, count in sorted(counts.items())
        }
        if total
        else {}
    )
    return {
        "selected_count": total,
        "unique_symbol_count": len(counts),
        "maximum_symbol_share": round(max(shares.values()), 8) if shares else None,
        "herfindahl_hirschman_index": (
            round(sum(share * share for share in shares.values()), 8)
            if shares
            else None
        ),
        "symbol_counts": dict(sorted(counts.items())),
    }


def _performance_gate_reasons(
    *,
    holdout_metrics: dict[str, Any],
    baseline_metrics: dict[str, Any],
    utility: dict[str, Any],
    bootstrap: dict[str, Any],
    regime_audit: dict[str, Any],
    concentration_audit: dict[str, Any],
    policy: ScientificValidationPolicy,
) -> list[str]:
    reasons: list[str] = []
    mean_utility = utility.get("mean_net_utility_r")
    if not isinstance(mean_utility, (int, float)) or mean_utility <= 0:
        reasons.append("HOLDOUT_NET_UTILITY_NOT_POSITIVE")
    utility_interval = bootstrap.get("mean_net_utility_r_95pct")
    if (
        not isinstance(utility_interval, dict)
        or not isinstance(utility_interval.get("lower"), (int, float))
        or utility_interval["lower"] <= 0
    ):
        reasons.append("HOLDOUT_NET_UTILITY_CONFIDENCE_INTERVAL_CROSSES_ZERO")
    if bootstrap["utility_resamples_with_selected_rows"] != bootstrap["iterations"]:
        reasons.append("HOLDOUT_BOOTSTRAP_SELECTION_COVERAGE_INCOMPLETE")
    if holdout_metrics["brier_score"] >= baseline_metrics["brier_score"]:
        reasons.append("HOLDOUT_BRIER_NOT_BETTER_THAN_DEVELOPMENT_PREVALENCE_BASELINE")
    if utility["selected_count"] < policy.minimum_selected_holdout_rows:
        reasons.append("HOLDOUT_SELECTED_EFFECTIVE_SAMPLE_TOO_SMALL")
    if any(
        not isinstance(audit["net_utility"]["mean_net_utility_r"], (int, float))
        or audit["net_utility"]["mean_net_utility_r"] <= 0
        for audit in regime_audit["regimes"].values()
    ):
        reasons.append("HOLDOUT_NET_UTILITY_UNSTABLE_ACROSS_REGIMES")
    maximum_share = concentration_audit["maximum_symbol_share"]
    if not isinstance(maximum_share, (int, float)) or (
        maximum_share > policy.maximum_selected_symbol_share
    ):
        reasons.append("HOLDOUT_SYMBOL_CONCENTRATION_EXCEEDS_POLICY")
    return reasons


def _split_summary(
    split: dict[str, Any],
    policy: ScientificValidationPolicy,
) -> dict[str, Any]:
    return {
        "contract": "CHRONOLOGICAL_DEVELOPMENT_CALIBRATION_UNTOUCHED_HOLDOUT_V1",
        "purge_seconds": policy.embargo_seconds,
        "embargo_seconds": policy.embargo_seconds,
        "boundaries": split["boundaries"],
        "development_rows": len(split["development"]),
        "calibration_rows": len(split["calibration"]),
        "calibration_rows_eligible_after_label_embargo": len(
            _eligible_calibration_rows(split)
        ),
        "holdout_rows": len(split["holdout"]),
        "discarded_boundary_rows": (
            max(
                0,
                int(split["total_rows"]) - sum(
                    len(split[name])
                    for name in ("development", "calibration", "holdout")
                ),
            )
        ),
    }


def _model_card_unavailable(
    packet: ScientificValidationRequest,
    manifest: dict[str, Any],
    reasons: list[str],
) -> dict[str, Any]:
    body = {
        "contract_version": "strict_model_card_v1",
        "status": "NOT_TRAINED",
        "intended_use": "SIGNAL_ONLY_RESEARCH",
        "source_revision": packet.source_revision,
        "dataset_manifest_sha256": manifest["dataset_manifest_sha256"],
        "limitations": sorted(set(reasons)),
        "probability_display_allowed": False,
        "live_execution_allowed": False,
    }
    return {**body, "model_card_sha256": canonical_sha256(body)}


def _model_card(
    packet: ScientificValidationRequest,
    manifest: dict[str, Any],
    *,
    champion_id: str,
    fitted: dict[str, Any],
    calibrator: dict[str, Any],
    holdout_metrics: dict[str, Any],
    utility: dict[str, Any],
    bootstrap: dict[str, Any],
    regime_audit: dict[str, Any],
    concentration_audit: dict[str, Any],
    performance_reasons: list[str],
) -> dict[str, Any]:
    body = {
        "contract_version": "strict_model_card_v1",
        "status": (
            "VALIDATED_AWAITING_OWNER_REVIEW"
            if not performance_reasons
            else "VALIDATED_DO_NOT_PROMOTE"
        ),
        "intended_use": "SIGNAL_ONLY_RESEARCH",
        "model_id": champion_id,
        "source_revision": packet.source_revision,
        "source_dataset_manifest_sha256": packet.source_dataset_manifest_sha256,
        "dataset_manifest_sha256": manifest["dataset_manifest_sha256"],
        "target": {
            "name": "tp2_hit_within_horizon",
            "horizon_seconds": packet.target_horizon_seconds,
        },
        "fitted_model": fitted,
        "calibrator": calibrator,
        "untouched_holdout_metrics": holdout_metrics,
        "holdout_net_utility": utility,
        "holdout_regime_audit": regime_audit,
        "holdout_symbol_concentration_audit": concentration_audit,
        "uncertainty": bootstrap,
        "performance_gate_blockers": performance_reasons,
        "known_limitations": [
            "SIGNAL_ONLY_NO_LIVE_EXECUTION",
            "OWNER_FEATURE_PROMOTION_APPROVAL_REQUIRED",
            "MONITOR_FOR_REGIME_AND_CALIBRATION_DRIFT",
        ],
        "probability_display_allowed": False,
        "live_execution_allowed": False,
    }
    return {**body, "model_card_sha256": canonical_sha256(body)}


def _hash_report(report: dict[str, Any]) -> dict[str, Any]:
    return {**report, "report_sha256": canonical_sha256(report)}
