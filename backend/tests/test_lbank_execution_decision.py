import sqlite3

from schema_test_support import migrate_test_database
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.lbank_execution_decision import (
    AGREE_ACCEPT,
    COMPARISON_UNKNOWN,
    SOURCE_CATALOGUE_SNAPSHOT,
    SOURCE_HUNTER_EVALUATION,
    VOLUME_PASS_EXECUTION_REJECT,
    VOLUME_REJECT_EXECUTION_ACCEPT,
    LBankExecutionDecisionLogger,
)


class FakeEnricher:
    def __init__(self, packets):
        self.packets = packets
        self.calls = []

    def for_symbol(self, symbol):
        self.calls.append(symbol)
        return dict(
            self.packets.get(
                symbol,
                {
                    "status": "UNKNOWN",
                    "evidence_status": "INSUFFICIENT",
                },
            )
        )


def _db_path(tmp_path) -> str:
    db_path = tmp_path / "registry.db"
    migrate_test_database(db_path)
    return str(db_path)


def candidate(*, scan_eligible, volume):
    return {
        "last_price": 0.01,
        "quote_volume": volume,
        "is_meme": False,
        "scan_eligible": scan_eligible,
    }


def read_rows(db_path, source):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM lbank_execution_decision_log
                WHERE source = ?
                ORDER BY symbol
                """,
                (source,),
            ).fetchall()
        ]


def test_comparison_kind_maps_proxy_disagreements():
    compare = LBankExecutionDecisionLogger.comparison_kind
    assert compare(True, "SUITABLE") == AGREE_ACCEPT
    assert (
        compare(True, "POOR")
        == VOLUME_PASS_EXECUTION_REJECT
    )
    assert (
        compare(False, "MARGINAL")
        == VOLUME_REJECT_EXECUTION_ACCEPT
    )
    assert compare(False, "UNKNOWN") == COMPARISON_UNKNOWN


def test_evaluations_are_aggregated_and_persisted_without_trade_decision(
    tmp_path,
):
    db_path = _db_path(tmp_path)
    DBAdapter(db_path)
    logger = LBankExecutionDecisionLogger(
        db_path,
        enricher=FakeEnricher({}),
    )
    packet = {
        "status": "POOR",
        "evidence_status": "SUFFICIENT",
    }

    for observed_at in (1000.0, 1010.0):
        assert logger.observe_evaluation(
            "POOR/USDT:USDT",
            volume_gate_passed=True,
            scan_eligible=True,
            candidate_state="WATCH",
            observed_at=observed_at,
            packet=packet,
        ) is True

    result = logger.flush_evaluations()
    assert result == {
        "persisted": True,
        "rows": 1,
        "evaluations": 2,
    }

    rows = read_rows(
        db_path,
        SOURCE_HUNTER_EVALUATION,
    )
    assert len(rows) == 1
    assert rows[0]["evaluation_count"] == 2
    assert (
        rows[0]["disagreement_kind"]
        == VOLUME_PASS_EXECUTION_REJECT
    )
    assert rows[0]["observational_only"] == 1
    assert rows[0]["trade_eligible"] is None


def test_volume_gate_is_independent_from_combined_scan_eligibility(
    tmp_path,
):
    logger = LBankExecutionDecisionLogger(
        _db_path(tmp_path),
        enricher=FakeEnricher({}),
        volume_gate_min_usdt=2_000_000.0,
    )
    assert logger.volume_gate_passes(2_000_000.0) is True
    assert logger.volume_gate_passes(1_999_999.0) is False
    assert logger.volume_gate_passes(None) is False


def test_catalogue_snapshot_covers_both_proxy_error_directions(
    tmp_path,
):
    db_path = _db_path(tmp_path)
    db = DBAdapter(db_path)
    symbols = {
        "GOOD/USDT:USDT": candidate(
            scan_eligible=True,
            volume=3_000_000.0,
        ),
        "FALSEPOS/USDT:USDT": candidate(
            scan_eligible=True,
            volume=3_000_000.0,
        ),
        "FALSENEG/USDT:USDT": candidate(
            scan_eligible=False,
            volume=500_000.0,
        ),
        "UNKNOWN/USDT:USDT": candidate(
            scan_eligible=False,
            volume=500_000.0,
        ),
    }
    db.update_candidates(symbols)
    packets = {
        "GOOD/USDT:USDT": {
            "status": "SUITABLE",
            "evidence_status": "SUFFICIENT",
        },
        "FALSEPOS/USDT:USDT": {
            "status": "POOR",
            "evidence_status": "SUFFICIENT",
        },
        "FALSENEG/USDT:USDT": {
            "status": "MARGINAL",
            "evidence_status": "SUFFICIENT",
        },
        "UNKNOWN/USDT:USDT": {
            "status": "UNKNOWN",
            "evidence_status": "INSUFFICIENT",
        },
    }
    logger = LBankExecutionDecisionLogger(
        db_path,
        enricher=FakeEnricher(packets),
        snapshot_interval_seconds=3600,
    )

    before = db.get_catalog_symbols()
    result = logger.record_universe_snapshot(
        now=7200.0,
    )
    after = db.get_catalog_symbols()

    assert result == {
        "persisted": True,
        "skipped": False,
        "rows": 4,
    }
    assert before == after

    rows = {
        row["symbol"]: row
        for row in read_rows(
            db_path,
            SOURCE_CATALOGUE_SNAPSHOT,
        )
    }
    assert (
        rows["FALSEPOS/USDT:USDT"]["disagreement_kind"]
        == VOLUME_PASS_EXECUTION_REJECT
    )
    assert (
        rows["FALSENEG/USDT:USDT"]["disagreement_kind"]
        == VOLUME_REJECT_EXECUTION_ACCEPT
    )
    assert rows["FALSENEG/USDT:USDT"]["scan_eligible"] == 0
    assert (
        rows["UNKNOWN/USDT:USDT"]["disagreement_kind"]
        == COMPARISON_UNKNOWN
    )
    assert all(
        row["trade_eligible"] is None
        for row in rows.values()
    )


def test_catalogue_snapshot_is_throttled_per_bucket(
    tmp_path,
):
    db_path = _db_path(tmp_path)
    db = DBAdapter(db_path)
    db.update_candidates(
        {
            "ONE/USDT:USDT": candidate(
                scan_eligible=True,
                volume=3_000_000.0,
            )
        }
    )
    enricher = FakeEnricher(
        {
            "ONE/USDT:USDT": {
                "status": "SUITABLE",
                "evidence_status": "SUFFICIENT",
            }
        }
    )
    logger = LBankExecutionDecisionLogger(
        db_path,
        enricher=enricher,
        snapshot_interval_seconds=3600,
    )

    first = logger.record_universe_snapshot(now=7200.0)
    second = logger.record_universe_snapshot(now=7210.0)

    assert first["rows"] == 1
    assert second == {
        "persisted": True,
        "skipped": True,
        "rows": 0,
    }
    assert enricher.calls == ["ONE/USDT:USDT"]
