from copy import deepcopy

from waterfallhunter.core.lbank_execution_suitability import (
    LBankExecutionSuitability,
    SUITABILITY_MARGINAL,
    SUITABILITY_POOR,
    SUITABILITY_SUITABLE,
    SUITABILITY_UNKNOWN,
)


def summary(
    *,
    symbol="TEST/USDT:USDT",
    evidence="SUFFICIENT",
    availability=1.0,
    cost100_p90=0.08,
    spread_p90=0.07,
    depth25_p50=10_000.0,
):
    return {
        "symbol": symbol,
        "availability_rate": availability,
        "evidence": {
            "status": evidence,
            "observed_samples": 36,
            "observation_span_hours": 37.0,
        },
        "metrics": {
            "cost_100_pct": {
                "p90": cost100_p90,
            },
            "spread_pct": {
                "p90": spread_p90,
            },
            "depth_25bps_min_usdt": {
                "p50": depth25_p50,
            },
        },
    }


def test_suitable_reference_profile():
    classifier = (
        LBankExecutionSuitability()
    )

    result = (
        classifier.classify_summary(
            summary(
                symbol="PEPE/USDT:USDT",
                availability=1.0,
                cost100_p90=0.008439,
                spread_p90=0.0070455,
                depth25_p50=978_044.0,
            )
        )
    )

    assert (
        result["status"]
        == SUITABILITY_SUITABLE
    )

    assert (
        result["failed_checks"]
        == []
    )

    assert (
        result["observational_only"]
        is True
    )

    assert (
        result["trade_eligible"]
        is None
    )


def test_marginal_reference_profile():
    classifier = (
        LBankExecutionSuitability()
    )

    result = (
        classifier.classify_summary(
            summary(
                symbol="ROBO/USDT:USDT",
                availability=1.0,
                cost100_p90=0.1403514,
                spread_p90=0.0841614,
                depth25_p50=3649.75861,
            )
        )
    )

    assert (
        result["status"]
        == SUITABILITY_MARGINAL
    )


def test_poor_high_cost_reference_profile():
    classifier = (
        LBankExecutionSuitability()
    )

    result = (
        classifier.classify_summary(
            summary(
                symbol="TCC/USDT:USDT",
                availability=1.0,
                cost100_p90=3.5621814,
                spread_p90=2.6931816,
                depth25_p50=9.792224,
            )
        )
    )

    assert (
        result["status"]
        == SUITABILITY_POOR
    )

    assert (
        "cost100_p90"
        in result["failed_checks"]
    )

    assert (
        "spread_p90"
        in result["failed_checks"]
    )

    assert (
        "depth25_p50"
        in result["failed_checks"]
    )


def test_poor_low_availability_reference_profile():
    classifier = (
        LBankExecutionSuitability()
    )

    result = (
        classifier.classify_summary(
            summary(
                symbol="VANRY/USDT:USDT",
                availability=(
                    0.6666666666666666
                ),
                cost100_p90=2.7636049,
                spread_p90=2.6840804,
                depth25_p50=178.9238455,
            )
        )
    )

    assert (
        result["status"]
        == SUITABILITY_POOR
    )

    assert (
        "availability"
        in result["failed_checks"]
    )


def test_insufficient_evidence_is_unknown():
    classifier = (
        LBankExecutionSuitability()
    )

    result = (
        classifier.classify_summary(
            summary(
                evidence="INSUFFICIENT",
            )
        )
    )

    assert (
        result["status"]
        == SUITABILITY_UNKNOWN
    )


def test_missing_required_metric_is_unknown():
    classifier = (
        LBankExecutionSuitability()
    )

    data = summary()

    data[
        "metrics"
    ][
        "cost_100_pct"
    ][
        "p90"
    ] = None

    result = (
        classifier.classify_summary(
            data
        )
    )

    assert (
        result["status"]
        == SUITABILITY_UNKNOWN
    )

    assert (
        "cost_100_pct_p90"
        in result["failed_checks"]
    )


def test_exact_suitable_boundaries_are_inclusive():
    classifier = (
        LBankExecutionSuitability()
    )

    result = (
        classifier.classify_summary(
            summary(
                availability=0.95,
                cost100_p90=0.1225,
                spread_p90=0.1123,
                depth25_p50=3590.0,
            )
        )
    )

    assert (
        result["status"]
        == SUITABILITY_SUITABLE
    )


def test_exact_marginal_boundaries_are_inclusive():
    classifier = (
        LBankExecutionSuitability()
    )

    result = (
        classifier.classify_summary(
            summary(
                availability=0.90,
                cost100_p90=0.305,
                spread_p90=0.220,
                depth25_p50=1190.0,
            )
        )
    )

    assert (
        result["status"]
        == SUITABILITY_MARGINAL
    )


def test_one_marginal_failure_is_poor():
    classifier = (
        LBankExecutionSuitability()
    )

    result = (
        classifier.classify_summary(
            summary(
                availability=1.0,
                cost100_p90=0.306,
                spread_p90=0.10,
                depth25_p50=10_000.0,
            )
        )
    )

    assert (
        result["status"]
        == SUITABILITY_POOR
    )


def test_classifier_does_not_mutate_input_summary():
    classifier = (
        LBankExecutionSuitability()
    )

    data = summary()

    before = deepcopy(
        data
    )

    classifier.classify_summary(
        data
    )

    assert data == before


def test_boolean_and_non_finite_values_do_not_count_as_metrics():
    classifier = (
        LBankExecutionSuitability()
    )

    data = summary(
        availability=True,
        cost100_p90=float("nan"),
    )

    result = (
        classifier.classify_summary(
            data
        )
    )

    assert (
        result["status"]
        == SUITABILITY_UNKNOWN
    )


def test_threshold_packet_is_explicit_and_stable():
    classifier = (
        LBankExecutionSuitability()
    )

    thresholds = (
        classifier.thresholds()
    )

    assert (
        thresholds[
            "suitable"
        ][
            "maximum_cost_100_p90_pct"
        ]
        == 0.1225
    )

    assert (
        thresholds[
            "marginal"
        ][
            "minimum_depth_25bps_p50_usdt"
        ]
        == 1190.0
    )
