import asyncio
import copy
import hashlib
import json
import math
import sqlite3
import time

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core.production_evidence import (
    ProductionEvidenceRecorder,
    build_production_replay_context,
    causal_age_seconds,
)
from waterfallhunter.core.signal_metadata import canonical_sha256


def _contract():
    return {
        "contract_schema_version": "production_decision_contract_v2",
        "application": {"app_version": "test", "source_tree_sha256": "a" * 64},
        "strategy": {}, "microstructure": {}, "derivatives": {},
        "position": {}, "recorder": {}, "runtime_settings": {},
    }


def _valid_replay_context(**overrides):
    decision = {
        "contract_version": "entry_decision_v1",
        "decision": "FORMING",
        "entry_readiness": 61.0,
        "block_reasons": [],
    }
    context = {
        "canonical_lifecycle_id": 7,
        "canonical_entry_decision": decision,
        "canonical_entry_decision_sha256": canonical_sha256(decision),
        "decision_evaluated_at": 110,
        "decision_clock_at": 110.5,
        "analysis_observed_at": 100,
        "reference_observed_at": 105.0,
        "freshness": {
            "policy_version": "entry_decision_v1",
            "max_analysis_age_seconds": 180.0,
            "max_reference_age_seconds": 60.0,
            "analysis_age_seconds": 10.0,
            "reference_age_seconds": 5.0,
            "analysis_pass": True,
            "reference_pass": True,
        },
        "trade_plan_feasibility_shadow": {
            "version": "technical_trade_plan_shadow_v1",
            "observational_only": True,
            "hard_gating_allowed": False,
            "trade_eligible": False,
            "available": True,
            "feasible": True,
            "status": "FEASIBLE",
        },
        "decision_contract_sha256": canonical_sha256(_contract()),
    }
    context.update(overrides)
    return context


def _result():
    return {
        "is_valid": False,
        "score": None,
        "suggested_status": "REJECTED",
        "observation_status": "PRE-TRIGGER",
        "metrics": {
            "exchange": "binance", "mapped_symbol": "TEST/USDT:USDT",
            "candle_analysis": {"details": {"5m": {"valid": True}}, "source_capture": {
                "raw_ohlcv_captured": True, "confirmation_ohlcv_captured": True,
                "primary_closed_ohlcv": {"5m": [[100, 1, 2, .5, 1.5, 10]]},
                "confirmation_closed_ohlcv_15m": [[100, 1, 2, .5, 1.5, 10]],
            }},
            "microstructure": {"approved": True, "spread_pct": .04, "slippage_pct": .05,
                "source_capture": {"raw_trades_captured": True, "fresh_trades": [{"timestamp": 123,"side":"sell","price":1.0,"amount":1.0}] * 20,
                    "orderbook_snapshots_captured": True, "orderbook_snapshots": [{"timestamp":123,"bids":[[1,2]],"asks":[[1.1,2]]}] * 3,
                    "market_filters_captured": True, "market": {"contractSize":1.0,"limits":{"amount":{"min":.01},"cost":{"min":1.0}},"precision":{}}}},
            "derivatives": {"available": True, "source_capture": {"provider":"binance"}},
            "strategy_stages": {"hype": True}, "quality_gates": {"cross_exchange_confirmed": False},
            "ticker": {"last": 1.0, "mark": 1.0, "vwap": 1.1},
            "liquidation_flow": {"available": True, "observed_at": 109, "long_liquidation_notional_1m": 100000.0, "short_liquidation_notional_1m": 1000.0, "liquidation_velocity_usd_per_min": 100000.0, "burst_ratio": 2.0},
            "cascade_intelligence": {"status":"PASS","readiness_points":7.0,"maximum_available":10.0,"components":{"liquidations":{"available":True,"points":1.2,"maximum":2.0}}},
        },
    }


def test_v9_records_replay_context_and_exact_liquidation_without_mutating_input(tmp_path):
    db_path = str(migrate_test_database(tmp_path / "evidence.db"))
    recorder = ProductionEvidenceRecorder(db_path)
    result = _result()
    before = copy.deepcopy(result)
    context = _valid_replay_context()
    assert recorder.record("TEST/USDT:USDT", candidate_state="PRE-TRIGGER", reference_source="lbank", reference_price=1.0, result=result, decision_contract=_contract(), observed_at=110.0, replay_context=context)
    assert result == before
    payload = recorder.read_payload(1)
    assert payload["schema_version"] == "production_decision_evidence_v9"
    assert payload["observational_only"] is True
    assert payload["hard_gating_allowed"] is False
    assert payload["replay_complete"] is True
    assert payload["replay_unavailable_reason"] is None
    assert payload["replay_context"] == context
    assert payload["metrics"]["liquidation_flow"]["available"] is True
    assert payload["metrics"]["cascade_intelligence"]["components"]["liquidations"]["points"] == 1.2
    raw = json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    with sqlite3.connect(db_path) as conn:
        stored = conn.execute("SELECT schema_version,evidence_sha256 FROM production_evidence_snapshots").fetchone()
    assert stored[0] == "production_decision_evidence_v9"
    assert stored[1] == hashlib.sha256(raw).hexdigest()


def test_replay_context_freezes_freshness_policy_and_canonical_decision():

    decision = {"contract_version":"entry_decision_v1","decision":"ENTRY_READY","entry_readiness":82.0,"block_reasons":[],"lifecycle_id":9}
    context = build_production_replay_context(
        lifecycle_id=9,
        entry_decision=decision,
        decision_evaluated_at=200,
        analysis_observed_at=180,
        reference_observed_at=195.0,
        policy_version="entry_decision_v1",
        max_analysis_age_seconds=180.0,
        max_reference_age_seconds=60.0,
        trade_plan_feasibility_shadow={"status":"FEASIBLE"},
    )
    assert context["canonical_lifecycle_id"] == 9
    assert context["canonical_entry_decision"] == decision
    assert isinstance(context["canonical_entry_decision_sha256"], str)
    assert len(context["canonical_entry_decision_sha256"]) == 64
    assert context["decision_evaluated_at"] == 200
    assert context["freshness"] == {
        "policy_version":"entry_decision_v1",
        "max_analysis_age_seconds":180.0,
        "max_reference_age_seconds":60.0,
        "analysis_age_seconds":20.0,
        "reference_age_seconds":5.0,
        "analysis_pass":True,
        "reference_pass":True,
    }


def test_v9_row_without_replay_context_is_retained_and_explicitly_unavailable(tmp_path):
    db_path = str(migrate_test_database(tmp_path / "evidence.db"))
    recorder = ProductionEvidenceRecorder(db_path)

    assert recorder.record(
        "TEST/USDT:USDT",
        candidate_state="WATCH",
        reference_source=None,
        reference_price=None,
        result={"is_valid": False, "metrics": {"error": "reference unavailable"}},
        decision_contract=_contract(),
        observed_at=110.0,
        replay_context=None,
    )

    payload = recorder.read_payload(1)
    assert payload["schema_version"] == "production_decision_evidence_v9"
    assert payload["replay_complete"] is False
    assert payload["replay_unavailable_reason"] == "REPLAY_CONTEXT_ABSENT"
    assert payload["replay_context"] == {}


@pytest.mark.parametrize(
    ("analysis_observed_at", "reference_observed_at"),
    [
        (201.0, 202.0),
        (math.nan, math.inf),
        (math.inf, -math.inf),
    ],
)
def test_replay_context_never_marks_future_or_nonfinite_evidence_fresh(
    analysis_observed_at,
    reference_observed_at,
):

    context = build_production_replay_context(
        lifecycle_id=9,
        entry_decision={"decision": "FORMING"},
        decision_evaluated_at=200,
        analysis_observed_at=analysis_observed_at,
        reference_observed_at=reference_observed_at,
        policy_version="entry_policy_v1",
        max_analysis_age_seconds=180.0,
        max_reference_age_seconds=60.0,
        trade_plan_feasibility_shadow={"status": "UNAVAILABLE"},
    )

    assert context["freshness"]["analysis_age_seconds"] is None
    assert context["freshness"]["reference_age_seconds"] is None
    assert context["freshness"]["analysis_pass"] is False
    assert context["freshness"]["reference_pass"] is False


def test_runtime_passes_canonical_replay_context_to_v9_recorder(monkeypatch):
    from waterfallhunter import main

    symbol = "V9/USDT:USDT"
    lifecycle_id = 17
    now = int(time.time())
    captured = []
    monkeypatch.setattr(main.scanner, "active_candidates", {symbol: {}})
    monkeypatch.setattr(main.scanner, "get_live_reference", lambda _symbol: (1.0, now - 2.0))
    monkeypatch.setattr(main.execution_decision_logger, "observe_evaluation", lambda *args, **kwargs: None)

    async def cross_check_symbol(*args, **kwargs):
        return {
            "is_valid": False,
            "score": None,
            "suggested_status": "REJECTED",
            "observation_status": "PRE-TRIGGER",
            "metrics": {"exchange": "binance", "mapped_symbol": symbol},
        }

    monkeypatch.setattr(main.validator, "cross_check_symbol", cross_check_symbol)
    monkeypatch.setattr(main.validator, "build_technical_trade_plan_shadow", lambda _metrics: {
        "version":"technical_trade_plan_shadow_v1", "observational_only":True,
        "hard_gating_allowed":False, "trade_eligible":False,
        "available":True, "feasible":True, "status":"FEASIBLE", "setup":{"status":"READY"},
    })
    monkeypatch.setattr(main, "_apply_deterministic_entry_gate", lambda _s, state, _m: (state, False))
    monkeypatch.setattr(main, "build_signal_leverage_advisory", lambda metrics, execution_suitability=None, **kwargs: {
        "policy_version": "adaptive_signal_leverage_v1",
        "minimum": 4, "maximum": 18, "symbol_agnostic": True,
        "signal_only": True, "advisory_only": True,
        "status": "UNAVAILABLE", "leverage": None, "reason": "test unavailable",
    })
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _symbol: None)
    monkeypatch.setattr(main.entry_decision_store, "append_if_changed", lambda *args, **kwargs: 123)

    def canonical_decision(*args, **kwargs):
        return {
            "contract_version":"entry_decision_v1",
            "policy_version":"entry_policy_v1",
            "evaluated_at":int(kwargs["evaluated_at"]),
            "decision":"FORMING",
            "entry_readiness":61.0,
            "evidence_coverage_pct":98.0,
            "block_reasons":[],
            "reason_codes":[],
            "trade_plan":None,
            "lifecycle_id":lifecycle_id,
        }

    monkeypatch.setattr(main, "build_entry_decision", canonical_decision)

    def capture_record(*args, **kwargs):
        captured.append(copy.deepcopy(kwargs.get("replay_context")))
        raise RuntimeError("stop-after-v9-capture")

    monkeypatch.setattr(main.production_evidence_recorder, "record", capture_record)
    evaluation = main.evaluate_candidate(symbol, {
        "status":"PRE-TRIGGER", "lifecycle_id":lifecycle_id,
        "scan_eligible":True, "quote_volume":3_000_000.0, "last_price":1.0,
    })
    with pytest.raises(RuntimeError, match="stop-after-v9-capture"):
        asyncio.run(evaluation)

    assert len(captured) == 1
    context = captured[0]
    assert context["canonical_lifecycle_id"] == lifecycle_id
    assert context["canonical_entry_decision"]["decision"] == "FORMING"
    assert context["canonical_entry_decision"]["event_id"] == 123
    assert context["freshness"]["analysis_pass"] is True
    assert context["freshness"]["reference_pass"] is True
    assert context["trade_plan_feasibility_shadow"]["status"] == "FEASIBLE"
    assert isinstance(context["decision_contract_sha256"], str)
    assert len(context["decision_contract_sha256"]) == 64


def test_v9_rejects_tampered_replay_hashes_as_fail_closed(tmp_path):
    db_path = str(migrate_test_database(tmp_path / "evidence.db"))

    recorder = ProductionEvidenceRecorder(db_path)
    assert recorder.record(
        "TEST/USDT:USDT",
        candidate_state="PRE-TRIGGER",
        reference_source="lbank",
        reference_price=1.0,
        result=_result(),
        decision_contract=_contract(),
        observed_at=110.0,
        replay_context=_valid_replay_context(
            canonical_entry_decision_sha256="b" * 64,
        ),
    )
    payload = recorder.read_payload(1)
    assert payload["replay_complete"] is False
    assert payload["replay_unavailable_reason"] == "REPLAY_CONTEXT_HASH_MISMATCH"

    recorder = ProductionEvidenceRecorder(
        str(migrate_test_database(tmp_path / "evidence2.db"))
    )
    assert recorder.record(
        "TEST/USDT:USDT",
        candidate_state="PRE-TRIGGER",
        reference_source="lbank",
        reference_price=1.0,
        result=_result(),
        decision_contract=_contract(),
        observed_at=110.0,
        replay_context=_valid_replay_context(
            decision_contract_sha256="c" * 64,
        ),
    )
    payload = recorder.read_payload(1)
    assert payload["replay_complete"] is False
    assert payload["replay_unavailable_reason"] == "REPLAY_CONTEXT_HASH_MISMATCH"


def test_v9_rejects_contract_hash_that_does_not_match_stored_contract(tmp_path):
    db_path = str(migrate_test_database(tmp_path / "evidence.db"))
    other_contract = _contract()
    other_contract["application"]["app_version"] = "tampered"

    recorder = ProductionEvidenceRecorder(db_path)
    assert recorder.record(
        "TEST/USDT:USDT",
        candidate_state="PRE-TRIGGER",
        reference_source="lbank",
        reference_price=1.0,
        result=_result(),
        decision_contract=other_contract,
        observed_at=110.0,
        replay_context=_valid_replay_context(),
    )
    payload = recorder.read_payload(1)
    assert payload["replay_complete"] is False
    assert payload["replay_unavailable_reason"] == "REPLAY_CONTEXT_HASH_MISMATCH"


@pytest.mark.parametrize("section", ["freshness", "trade_plan_feasibility_shadow"])
def test_v9_empty_nested_replay_sections_are_not_replay_complete(tmp_path, section):
    db_path = str(migrate_test_database(tmp_path / "evidence.db"))
    context = _valid_replay_context(**{section: {}})

    recorder = ProductionEvidenceRecorder(db_path)
    assert recorder.record(
        "TEST/USDT:USDT",
        candidate_state="PRE-TRIGGER",
        reference_source="lbank",
        reference_price=1.0,
        result=_result(),
        decision_contract=_contract(),
        observed_at=110.0,
        replay_context=context,
    )
    payload = recorder.read_payload(1)
    assert payload["replay_complete"] is False
    assert payload["replay_unavailable_reason"] == "REPLAY_CONTEXT_INCOMPLETE"


def test_causal_age_rejects_negative_and_nonfinite_timestamps():
    assert causal_age_seconds(-1, -2) is None
    assert causal_age_seconds(100, -1) is None
    assert causal_age_seconds(-1, 100) is None
    assert causal_age_seconds(math.nan, 1.0) is None
    assert causal_age_seconds(math.inf, 1.0) is None
    assert causal_age_seconds(100, math.nan) is None
    assert causal_age_seconds(100, -math.inf) is None
    assert causal_age_seconds(100, 90.0) == 10.0


def test_full_precision_decision_clock_keeps_same_second_reference_causal():
    context = build_production_replay_context(
        lifecycle_id=9,
        entry_decision={"decision": "FORMING"},
        decision_evaluated_at=200,
        decision_clock_at=200.9,
        analysis_observed_at=190,
        reference_observed_at=200.5,
        policy_version="entry_policy_v1",
        max_analysis_age_seconds=180.0,
        max_reference_age_seconds=60.0,
        trade_plan_feasibility_shadow={
            "version": "technical_trade_plan_shadow_v1",
            "available": True,
            "feasible": True,
            "status": "FEASIBLE",
            "trade_eligible": False,
        },
    )

    assert context["decision_evaluated_at"] == 200
    assert context["decision_clock_at"] == 200.9
    assert context["freshness"]["reference_age_seconds"] == pytest.approx(0.4)
    assert context["freshness"]["reference_pass"] is True


def test_integer_only_decision_clock_preserves_fail_closed_future_reference():
    context = build_production_replay_context(
        lifecycle_id=9,
        entry_decision={"decision": "FORMING"},
        decision_evaluated_at=200,
        analysis_observed_at=190,
        reference_observed_at=200.5,
        policy_version="entry_policy_v1",
        max_analysis_age_seconds=180.0,
        max_reference_age_seconds=60.0,
        trade_plan_feasibility_shadow={
            "version": "technical_trade_plan_shadow_v1",
            "available": True,
            "feasible": True,
            "status": "FEASIBLE",
            "trade_eligible": False,
        },
    )

    assert context["decision_clock_at"] == 200
    assert context["freshness"]["reference_age_seconds"] is None
    assert context["freshness"]["reference_pass"] is False



def test_source_hint_bootstrap_is_lifecycle_fenced_and_preserves_full_clock(tmp_path):
    db_path = str(migrate_test_database(tmp_path / "hints.db"))
    recorder = ProductionEvidenceRecorder(db_path, bucket_seconds=60)
    result = _result()
    result["metrics"]["watch_prefilter"] = {
        "version": "watch_prefilter_v1",
        "conclusive_no_hype": True,
    }
    result["metrics"]["full_analysis_observed_at"] = 120.0
    context = _valid_replay_context(
        canonical_lifecycle_id=7,
        analysis_observed_at=120,
        decision_evaluated_at=170,
        decision_clock_at=170.5,
        reference_observed_at=165.0,
    )
    context["canonical_entry_decision"]["lifecycle_id"] = 7
    context["canonical_entry_decision_sha256"] = canonical_sha256(
        context["canonical_entry_decision"]
    )
    assert recorder.record(
        "TEST/USDT:USDT",
        candidate_state="WATCH",
        reference_source="lbank",
        reference_price=1.0,
        result=result,
        decision_contract=_contract(),
        observed_at=180.0,
        replay_context=context,
    )

    hints = recorder.latest_source_hints({"TEST/USDT:USDT": 7}, lookback_rows=100)
    assert hints == {
        "TEST/USDT:USDT": {
            "exchange": "binance",
            "mapped_symbol": "TEST/USDT:USDT",
            "lifecycle_id": 7,
            "full_analysis_observed_at": 120.0,
            "evidence_observed_at": 180.0,
        }
    }
    assert recorder.latest_source_hints({"TEST/USDT:USDT": 8}, lookback_rows=100) == {}


def test_source_hint_bootstrap_does_not_fall_back_behind_latest_sourceless_snapshot(tmp_path):
    db_path = str(migrate_test_database(tmp_path / "hints-latest.db"))
    recorder = ProductionEvidenceRecorder(db_path, bucket_seconds=60)
    context = _valid_replay_context(canonical_lifecycle_id=7)
    context["canonical_entry_decision"]["lifecycle_id"] = 7
    context["canonical_entry_decision_sha256"] = canonical_sha256(context["canonical_entry_decision"])
    assert recorder.record(
        "TEST/USDT:USDT", candidate_state="WATCH", reference_source="lbank",
        reference_price=1.0, result=_result(), decision_contract=_contract(),
        observed_at=120.0, replay_context=context,
    )
    unavailable = {
        "is_valid": False, "score": None, "suggested_status": "REJECTED",
        "metrics": {"error": "no exchange source selected"},
    }
    assert recorder.record(
        "TEST/USDT:USDT", candidate_state="WATCH", reference_source="lbank",
        reference_price=1.0, result=unavailable, decision_contract=_contract(),
        observed_at=240.0, replay_context=context,
    )
    assert recorder.latest_source_hints({"TEST/USDT:USDT": 7}, lookback_rows=100) == {}


def test_latest_source_hints_rejects_fast_watch_without_full_analysis_clock(tmp_path):
    db_path = str(migrate_test_database(tmp_path / "fast-watch-hint.db"))
    recorder = ProductionEvidenceRecorder(db_path, bucket_seconds=60)
    result = _result()
    result["metrics"]["watch_prefilter"] = {
        "version": "watch_prefilter_v2_4h_waterfall",
        "observational_only": True,
        "conclusive_no_hype": True,
    }
    result["metrics"].pop("full_analysis_observed_at", None)
    context = _valid_replay_context(canonical_lifecycle_id=7)
    context["canonical_entry_decision"]["lifecycle_id"] = 7
    context["canonical_entry_decision_sha256"] = canonical_sha256(context["canonical_entry_decision"])
    assert recorder.record(
        "TEST/USDT:USDT", candidate_state="WATCH", reference_source="lbank",
        reference_price=1.0, result=result, decision_contract=_contract(),
        observed_at=200.0, replay_context=context,
    )
    assert recorder.latest_source_hints({"TEST/USDT:USDT": 7}, lookback_rows=100) == {}
