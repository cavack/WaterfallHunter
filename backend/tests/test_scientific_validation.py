from __future__ import annotations

import pytest
from pydantic import ValidationError

from waterfallhunter.core.scientific_validation import (
    DAY_SECONDS,
    ScientificValidationPolicy,
    ScientificValidationRequest,
    ScientificValidationRow,
    _apply_isotonic,
    _fit_isotonic,
    _probability_metrics,
    scientific_source_manifest_sha256,
    validate_strict_scientific_evidence,
)
from waterfallhunter.core.signal_metadata import canonical_sha256


def _request(*, days: int = 70, row_count: int = 180) -> ScientificValidationRequest:
    start = 1_800_000_000
    interval = max(1, days * DAY_SECONDS // (row_count - 1))
    rows = []
    for index in range(row_count):
        triggered_at = start + index * interval
        target = index % 2 == 0
        row = {
                "signal_id": f"strict-{index:04d}",
                "signal_class": "STRICT",
                "strategy_profile": "strict_score_v2",
                "score_version": "score_v2",
                "signal_triggered_at": triggered_at,
                "feature_observed_at": triggered_at - 60,
                "label_observed_at": triggered_at + DAY_SECONDS,
                "predictive_evidence_score": 90 if target else 10,
                "target_tp2_hit_within_horizon": target,
                "canonical_symbol": f"ASSET{index % 4}-USDT-PERP",
                "regime": "BEAR_TREND" if index % 4 < 2 else "RANGE_HIGH_VOL",
                "net_utility_r": 1.0 if target else -0.75,
                "execution_costs_complete": True,
                "execution_cost_basis": "REALIZED",
            }
        rows.append({**row, "source_row_sha256": canonical_sha256(row)})
    revision = "b" * 40
    generated_at = rows[-1]["label_observed_at"]
    validated_rows = tuple(ScientificValidationRow.model_validate(row) for row in rows)
    return ScientificValidationRequest.model_validate(
        {
            "source_dataset_manifest_sha256": scientific_source_manifest_sha256(
                source_revision=revision,
                generated_at=generated_at,
                target_horizon_seconds=DAY_SECONDS,
                rows=validated_rows,
            ),
            "source_revision": revision,
            "generated_at": generated_at,
            "target_horizon_seconds": DAY_SECONDS,
            "rows": rows,
        }
    )


def _refresh_source_identity(payload: dict) -> dict:
    for row in payload["rows"]:
        material = {key: value for key, value in row.items() if key != "source_row_sha256"}
        row["source_row_sha256"] = canonical_sha256(material)
    validated_rows = tuple(
        ScientificValidationRow.model_validate(row) for row in payload["rows"]
    )
    payload["source_dataset_manifest_sha256"] = scientific_source_manifest_sha256(
        source_revision=payload["source_revision"],
        generated_at=payload["generated_at"],
        target_horizon_seconds=payload["target_horizon_seconds"],
        rows=validated_rows,
    )
    return payload


def test_complete_validation_is_deterministic_purged_and_owner_gated() -> None:
    request = _request()

    first = validate_strict_scientific_evidence(request)
    second = validate_strict_scientific_evidence(request)

    assert first == second
    assert first["report_sha256"] == second["report_sha256"]
    assert first["evidence_gate_status"] == "COMPLETE_FOR_OWNER_REVIEW"
    assert first["promotion_decision"] == "OWNER_REVIEW_REQUIRED"
    assert first["promotion_allowed"] is False
    assert first["feature_promotion_approval_required"] is True
    assert (
        first["champion_challenger"]["champion_model_id"]
        == "regularized_logistic_score_v1"
    )
    assert first["champion_challenger"]["holdout_used_for_selection"] is False
    assert first["independent_calibration"]["fit_source"] == "CALIBRATION_SPLIT_ONLY"
    assert first["independent_calibration"]["maximum_label_observed_at"] <= (
        first["split_summary"]["boundaries"]["holdout_start"]
        - first["validation_policy"]["embargo_seconds"]
    )
    assert first["untouched_holdout"]["used_for_selection"] is False
    assert first["untouched_holdout"]["multi_regime_audit"]["regime_count"] == 2
    assert first["untouched_holdout"]["symbol_concentration_audit"][
        "maximum_symbol_share"
    ] <= 0.6
    assert "pr_auc_average_precision" in first["untouched_holdout"][
        "probability_metrics"
    ]
    assert "maximum_drawdown_r" in first["untouched_holdout"]["net_utility"]
    assert (
        first["untouched_holdout"]["block_bootstrap_confidence_intervals"]
        ["method"]
        == "DETERMINISTIC_MOVING_BLOCK_BOOTSTRAP"
    )
    for fold in first["walk_forward"]["folds"]:
        assert fold["maximum_train_label_observed_at"] <= (
            fold["test_start"] - first["validation_policy"]["embargo_seconds"]
        )
    assert first["model_card"]["probability_display_allowed"] is False
    assert first["model_card"]["live_execution_allowed"] is False


def test_holdout_changes_cannot_change_champion_selection() -> None:
    request = _request()
    original = validate_strict_scientific_evidence(request)
    holdout_start = original["split_summary"]["boundaries"]["holdout_start"]
    changed_rows = []
    for row in request.rows:
        payload = row.model_dump(mode="json")
        if row.signal_triggered_at >= holdout_start:
            payload["target_tp2_hit_within_horizon"] = (
                not row.target_tp2_hit_within_horizon
            )
            payload["net_utility_r"] = -row.net_utility_r
        changed_rows.append(payload)
    changed_payload = request.model_dump(mode="json")
    changed_payload["rows"] = changed_rows
    changed_request = ScientificValidationRequest.model_validate(
        _refresh_source_identity(changed_payload)
    )

    changed = validate_strict_scientific_evidence(changed_request)

    assert changed["champion_challenger"]["champion_model_id"] == (
        original["champion_challenger"]["champion_model_id"]
    )
    assert changed["champion_challenger"]["ranking"] == (
        original["champion_challenger"]["ranking"]
    )
    assert changed["untouched_holdout"]["probability_metrics"] != (
        original["untouched_holdout"]["probability_metrics"]
    )


def test_insufficient_strict_evidence_fails_closed_with_model_card() -> None:
    report = validate_strict_scientific_evidence(
        _request(days=9, row_count=24)
    )

    assert report["evidence_gate_status"] == "INSUFFICIENT"
    assert report["promotion_decision"] == "DO_NOT_PROMOTE"
    assert report["promotion_allowed"] is False
    assert "STRICT_OBSERVATION_SPAN_BELOW_SIX_WEEKS" in report["blocking_reasons"]
    assert "STRICT_TOTAL_ROW_COUNT_BELOW_POLICY_MINIMUM" in report["blocking_reasons"]
    assert report["champion_challenger"]["available"] is False
    assert report["untouched_holdout"]["available"] is False
    assert report["model_card"]["status"] == "NOT_TRAINED"
    assert len(report["model_card"]["model_card_sha256"]) == 64


def test_complete_data_with_negative_net_utility_still_does_not_promote() -> None:
    request = _request()
    payload = request.model_dump(mode="json")
    for row in payload["rows"]:
        row["net_utility_r"] = -1.0

    report = validate_strict_scientific_evidence(_refresh_source_identity(payload))

    assert report["evidence_gate_status"] == "PERFORMANCE_GATE_FAILED"
    assert report["promotion_decision"] == "DO_NOT_PROMOTE"
    assert "HOLDOUT_NET_UTILITY_NOT_POSITIVE" in report["blocking_reasons"]
    assert report["model_card"]["status"] == "VALIDATED_DO_NOT_PROMOTE"
    assert report["promotion_allowed"] is False


def test_complete_data_with_symbol_concentration_still_does_not_promote() -> None:
    payload = _request().model_dump(mode="json")
    for row in payload["rows"]:
        row["canonical_symbol"] = "ONE-ASSET-USDT-PERP"

    report = validate_strict_scientific_evidence(_refresh_source_identity(payload))

    assert report["evidence_gate_status"] == "PERFORMANCE_GATE_FAILED"
    assert "HOLDOUT_SYMBOL_CONCENTRATION_EXCEEDS_POLICY" in report["blocking_reasons"]
    assert report["promotion_decision"] == "DO_NOT_PROMOTE"


def test_validation_rejects_non_strict_future_or_mixed_horizon_rows() -> None:
    base = _request(days=9, row_count=24).model_dump(mode="json")
    non_strict = {**base["rows"][0], "signal_class": "EXPERIMENTAL"}
    future = {
        **base["rows"][0],
        "feature_observed_at": base["rows"][0]["signal_triggered_at"] + 1,
    }
    mixed_horizon = {
        **base["rows"][0],
        "label_observed_at": base["rows"][0]["label_observed_at"] + 1,
    }

    with pytest.raises(ValidationError):
        ScientificValidationRequest.model_validate({**base, "rows": [non_strict]})
    with pytest.raises(ValidationError, match="feature evidence"):
        ScientificValidationRequest.model_validate({**base, "rows": [future]})
    with pytest.raises(ValidationError, match="declared target horizon"):
        ScientificValidationRequest.model_validate(
            _refresh_source_identity({**base, "rows": [mixed_horizon]})
        )


def test_validation_rejects_coerced_time_and_ambiguous_revision() -> None:
    base = _request(days=9, row_count=24).model_dump(mode="json")
    coerced = {**base["rows"][0], "signal_triggered_at": "1800000000"}

    with pytest.raises(ValidationError):
        ScientificValidationRequest.model_validate({**base, "rows": [coerced]})
    with pytest.raises(ValidationError, match="40- or 64-character"):
        ScientificValidationRequest.model_validate({**base, "source_revision": "b" * 41})


def test_validation_rejects_utility_magnitudes_that_cannot_be_safely_aggregated() -> None:
    payload = _request(days=9, row_count=24).model_dump(mode="json")
    payload["rows"][0]["net_utility_r"] = 1e308

    with pytest.raises(ValidationError):
        ScientificValidationRequest.model_validate(
            _refresh_source_identity(payload)
        )


def test_policy_is_hash_bound() -> None:
    policy = ScientificValidationPolicy.v1()
    material = policy.model_dump(mode="json")
    material["minimum_total_rows"] += 1

    with pytest.raises(ValidationError, match="policy hash mismatch"):
        ScientificValidationPolicy.model_validate(material)


def test_source_rows_and_manifest_are_content_bound() -> None:
    payload = _request().model_dump(mode="json")
    payload["rows"][0]["net_utility_r"] = 99

    with pytest.raises(ValidationError, match="source row hash mismatch"):
        ScientificValidationRequest.model_validate(payload)

    payload = _request().model_dump(mode="json")
    payload["source_dataset_manifest_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="dataset manifest hash mismatch"):
        ScientificValidationRequest.model_validate(payload)


def test_report_candidate_registry_does_not_mutate_global_specs() -> None:
    first = validate_strict_scientific_evidence(_request())
    first["candidate_registry"][1]["iterations"] = 1

    second = validate_strict_scientific_evidence(_request())

    assert second["candidate_registry"][1]["iterations"] == 1_000


def test_rank_metrics_are_invariant_to_order_with_tied_scores() -> None:
    probabilities = [0.5] * 10
    labels = [True, False] * 5

    first = _probability_metrics(labels, probabilities)
    second = _probability_metrics(list(reversed(labels)), probabilities)

    assert first["pr_auc_average_precision"] == second["pr_auc_average_precision"] == 0.5
    assert first["precision_at_top_10pct"] == second["precision_at_top_10pct"] == 0.5


def test_isotonic_application_uses_exact_fitted_boundaries() -> None:
    values = [0.12345678901234, 0.12345678901235, 0.9]
    calibrator = _fit_isotonic(values, [False, True, True])

    assert calibrator["blocks"][0]["maximum_raw_probability"] == values[0]
    assert _apply_isotonic(calibrator, values[0]) == 0.0
