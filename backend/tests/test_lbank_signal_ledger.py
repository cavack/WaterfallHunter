import json
import sqlite3

import pytest

from schema_test_support import (
    build_legacy_runtime_database,
    business_row_hashes,
    migrate_test_database,
)
from waterfallhunter.core.contracts import SignalClass
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.lbank_signal_ledger import (
    LBankSignalLedger,
)
from waterfallhunter.core.signal_metadata import (
    ClassificationMethod,
    METADATA_CONTRACT_VERSION,
    MODEL_GENERATION,
    STRICT_STRATEGY_PROFILE,
    SignalMetadataInput,
)


SYMBOL = "LEDGER/USDT:USDT"


def _ready_candidate() -> dict:
    return {
        "last_price": 0.01,
        "quote_volume": 3_000_000.0,
        "is_meme": False,
        "scan_eligible": True,
    }


def _metrics() -> dict:
    return {
        "exchange": "lbank",
        "mapped_symbol": SYMBOL,
        "position_setup": {
            "status": "READY",
            "entry_price": 0.0101,
            "stop_loss": 0.0105,
            "take_profit_1": 0.0097,
            "take_profit_2": 0.0093,
            "position_size_contracts": 100.0,
        },
    }


def _execution() -> dict:
    return {
        "symbol": SYMBOL,
        "status": "MARGINAL",
        "evidence_status": "SUFFICIENT",
        "observed_samples": 96,
        "observation_span_hours": 24.0,
        "availability_rate": 0.98,
        "cost_100_p90_pct": 0.2,
        "spread_p90_pct": 0.15,
        "depth_25bps_p50_usdt": 2_000.0,
        "failed_checks": ["suitable_cost_100_p90_pct"],
        "observational_only": True,
        "trade_eligible": None,
    }


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


def _armed_db(tmp_path):
    db_path = tmp_path / "signal.db"
    migrate_test_database(db_path)
    db = DBAdapter(
        db_path=str(db_path)
    )
    db.update_candidates(
        {SYMBOL: _ready_candidate()}
    )
    assert db.update_candidate_state(
        SYMBOL,
        "ARMED",
    )
    return db


def _catalogue_row(db):
    with sqlite3.connect(db.db_path) as conn:
        return conn.execute(
            """
            SELECT status, trigger_data
            FROM lbank_catalog
            WHERE symbol = ?
            """,
            (SYMBOL,),
        ).fetchone()


def test_trigger_transition_and_signal_snapshot_are_atomic(
    tmp_path,
):
    db = _armed_db(tmp_path)
    ledger = LBankSignalLedger(db.db_path)

    signal_id = ledger.persist_trigger(
        SYMBOL,
        "ARMED",
        score=91.5,
        trigger_metrics=_metrics(),
        execution_suitability=_execution(),
        metadata=_metadata(),
        quote_volume=3_000_000.0,
        volume_gate_passed=True,
        proxy_execution_disagreement="AGREE_ACCEPT",
        triggered_at=1_700_000_000,
    )

    assert signal_id == 1
    status, trigger_data = _catalogue_row(db)
    assert status == "TRIGGERED"
    assert json.loads(trigger_data) == _metrics()

    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(
            """
            SELECT
                symbol,
                triggered_at,
                state_before,
                score,
                entry_price,
                stop_loss,
                take_profit_1,
                take_profit_2,
                execution_status,
                execution_evidence_status,
                execution_observed_samples,
                execution_failed_checks_json,
                execution_suitability_json,
                quote_volume_at_trigger,
                volume_gate_passed,
                proxy_execution_disagreement,
                observational_only,
                trade_eligible
            FROM lbank_signal_ledger
            WHERE id = ?
            """,
            (signal_id,),
        ).fetchone()

    assert row[:4] == (
        SYMBOL,
        1_700_000_000,
        "ARMED",
        91.5,
    )
    assert row[4:8] == (
        0.0101,
        0.0105,
        0.0097,
        0.0093,
    )
    assert row[8:11] == (
        "MARGINAL",
        "SUFFICIENT",
        96,
    )
    assert json.loads(row[11]) == [
        "suitable_cost_100_p90_pct"
    ]
    assert json.loads(row[12]) == _execution()
    assert row[13:16] == (
        3_000_000.0,
        1,
        "AGREE_ACCEPT",
    )
    assert row[16:] == (1, None)


def test_legacy_ledger_verification_is_read_only_and_explicit_migration_preserves_rows(
    tmp_path,
):
    db_path = build_legacy_runtime_database(tmp_path / "legacy.db")
    before = business_row_hashes(db_path, ("lbank_signal_ledger",))

    LBankSignalLedger(str(db_path))
    after_verify = business_row_hashes(db_path, ("lbank_signal_ledger",))
    assert after_verify == before

    migrate_test_database(db_path)
    LBankSignalLedger(str(db_path))

    after_migration = business_row_hashes(db_path, ("lbank_signal_ledger",))
    assert after_migration == before


@pytest.mark.parametrize(
    "expected_state,make_ineligible",
    [
        ("PRE-TRIGGER", False),
        ("ARMED", True),
    ],
)
def test_stale_or_ineligible_transition_appends_no_signal(
    tmp_path,
    expected_state,
    make_ineligible,
):
    db = _armed_db(tmp_path)
    ledger = LBankSignalLedger(db.db_path)

    if make_ineligible:
        db.update_candidates(
            {
                SYMBOL: {
                    **_ready_candidate(),
                    "scan_eligible": False,
                }
            }
        )

    assert ledger.persist_trigger(
        SYMBOL,
        expected_state,
        score=91.5,
        trigger_metrics=_metrics(),
        execution_suitability=_execution(),
        metadata=_metadata(),
    ) is None

    with sqlite3.connect(db.db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM lbank_signal_ledger"
        ).fetchone()[0]

    assert count == 0
    assert _catalogue_row(db)[0] != "TRIGGERED"


def test_ledger_insert_failure_rolls_back_trigger_transition(
    tmp_path,
):
    db = _armed_db(tmp_path)
    ledger = LBankSignalLedger(db.db_path)

    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_test_signal
            BEFORE INSERT ON lbank_signal_ledger
            BEGIN
                SELECT RAISE(ABORT, 'injected insert failure');
            END
            """
        )

    assert ledger.persist_trigger(
        SYMBOL,
        "ARMED",
        score=91.5,
        trigger_metrics=_metrics(),
        execution_suitability=_execution(),
        metadata=_metadata(),
    ) is None

    assert _catalogue_row(db) == ("ARMED", "{}")


def test_signal_rows_cannot_be_updated_or_deleted(
    tmp_path,
):
    db = _armed_db(tmp_path)
    ledger = LBankSignalLedger(db.db_path)
    signal_id = ledger.persist_trigger(
        SYMBOL,
        "ARMED",
        score=91.5,
        trigger_metrics=_metrics(),
        execution_suitability=_execution(),
        metadata=_metadata(),
    )

    with sqlite3.connect(db.db_path) as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="immutable",
        ):
            conn.execute(
                """
                UPDATE lbank_signal_ledger
                SET score = 1
                WHERE id = ?
                """,
                (signal_id,),
            )

        with pytest.raises(
            sqlite3.IntegrityError,
            match="immutable",
        ):
            conn.execute(
                """
                DELETE FROM lbank_signal_ledger
                WHERE id = ?
                """,
                (signal_id,),
            )
