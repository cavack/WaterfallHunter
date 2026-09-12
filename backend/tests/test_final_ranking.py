from waterfallhunter.core.final_ranking import FinalRanking


EVALUATION_TIME = 1_700_000_000.0


def _candidate(
    *,
    status="ARMED",
    score=80.0,
    execution="SUITABLE",
    analysis_age=10.0,
    reference_age=5.0,
):
    return {
        "status": status,
        "score": score,
        "analysis_observed_at": EVALUATION_TIME - analysis_age,
        "reference_observed_at": EVALUATION_TIME - reference_age,
        "execution_suitability": {"status": execution},
        "metrics": {
            "score_version": "score_v2",
            "score": score,
            "trade_eligible": True,
            "strategy_stages": {"hype": True, "damage": True, "setup": True, "trigger": status == "TRIGGERED"},
            "relative_weakness_features": {
                "available": True,
                "timeframes": {
                    "4h": {"relative_return_6bars_pct": -5.0},
                    "1h": {"relative_return_6bars_pct": -4.0},
                    "15m": {"relative_return_6bars_pct": -2.0},
                    "5m": {"relative_return_6bars_pct": -1.0},
                },
            },
        },
    }


def _partial_watch_candidate(*, score, coverage):
    candidate = _candidate(status="WATCH", score=score)
    candidate["metrics"].update(
        {
            "score": None,
            "trade_eligible": False,
            "observation_score": score,
            "observation_score_version": "score_v2_watch_v1",
            "watch_score": {
                "score_version": "score_v2_watch_v1",
                "score": score,
                "coverage_pct": coverage,
            },
        }
    )
    return candidate


def test_ranking_is_observational_and_never_claims_eligibility_or_anti_chase_veto():
    packet = FinalRanking.for_candidate(
        "TEST",
        _candidate(),
        evaluation_time=EVALUATION_TIME,
    )

    assert packet["observational_only"] is True
    assert packet["trade_eligible"] is None
    assert packet["anti_chase"]["status"] == "NOT_EVALUATED"
    assert packet["anti_chase"]["veto"] is None


def test_analysis_and_reference_freshness_are_independent_and_never_zero_filled():
    missing_reference = _candidate()
    missing_reference["reference_observed_at"] = None
    packet = FinalRanking.for_candidate(
        "MISSING_REFERENCE",
        missing_reference,
        evaluation_time=EVALUATION_TIME,
    )

    assert packet["components"]["analysis_freshness"]["available"] is True
    assert packet["components"]["reference_freshness"]["available"] is False
    assert packet["components"]["reference_freshness"]["points"] is None
    assert packet["confidence"] < 1.0


def test_ranking_rewards_readiness_execution_and_complete_evidence():
    result = FinalRanking.rank({
        "WATCH": _candidate(status="WATCH", score=60.0, execution="MARGINAL", analysis_age=20.0),
        "READY": _candidate(status="TRIGGERED", score=85.0, execution="SUITABLE", analysis_age=5.0),
    }, evaluation_time=EVALUATION_TIME)

    assert result["top"][0]["symbol"] == "READY"
    assert result["top"][0]["rank"] == 1
    assert result["observational_only"] is True


def test_unknown_execution_is_missing_not_suitable_or_zero():
    packet = FinalRanking.for_candidate(
        "UNKNOWN",
        _candidate(execution="UNKNOWN"),
        evaluation_time=EVALUATION_TIME,
    )

    assert packet["components"]["execution_quality"]["available"] is False
    assert "execution_quality" in packet["missing_components"]


def test_partial_watch_score_is_penalized_by_evidence_coverage():
    packet = FinalRanking.for_candidate(
        "PARTIAL",
        _partial_watch_candidate(score=90.0, coverage=50.0),
        evaluation_time=EVALUATION_TIME,
    )

    signal = packet["components"]["signal_score"]
    assert signal["available"] is True
    assert signal["value"] == 0.45
    assert signal["points"] == 9.0


def test_partial_watch_score_without_coverage_is_unavailable_not_assumed_complete():
    candidate = _partial_watch_candidate(score=90.0, coverage=50.0)
    candidate["metrics"]["watch_score"].pop("coverage_pct")

    packet = FinalRanking.for_candidate(
        "NO_COVERAGE",
        candidate,
        evaluation_time=EVALUATION_TIME,
    )

    signal = packet["components"]["signal_score"]
    assert signal["available"] is False
    assert signal["points"] is None
    assert "signal_score" in packet["missing_components"]


def test_complete_score_v2_is_not_coverage_penalized():
    packet = FinalRanking.for_candidate(
        "STRICT",
        _candidate(score=80.0),
        evaluation_time=EVALUATION_TIME,
    )

    signal = packet["components"]["signal_score"]
    assert signal["available"] is True
    assert signal["value"] == 0.8
    assert signal["points"] == 16.0


def test_ineligible_primary_score_is_unavailable_without_coverage_qualified_watch_score():
    candidate = _candidate(score=99.0)
    candidate["metrics"]["trade_eligible"] = False
    candidate["metrics"].pop("watch_score", None)

    packet = FinalRanking.for_candidate(
        "INELIGIBLE_PRIMARY",
        candidate,
        evaluation_time=EVALUATION_TIME,
    )

    signal = packet["components"]["signal_score"]
    assert signal["available"] is False
    assert signal["points"] is None
    assert "signal_score" in packet["missing_components"]


def test_non_score_v2_primary_score_is_unavailable_without_coverage_qualified_watch_score():
    candidate = _candidate(score=99.0)
    candidate["metrics"]["score_version"] = "legacy_score_v1"
    candidate["metrics"].pop("watch_score", None)

    packet = FinalRanking.for_candidate(
        "LEGACY_PRIMARY",
        candidate,
        evaluation_time=EVALUATION_TIME,
    )

    signal = packet["components"]["signal_score"]
    assert signal["available"] is False
    assert signal["points"] is None
    assert "signal_score" in packet["missing_components"]


def test_ranking_rejects_hidden_or_invalid_evaluation_clock():
    import pytest

    with pytest.raises(ValueError, match="evaluation_time"):
        FinalRanking.for_candidate("TEST", _candidate(), evaluation_time=float("nan"))


def test_persisted_strict_lifecycle_chain_counts_as_full_readiness():
    candidate = _candidate(status="TRIGGERED", score=90.0)
    candidate["metrics"]["strategy_stages"] = {
        "hype": False,
        "damage": False,
        "setup": False,
        "trigger": True,
    }
    candidate["metrics"]["stage_lifecycle"] = {
        "available": True,
        "stale": False,
        "observational_only": False,
        "hard_gating_allowed": True,
        "confirmed": {
            "hype": True,
            "damage": True,
            "setup": True,
            "trigger": True,
            "passed": True,
        },
    }

    packet = FinalRanking.for_candidate(
        "PERSISTED_CHAIN",
        candidate,
        evaluation_time=EVALUATION_TIME,
    )

    assert packet["components"]["cascade_readiness"]["value"] == 1.0
