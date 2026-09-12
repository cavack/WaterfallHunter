import sqlite3

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core.historical_outcome_store import HistoricalOutcomeStore


def _report():
    return {
        "source": "official public source",
        "source_provenance": {"klines_endpoint": "https://example.test/klines"},
        "generated_at": "2026-08-13T00:00:00+00:00",
        "days": 180,
        "window": {"start_ms": 1_000, "end_ms": 2_000},
        "strategy": "channel_v1",
        "strategy_equivalent": False,
        "net_ev_contract": {"cost_basis": "modeled", "promotion_permitted": False},
        "trades": [
            {
                "symbol": "ETHUSDT", "timestamp": 1_100, "exit_timestamp": 1_200,
                "outcome": "win", "realized_r": 0.5, "net_realized_r": 0.48,
                "exit_reason": "target", "execution_costs": {"complete": True, "basis": "modeled"},
            },
            {
                "symbol": "ETHUSDT", "timestamp": 1_300, "exit_timestamp": 1_400,
                "outcome": "loss", "realized_r": -1.0, "net_realized_r": -1.02,
                "exit_reason": "stop", "execution_costs": {"complete": True, "basis": "modeled"},
            },
        ],
    }


def _store(tmp_path, *, cache_ttl_seconds=60.0):
    db_path = migrate_test_database(tmp_path / "outcomes.db")
    return HistoricalOutcomeStore(
        str(db_path),
        cache_ttl_seconds=cache_ttl_seconds,
    )


def test_import_is_atomic_idempotent_and_operationally_queryable(tmp_path):
    store = _store(tmp_path, cache_ttl_seconds=0)
    first = store.import_report(_report(), report_sha256="a" * 64)
    second = store.import_report(_report(), report_sha256="a" * 64)
    report = store.build_report()

    assert first == {"imported": True, "dataset_id": 1, "event_count": 2, "idempotent": False}
    assert second == {"imported": False, "dataset_id": 1, "event_count": 2, "idempotent": True}
    assert report["operational"] is True
    assert report["observational_only"] is True
    assert report["hard_gating_allowed"] is False
    assert report["threshold_calibration_allowed"] is False
    assert report["summary"]["event_count"] == 2
    assert report["summary"]["win_rate"] == 0.5
    assert report["summary"]["net_expectancy_r"] == -0.27
    assert report["by_symbol"]["ETH/USDT:USDT"]["ranking_eligible"] is False


def test_import_rejects_incomplete_cost_evidence_without_partial_rows(tmp_path):
    store = _store(tmp_path)
    report = _report()
    report["trades"][1]["execution_costs"]["complete"] = False

    with pytest.raises(ValueError, match="complete modeled outcome evidence"):
        store.import_report(report, report_sha256="b" * 64)

    assert store.build_report()["available"] is False


def test_imported_rows_are_immutable(tmp_path):
    store = _store(tmp_path)
    db_path = store.db_path
    store.import_report(_report(), report_sha256="c" * 64)

    with sqlite3.connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE operational_historical_signal_outcomes SET net_realized_r = 99")
