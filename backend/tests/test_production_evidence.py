import copy
import sqlite3

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core.production_evidence import ProductionEvidenceRecorder


def _contract():
    return {
        "contract_schema_version": "production_decision_contract_v2",
        "application": {"app_version": "test", "source_tree_sha256": "a" * 64},
        "strategy": {},
        "microstructure": {},
        "derivatives": {},
        "position": {},
        "recorder": {},
        "runtime_settings": {},
    }


def _result():
    return {
        "is_valid": True,
        "score": 72.0,
        "suggested_status": "ARMED",
        "metrics": {
            "exchange": "binance",
            "mapped_symbol": "TEST/USDT:USDT",
            "data_sources": {"confirmation": "bybit"},
            "valid_candle_timeframes": 4,
            "candle_analysis": {
                "details": {"5m": {"valid": True}},
                "source_capture": {
                    "raw_ohlcv_captured": True,
                    "confirmation_ohlcv_captured": True,
                    "primary_closed_ohlcv": {
                        "5m": [[100, 1, 2, 0.5, 1.5, 10]],
                    },
                    "confirmation_closed_ohlcv_15m": [[100, 1, 2, 0.5, 1.5, 10]],
                },
            },
            "microstructure": {
                "approved": True,
                "source_capture": {
                    "raw_trades_captured": True,
                    "fresh_trades": [
                        {"timestamp": 123, "side": "sell", "price": 1.0, "amount": 1.0}
                    ] * 20,
                    "orderbook_snapshots": [
                        {"timestamp": 123, "received_at": 1.0, "bids": [[1.0, 2.0]], "asks": [[1.1, 2.0]]}
                    ] * 3,
                    "orderbook_snapshots_captured": True,
                    "market": {
                        "contractSize": 1.0,
                        "limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}},
                        "precision": {},
                    },
                    "market_filters_captured": True,
                },
            },
            "derivatives": {
                "available": True,
                "source_capture": {
                    "provider": "binance",
                    "funding_rows": [{"fundingRate": "0.001"}],
                    "taker_rows": [{"buySellRatio": "0.8"}],
                    "top_trader_rows": [{"longShortRatio": "1.2"}],
                    "open_interest_rows": [{"sumOpenInterestValue": "100"}],
                },
            },
            "strategy_stages": {"hype": True, "damage": True, "setup": True, "trigger": False, "passed": False},
            "quality_gates": {"cross_exchange_confirmed": True},
            "orderbook": {"bids": [[1.0, 2.0]] * 30, "asks": [[1.1, 2.0]] * 30, "timestamp": 123},
            "ticker": {"last": 1.05, "mark": 1.04, "quoteVolume": 1_000_000, "info": {"must_not": "persist"}},
        },
    }


def test_records_one_immutable_compressed_packet_per_symbol_bucket(tmp_path):
    db_path = str(migrate_test_database(tmp_path / "evidence.db"))
    recorder = ProductionEvidenceRecorder(db_path, bucket_seconds=300)
    result = _result()

    assert recorder.record("TEST/USDT:USDT", candidate_state="PRE-TRIGGER", reference_source="lbank", reference_price=1.05, result=result, decision_contract=_contract(), observed_at=1_000.0) is True
    assert recorder.record("TEST/USDT:USDT", candidate_state="PRE-TRIGGER", reference_source="lbank", reference_price=1.05, result=result, decision_contract=_contract(), observed_at=1_001.0) is False

    report = recorder.build_report(now=1_100.0)
    assert report["snapshot_count_24h"] == 1
    assert report["coverage"]["decision_packet_complete_rate"] == 1.0
    assert report["replay"]["source_replay_ready"] is True
    assert report["replay"]["source_replay_ready_rate"] == 1.0
    assert report["replay"]["feature_replay_ready"] is True
    assert report["replay"]["feature_replay_ready_rate"] == 1.0
    assert report["replay"]["triggered_path_replay_ready"] is True
    assert report["replay"]["decision_provenance_captured"] is True
    assert report["replay"]["raw_derivatives_captured"] is True
    assert report["replay"]["production_evidence_complete"] is True
    assert report["replay"]["confirmation_ohlcv_captured"] is True
    assert report["storage"]["compressed_bytes_24h"] < report["storage"]["uncompressed_bytes_24h"]

    payload = recorder.read_payload(1)
    assert len(payload["metrics"]["orderbook"]["bids"]) == 25
    assert "info" not in payload["metrics"]["ticker"]
    assert payload["metrics"]["ticker"]["mark"] == 1.04
    assert payload["capture_limitations"]["raw_ohlcv_captured"] is True
    assert payload["capture_limitations"]["raw_trades_captured"] is True
    assert payload["capture_limitations"]["raw_derivatives_captured"] is True
    assert payload["capture_limitations"]["decision_provenance_captured"] is True
    assert payload["capture_limitations"]["confirmation_ohlcv_captured"] is True
    assert payload["result"]["decision_reason"] == "strict trade gates passed"
    assert payload["metrics"]["source_capture"]["derivatives"]["selected"]["provider"] == "binance"
    assert "source_capture" not in payload["metrics"]["derivatives"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT code_sha256_v5 FROM production_evidence_snapshots"
        ).fetchone()[0] == "a" * 64
    assert "source_capture" not in payload["metrics"]["candle_analysis"]
    assert "source_capture" not in payload["metrics"]["microstructure"]

    with sqlite3.connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM production_evidence_snapshots")


def test_final_trigger_event_is_not_hidden_by_bucket_deduplication(tmp_path):
    db_path = str(migrate_test_database(tmp_path / "evidence.db"))
    recorder = ProductionEvidenceRecorder(db_path, bucket_seconds=300)
    ordinary = _result()
    final = copy.deepcopy(ordinary)
    final["suggested_status"] = "TRIGGERED"
    final["metrics"]["production_decision"] = {
        "final": True,
        "path": "TRIGGERED",
        "reason": "persisted",
        "recorded_after_persistence": True,
        "signal_id": 42,
    }

    assert recorder.record(
        "TEST/USDT:USDT", candidate_state="ARMED", reference_source="lbank",
        reference_price=1.05, result=ordinary, decision_contract=_contract(),
        observed_at=1_000.0,
    ) is True
    assert recorder.record(
        "TEST/USDT:USDT", candidate_state="ARMED", reference_source="lbank",
        reference_price=1.05, result=final, decision_contract=_contract(),
        observed_at=1_001.0,
    ) is True

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id,bucket_started_at FROM production_evidence_snapshots ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    assert rows[0][1] >= 0
    assert rows[1][1] < 0
    assert recorder.read_payload(rows[1][0])["metrics"]["production_decision"]["signal_id"] == 42


def test_failure_packet_is_recorded_without_claiming_completeness(tmp_path):
    recorder = ProductionEvidenceRecorder(str(migrate_test_database(tmp_path / "evidence.db")))
    recorder.record(
        "FAIL/USDT:USDT",
        candidate_state="WATCH",
        reference_source=None,
        reference_price=None,
        result={"is_valid": False, "metrics": {"error": "no reference"}},
        observed_at=2_000.0,
    )

    report = recorder.build_report(now=2_100.0)
    assert report["snapshot_count_24h"] == 1
    assert report["coverage"]["decision_packet_complete_rate"] == 0.0
    assert recorder.read_payload(1)["metrics"]["error"] == "no reference"


def test_missing_raw_confirmation_is_not_marked_production_complete(tmp_path):
    recorder = ProductionEvidenceRecorder(str(migrate_test_database(tmp_path / "evidence.db")))
    result = copy.deepcopy(_result())
    source = result["metrics"]["candle_analysis"]["source_capture"]
    source["confirmation_ohlcv_captured"] = False
    source["confirmation_closed_ohlcv_15m"] = None

    assert recorder.record(
        "NO-CONFIRM/USDT:USDT",
        candidate_state="WATCH",
        reference_source="lbank",
        reference_price=1.0,
        result=result,
        decision_contract=_contract(),
        observed_at=3_000.0,
    ) is True
    payload = recorder.read_payload(1)
    assert payload["capture_limitations"]["feature_replay_ready"] is False
    assert payload["capture_limitations"]["production_evidence_complete"] is False
