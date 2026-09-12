import json

from waterfallhunter.core.dashboard_projection import (
    project_dashboard_candidate,
    project_dashboard_payload,
)


def _candidate() -> dict:
    return {
        "symbol": "TEST/USDT:USDT",
        "status": "FUEL-RICH",
        "last_price": 0.25,
        "analysis_status": "ready",
        "analysis_observed_at": 100.0,
        "reference_observed_at": 101.0,
        "execution_suitability": {"huge": "x" * 10_000},
        "metrics": {
            "candle_features": {"raw": "x" * 20_000},
            "entry_decision": {
                "decision": "FORMING",
                "entry_readiness": 61.2,
                "evidence_coverage_pct": 98.0,
                "reason_codes": ["TIMING_INCOMPLETE"],
                "block_reasons": [],
                "trade_plan": None,
                "policy": {
                    "max_analysis_age_seconds": 180,
                    "max_reference_age_seconds": 30,
                    "other": "not-public",
                },
                "research_provenance": {"huge": "x" * 30_000},
                "evidence_summary": {
                    "derivatives": {
                        "oi_change_1h_pct": -2.1,
                        "funding_rate_pct": 0.01,
                        "raw": "x" * 5_000,
                    },
                    "order_flow": {
                        "taker_buy_sell_ratio": 0.7,
                        "sell_share_pct": 68.0,
                        "raw": "x" * 5_000,
                    },
                    "execution": {"spread_pct": 0.02, "raw": "x" * 5_000},
                    "cascade": {
                        "status": "PARTIAL",
                        "readiness_points": 5.4,
                        "maximum_available": 8.0,
                        "components": {"huge": "x" * 5_000},
                    },
                    "cross_exchange_confirmed": True,
                    "anti_chase_extension_atr": 0.8,
                },
            },
            "ai_advisory": {
                "ai_status": "AVAILABLE",
                "ai_advice": "NEUTRAL",
                "ai_confidence": 55,
                "ai_reasoning": "observational",
                "ai_provider": "gemini",
                "raw": "x" * 5_000,
            },
        },
    }


def test_live_projection_preserves_decision_fields_without_raw_diagnostics():
    source = _candidate()
    projected = project_dashboard_candidate(source)
    decision = projected["metrics"]["entry_decision"]

    assert projected["status"] == "FUEL-RICH"
    assert decision["decision"] == "FORMING"
    assert decision["entry_readiness"] == 61.2
    assert decision["evidence_summary"]["cascade"] == {
        "status": "PARTIAL",
        "readiness_points": 5.4,
        "maximum_available": 8.0,
    }
    assert decision["policy"] == {
        "max_analysis_age_seconds": 180,
        "max_reference_age_seconds": 30,
    }
    assert "research_provenance" not in decision
    assert "candle_features" not in projected["metrics"]
    assert "execution_suitability" not in projected


def test_projection_is_bounded_relative_to_raw_candidate():
    source = _candidate()
    raw_bytes = len(json.dumps(source, separators=(",", ":")).encode())
    projected_bytes = len(
        json.dumps(project_dashboard_candidate(source), separators=(",", ":")).encode()
    )
    assert projected_bytes < raw_bytes * 0.2


def test_payload_projection_preserves_backend_terminal_and_research_summaries():
    payload = {
        "total": 1,
        "candidates": {"TEST/USDT:USDT": _candidate()},
        "decision_terminal": {"counts": {"FORMING": 1}},
        "final_ranking": {"ranked": []},
        "signal_funnel": {"total": 1},
    }
    projected = project_dashboard_payload(payload)
    assert projected["decision_terminal"] is payload["decision_terminal"]
    assert projected["final_ranking"] is payload["final_ranking"]
    assert projected["signal_funnel"] is payload["signal_funnel"]
    assert "candle_features" not in projected["candidates"]["TEST/USDT:USDT"]["metrics"]


def test_live_projection_preserves_bounded_observational_reference_plan():
    source = _candidate()
    source["metrics"]["technical_trade_plan_shadow"] = {
        "version": "technical_trade_plan_shadow_v1",
        "observational_only": True,
        "hard_gating_allowed": False,
        "available": True,
        "feasible": True,
        "status": "FEASIBLE",
        "setup": {
            "status": "READY",
            "entry_price": 1.0,
            "stop_loss": 1.1,
            "take_profit_1": 0.9,
            "take_profit_2": 0.8,
            "take_profit_3": 0.7,
            "reward_to_risk": 2.0,
            "raw_heavy_field": "drop-me",
        },
        "reference": {"price": 1.01, "source": "mark", "raw": "drop-me"},
    }
    shadow = project_dashboard_candidate(source)["metrics"]["technical_trade_plan_shadow"]
    assert shadow["available"] is True
    assert shadow["feasible"] is True
    assert shadow["setup"]["take_profit_2"] == 0.8
    assert "raw_heavy_field" not in shadow["setup"]
    assert shadow["reference"] == {"price": 1.01, "source": "mark"}
