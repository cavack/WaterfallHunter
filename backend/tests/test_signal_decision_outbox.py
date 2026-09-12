from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core.contracts import SignalClass
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.lbank_signal_ledger import LBankSignalLedger
from waterfallhunter.core.schema_contract import (
    CURRENT_RUNTIME_SCHEMA_VERSION,
    verify_managed_schema,
)
from waterfallhunter.core.signal_metadata import (
    ClassificationMethod,
    METADATA_CONTRACT_VERSION,
    MODEL_GENERATION,
    STRICT_STRATEGY_PROFILE,
    SignalMetadataInput,
    canonical_sha256,
)


SYMBOL = "OUTBOX/USDT:USDT"


def _metadata() -> SignalMetadataInput:
    return SignalMetadataInput(
        signal_class=SignalClass.STRICT,
        strategy_profile=STRICT_STRATEGY_PROFILE,
        score_version="score_v2",
        model_generation=MODEL_GENERATION,
        decision_contract_hash="a" * 64,
        analysis_observed_at=1_700_000_000,
        reference_observed_at=1_699_999_990,
        metadata_contract_version=METADATA_CONTRACT_VERSION,
        classification_method=ClassificationMethod.FUTURE_PIPELINE_EXPLICIT,
        classification_evidence_hash=None,
    )


def _armed_database(path: Path) -> DBAdapter:
    db_path = migrate_test_database(path)
    db = DBAdapter(db_path=str(db_path))
    db.update_candidates(
        {
            SYMBOL: {
                "last_price": 1.0,
                "quote_volume": 3_000_000.0,
                "is_meme": False,
                "scan_eligible": True,
            }
        }
    )
    assert db.update_candidate_state(SYMBOL, "ARMED")
    return db


def _persist(ledger: LBankSignalLedger) -> int | None:
    return ledger.persist_trigger(
        SYMBOL,
        "ARMED",
        score=91.5,
        trigger_metrics={
            "position_setup": {
                "entry_price": 1.0,
                "stop_loss": 1.05,
                "take_profit_1": 0.95,
                "take_profit_2": 0.90,
            }
        },
        execution_suitability={
            "status": "SUITABLE",
            "failed_checks": [],
        },
        metadata=_metadata(),
        metadata_created_at=1_700_000_020,
        triggered_at=1_700_000_010,
    )


def _table_counts(conn: sqlite3.Connection) -> tuple[int, int, int, int]:
    return (
        int(conn.execute("SELECT COUNT(*) FROM lbank_signal_ledger").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM signal_metadata").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM signal_decisions").fetchone()[0]),
        int(conn.execute("SELECT COUNT(*) FROM domain_outbox_events").fetchone()[0]),
    )


def test_migration_v4_creates_verified_decision_and_outbox_contract(
    tmp_path: Path,
) -> None:
    db_path = migrate_test_database(tmp_path / "outbox-schema.db")

    result = verify_managed_schema(
        db_path,
        check_user_version=CURRENT_RUNTIME_SCHEMA_VERSION,
    )

    assert CURRENT_RUNTIME_SCHEMA_VERSION == 10
    assert result.valid is True
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (10,)
        objects = {
            (str(row[0]), str(row[1]))
            for row in conn.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE name IN ('signal_decisions', 'domain_outbox_events')"
            )
        }
    assert objects == {
        ("table", "signal_decisions"),
        ("table", "domain_outbox_events"),
    }


def test_signal_transaction_persists_decision_and_pending_outbox_exactly_once(
    tmp_path: Path,
) -> None:
    db = _armed_database(tmp_path / "atomic-outbox.db")

    signal_id = _persist(LBankSignalLedger(db.db_path))

    assert signal_id == 1
    with sqlite3.connect(db.db_path) as conn:
        assert _table_counts(conn) == (1, 1, 1, 1)
        decision = conn.execute(
            "SELECT decision_id, calibrated_probability, payload_json, payload_hash "
            "FROM signal_decisions WHERE signal_id = 1"
        ).fetchone()
        event = conn.execute(
            "SELECT event_id, event_key, status, attempt_count, payload_json, payload_hash "
            "FROM domain_outbox_events WHERE signal_id = 1"
        ).fetchone()

    assert decision is not None
    decision_payload = json.loads(str(decision[2]))
    assert decision[:2] == ("signal:1:decision:1", None)
    assert decision_payload["predictive_evidence_score"] == 91.5
    assert decision_payload["calibrated_probability"] is None
    assert decision_payload["execution_mode"] == "SIGNAL_ONLY"
    assert decision[3] == canonical_sha256(decision_payload)

    assert event is not None
    event_payload = json.loads(str(event[4]))
    assert event[:4] == (
        "signal:1:confirmed:1",
        "signal:1:confirmed:1",
        "PENDING",
        0,
    )
    assert event_payload["decision_payload_hash"] == decision[3]
    assert event[5] == canonical_sha256(event_payload)


def test_outbox_insert_failure_rolls_back_catalog_ledger_metadata_and_decision(
    tmp_path: Path,
) -> None:
    db = _armed_database(tmp_path / "outbox-failure.db")
    with sqlite3.connect(db.db_path) as conn:
        conn.executescript(
            """
            CREATE TRIGGER reject_test_outbox
            BEFORE INSERT ON domain_outbox_events
            BEGIN
                SELECT RAISE(ABORT, 'injected outbox failure');
            END;
            """
        )

    assert _persist(LBankSignalLedger(db.db_path)) is None

    with sqlite3.connect(db.db_path) as conn:
        assert _table_counts(conn) == (0, 0, 0, 0)
        assert conn.execute(
            "SELECT status FROM lbank_catalog WHERE symbol = ?",
            (SYMBOL,),
        ).fetchone() == ("ARMED",)


def test_decision_and_outbox_material_are_immutable(
    tmp_path: Path,
) -> None:
    db = _armed_database(tmp_path / "immutability.db")
    assert _persist(LBankSignalLedger(db.db_path)) == 1

    with sqlite3.connect(db.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE signal_decisions SET decision_status='INVALIDATED' "
                "WHERE signal_id=1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
            conn.execute("DELETE FROM domain_outbox_events WHERE signal_id=1")
        with pytest.raises(sqlite3.IntegrityError, match="material is immutable"):
            conn.execute(
                "UPDATE domain_outbox_events SET payload_json='{}' WHERE signal_id=1"
            )

        conn.execute(
            "UPDATE domain_outbox_events "
            "SET status='SENDING', attempt_count=1, updated_at=1700000021 "
            "WHERE signal_id=1"
        )
        assert conn.execute(
            "SELECT status, attempt_count FROM domain_outbox_events WHERE signal_id=1"
        ).fetchone() == ("SENDING", 1)
