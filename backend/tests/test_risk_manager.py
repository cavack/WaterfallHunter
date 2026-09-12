import pytest

import waterfallhunter.core.risk_manager as risk_manager
from waterfallhunter.core.risk_manager import (
    build_signal_leverage_advisory,
    get_leverage,
    recommend_signal_leverage,
)


def _metrics(
    *,
    score=100.0,
    entry=100.0,
    stop=102.0,
    atr_pct=0.5,
    spread=0.03,
    slippage=0.03,
    exit_slippage=None,
    maximum_leverage=None,
):
    return {
        "score": score,
        "position_setup": {"entry_price": entry, "stop_loss": stop},
        "candle_features": {
            "5m": {"atr_pct": atr_pct},
            "15m": {"atr_pct": atr_pct},
        },
        "microstructure": {
            "spread_pct": spread,
            "slippage_pct": slippage,
            "exit_slippage_pct": (
                slippage if exit_slippage is None else exit_slippage
            ),
        },
        "market_constraints": {"maximum_leverage": maximum_leverage},
    }


def test_legacy_leverage_contract_remains_unchanged_for_replay_compatibility():
    assert get_leverage("BTC/USDT:USDT") == 2
    assert get_leverage("ETH/USDT:USDT") == 2
    assert get_leverage("PEPE/USDT:USDT") == 3


def test_adaptive_leverage_uses_signal_risk_not_symbol_name():
    clean = recommend_signal_leverage(
        _metrics(),
        {"status": "SUITABLE"},
    )
    assert clean == 18

    medium = recommend_signal_leverage(
        _metrics(score=90.0, stop=104.0, atr_pct=1.8, spread=0.12, slippage=0.12),
        {"status": "MARGINAL"},
    )
    assert 4 <= medium < clean


def test_adaptive_leverage_changes_when_risk_changes_for_same_symbol_evidence_shape():
    low_risk = recommend_signal_leverage(_metrics(score=98.0, stop=102.0, atr_pct=0.7), {"status": "SUITABLE"})
    higher_risk = recommend_signal_leverage(_metrics(score=98.0, stop=106.0, atr_pct=2.5, spread=0.18, slippage=0.2), {"status": "MARGINAL"})
    assert 4 <= higher_risk < low_risk <= 18


def test_adaptive_leverage_rejects_when_independent_risk_bound_requires_below_4x():
    with pytest.raises(ValueError, match="below 4x"):
        recommend_signal_leverage(
            _metrics(score=86.0, stop=112.0, atr_pct=6.0, spread=0.28, slippage=0.29),
            {"status": "POOR"},
        )

def test_adaptive_leverage_requires_strict_finite_signal_inputs():
    with pytest.raises(ValueError):
        recommend_signal_leverage({"score": None}, {"status": "SUITABLE"})


def test_supplied_invalid_exit_slippage_is_unavailable_not_silently_fallback():
    for invalid in (-0.01, float("nan")):
        advisory = build_signal_leverage_advisory(
            _metrics(exit_slippage=invalid),
            {"available": True, "status": "SUITABLE", "maximum_leverage": 18},
        )
        assert advisory["status"] == "UNAVAILABLE"
        assert advisory["leverage"] is None
        assert "execution friction" in advisory["reason"]


def test_explicit_null_exit_slippage_is_unavailable_not_absent_fallback():
    metrics = _metrics(slippage=0.12)
    metrics["microstructure"]["exit_slippage_pct"] = None
    advisory = build_signal_leverage_advisory(
        metrics,
        {"available": True, "status": "SUITABLE", "maximum_leverage": 18},
    )
    assert advisory["status"] == "UNAVAILABLE"
    assert advisory["leverage"] is None
    assert "execution friction" in advisory["reason"]


def test_absent_exit_slippage_uses_entry_slippage_fallback():
    metrics = _metrics(slippage=0.12)
    metrics["microstructure"].pop("exit_slippage_pct")
    advisory = build_signal_leverage_advisory(
        metrics,
        {"available": True, "status": "SUITABLE", "maximum_leverage": 18},
    )
    assert advisory["status"] == "AVAILABLE"
    assert advisory["leverage"] == 12


def test_advisory_carries_complete_normalized_causal_input_packet():
    metrics = _metrics(score=92.0, spread=0.04, slippage=0.06, exit_slippage=0.08)
    metrics["market_constraints"] = {"maximum_leverage": 11}
    advisory = build_signal_leverage_advisory(
        metrics,
        {"available": True, "status": "MARGINAL", "maximum_leverage": 10, "observed_samples": 55},
    )
    causal = advisory["causal_input"]
    assert advisory["policy_version"] == "adaptive_signal_leverage_v2"
    assert causal["score"] == 92.0
    assert causal["position_setup"] == {"status": "", "entry_price": 100.0, "stop_loss": 102.0}
    assert causal["microstructure"] == {
        "spread_pct": 0.04, "slippage_pct": 0.06,
        "exit_slippage_present": True, "exit_slippage_pct": 0.08,
    }
    assert causal["candle_atr_pct"] == {"5m": 0.5, "15m": 0.5, "1h": None}
    assert causal["market_constraints"] == {"maximum_leverage": 11.0}
    assert causal["execution_suitability"] == {
        "available": True, "status": "MARGINAL", "maximum_leverage": 10.0,
    }


def test_adaptive_leverage_uses_exit_side_slippage_ceiling():
    leverage = recommend_signal_leverage(
        _metrics(slippage=0.03, exit_slippage=0.29),
        {"status": "SUITABLE"},
    )

    assert leverage == 6


def test_adaptive_leverage_uses_current_market_maximum():
    leverage = recommend_signal_leverage(
        _metrics(maximum_leverage=7),
        {"status": "SUITABLE"},
    )

    assert leverage == 7


def test_adaptive_leverage_falls_back_to_suitability_maximum_when_constraint_is_null():
    leverage = recommend_signal_leverage(
        _metrics(maximum_leverage=None),
        {"status": "SUITABLE", "maximum_leverage": 5},
    )
    assert leverage == 5


def test_adaptive_leverage_advisory_available_uses_canonical_policy():
    advisory = build_signal_leverage_advisory(_metrics(), {"status": "SUITABLE"})
    assert advisory["status"] == "AVAILABLE"
    assert advisory["leverage"] == 18
    assert advisory["policy_version"] == "adaptive_signal_leverage_v2"


def test_adaptive_leverage_advisory_missing_inputs_is_unavailable_without_fallback():
    advisory = build_signal_leverage_advisory({"score": None}, {"status": "SUITABLE"})
    assert advisory["status"] == "UNAVAILABLE"
    assert advisory["leverage"] is None
    assert "strict finite score" in advisory["reason"]


def test_adaptive_leverage_advisory_below_four_is_not_recommended_without_clamp():
    advisory = build_signal_leverage_advisory(
        _metrics(score=86.0, stop=112.0, atr_pct=6.0, spread=0.28, slippage=0.29),
        {"status": "POOR"},
    )
    assert advisory["status"] == "NOT_RECOMMENDED"
    assert advisory["leverage"] is None
    assert "below 4x" in advisory["reason"]


def test_btc_legacy_two_x_cannot_influence_symbol_agnostic_live_advisory():
    assert get_leverage("BTC/USDT:USDT") == 2
    advisory = build_signal_leverage_advisory(_metrics(), {"status": "SUITABLE"})
    assert advisory["status"] == "AVAILABLE"
    assert advisory["leverage"] == 18
    assert advisory["symbol_agnostic"] is True


def test_unexpected_adaptive_calculator_failure_is_explicitly_unavailable(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("calculator offline")

    monkeypatch.setattr(risk_manager, "recommend_signal_leverage", fail)
    advisory = risk_manager.build_signal_leverage_advisory(
        _metrics(), {"status": "SUITABLE"}
    )
    assert advisory["status"] == "UNAVAILABLE"
    assert advisory["leverage"] is None
    assert advisory["reason"] == "adaptive leverage calculation unavailable"
    assert advisory["error_type"] == "RuntimeError"


def test_complete_low_score_is_not_recommended_not_unavailable():
    advisory = build_signal_leverage_advisory(
        _metrics(score=84.0),
        {"status": "SUITABLE", "evidence_status": "SUFFICIENT", "observed_samples": 40},
    )
    assert advisory["status"] == "NOT_RECOMMENDED"
    assert advisory["leverage"] is None


def test_rejected_position_setup_is_not_recommended_not_available():
    metrics = _metrics(score=100.0)
    metrics["position_setup"]["status"] = "REJECTED: Minimum notional requirement failed (5 USDT)"
    advisory = build_signal_leverage_advisory(
        metrics,
        {"available": True, "status": "SUITABLE", "maximum_leverage": 18},
    )
    assert advisory["status"] == "NOT_RECOMMENDED"
    assert advisory["leverage"] is None
    assert "position setup rejected" in advisory["reason"].lower()


def test_unknown_execution_suitability_is_unavailable_not_fabricated_eight_x():
    advisory = build_signal_leverage_advisory(
        _metrics(score=100.0),
        {"status": "UNKNOWN", "reason": "required execution metrics missing"},
    )
    assert advisory["status"] == "UNAVAILABLE"
    assert advisory["leverage"] is None


def test_leverage_advisory_persists_normalized_execution_suitability_input():
    execution = {
        "symbol": "TEST/USDT:USDT",
        "status": "MARGINAL",
        "reason": "usable",
        "evidence_status": "SUFFICIENT",
        "observed_samples": 37,
        "observation_span_hours": 38.0,
        "availability_rate": 0.97,
        "cost_100_p90_pct": 0.11,
        "spread_p90_pct": 0.09,
        "depth_25bps_p50_usdt": 5000.0,
        "failed_checks": ["spread_p90"],
        "observational_only": True,
        "trade_eligible": None,
    }
    advisory = build_signal_leverage_advisory(_metrics(score=95.0), execution)
    assert advisory["execution_suitability_input"] == execution


def test_non_actionable_canonical_decision_is_not_recommended_not_unavailable():
    advisory = build_signal_leverage_advisory(
        {"score": None},
        {"status": "SUITABLE"},
        decision_status="NO_TRADE",
    )
    assert advisory["status"] == "NOT_RECOMMENDED"
    assert advisory["leverage"] is None
    assert "NO_TRADE" in advisory["reason"]


def test_entry_ready_still_fails_closed_when_causal_leverage_inputs_are_missing():
    advisory = build_signal_leverage_advisory(
        {"score": None},
        {"status": "SUITABLE"},
        decision_status="ENTRY_READY",
    )
    assert advisory["status"] == "UNAVAILABLE"
    assert advisory["leverage"] is None


def test_entry_ready_valid_inputs_keep_numeric_adaptive_leverage():
    advisory = build_signal_leverage_advisory(
        _metrics(),
        {"available": True, "status": "SUITABLE", "maximum_leverage": 18},
        decision_status="ENTRY_READY",
    )
    assert advisory["status"] == "AVAILABLE"
    assert advisory["leverage"] == 18
