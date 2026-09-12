import pytest
from pydantic import ValidationError

from waterfallhunter import main


def test_runtime_lifecycle_decision_clock_is_taken_after_sources() -> None:
    evidence = main._build_runtime_lifecycle_v2_evidence(
        metrics={
            "microstructure": {
                "observed_at": 1000.60,
            },
        },
        decision_clock_at=1000.75,
        analysis_observed_at=1000.10,
        reference_observed_at=999.50,
    )

    assert evidence.decision_at == 1001
    assert evidence.decision_clock_at == 1000.75
    assert max(evidence.required_observed_at) <= evidence.decision_clock_at


def test_runtime_lifecycle_raw_clock_rejects_future_source_inside_ceil_second() -> None:
    with pytest.raises(ValidationError, match="source timestamp"):
        main._build_runtime_lifecycle_v2_evidence(
            metrics={
                "microstructure": {
                    "observed_at": 1001.00,
                },
            },
            decision_clock_at=1000.75,
            analysis_observed_at=1000.10,
            reference_observed_at=999.50,
        )


def test_runtime_lifecycle_freshness_uses_raw_clock_not_rounded_persistence_time() -> None:
    evidence = main._build_runtime_lifecycle_v2_evidence(
        metrics={
            "validated_lbank_constraints": {
                "constraints_observed_at": 999.50,
                "expires_at": 1001.00,
            },
        },
        decision_clock_at=1000.10,
        analysis_observed_at=1000.00,
        reference_observed_at=999.50,
    )

    assert evidence.decision_at == 1001
    assert evidence.decision_clock_at == 1000.10
    assert evidence.lbank_constraints_fresh is True


def test_runtime_lifecycle_global_freshness_uses_raw_clock() -> None:
    evidence = main._build_runtime_lifecycle_v2_evidence(
        metrics={
            "microstructure": {
                "observed_at": 1000.00,
            },
        },
        decision_clock_at=1000.10,
        analysis_observed_at=940.15,
        reference_observed_at=999.50,
    )

    assert evidence.decision_at == 1001
    assert evidence.decision_clock_at == 1000.10
    assert evidence.fresh is True
