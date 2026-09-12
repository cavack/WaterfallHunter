import sqlite3

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core.contracts import SignalClass
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.lbank_signal_ledger import LBankSignalLedger
from waterfallhunter.core.signal_metadata import (
    ClassificationMethod,
    EXPERIMENTAL_STRATEGY_PROFILE,
    METADATA_CONTRACT_VERSION,
    MODEL_GENERATION,
    STRICT_STRATEGY_PROFILE,
    SignalMetadataInput,
    build_signal_metadata_input,
)


SYMBOL = "METADATA/USDT:USDT"


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
        },
    }


def _execution() -> dict:
    return {
        "symbol": SYMBOL,
        "status": "SUITABLE",
        "evidence_status": "SUFFICIENT",
        "failed_checks": [],
    }


def _metadata(signal_class: SignalClass) -> SignalMetadataInput:
    strategy_profile = (
        STRICT_STRATEGY_PROFILE
        if signal_class is SignalClass.STRICT
        else EXPERIMENTAL_STRATEGY_PROFILE
    )
    score_version = (
        "score_v2"
        if signal_class is SignalClass.STRICT
        else "score_v2_watch_v1"
    )
    return SignalMetadataInput(
        signal_class=signal_class,
        strategy_profile=strategy_profile,
        score_version=score_version,
        model_generation=MODEL_GENERATION,
        decision_contract_hash="a" * 64,
        analysis_observed_at=1_700_000_000,
        reference_observed_at=1_699_999_990,
        metadata_contract_version=METADATA_CONTRACT_VERSION,
        classification_method=ClassificationMethod.FUTURE_PIPELINE_EXPLICIT,
        classification_evidence_hash=None,
    )


def _producer_metrics(
    *,
    strategy_profile: str,
    score_version: str,
) -> dict:
    return {
        "strategy_profile": strategy_profile,
        "score_version": score_version,
        "analysis_observed_at": 1_700_000_000,
        "reference_observed_at": 1_699_999_990,
    }


def _armed_db(tmp_path):
    db_path = migrate_test_database(tmp_path / "signal-metadata.db")
    db = DBAdapter(db_path=str(db_path))
    db.update_candidates({SYMBOL: _ready_candidate()})
    assert db.update_candidate_state(SYMBOL, "ARMED")
    return db


def _persist(
    ledger: LBankSignalLedger,
    metadata: SignalMetadataInput,
    *,
    expected_state: str = "ARMED",
) -> int | None:
    return ledger.persist_trigger(
        SYMBOL,
        expected_state,
        score=91.5,
        trigger_metrics=_metrics(),
        execution_suitability=_execution(),
        triggered_at=1_700_000_010,
        metadata=metadata,
        metadata_created_at=1_700_000_020,
    )


def _counts(db_path: str) -> tuple[int, int]:
    with sqlite3.connect(db_path) as conn:
        ledger_count = conn.execute(
            "SELECT COUNT(*) FROM lbank_signal_ledger"
        ).fetchone()[0]
        metadata_count = conn.execute(
            "SELECT COUNT(*) FROM signal_metadata"
        ).fetchone()[0]
    return int(ledger_count), int(metadata_count)


def test_future_metadata_producer_builds_explicit_strict_lineage() -> None:
    metadata = build_signal_metadata_input(
        _producer_metrics(
            strategy_profile=STRICT_STRATEGY_PROFILE,
            score_version="score_v2",
        ),
        "b" * 64,
    )

    assert metadata.signal_class is SignalClass.STRICT
    assert metadata.strategy_profile == STRICT_STRATEGY_PROFILE
    assert metadata.score_version == "score_v2"
    assert metadata.analysis_observed_at == 1_700_000_000
    assert metadata.reference_observed_at == 1_699_999_990
    assert metadata.decision_contract_hash == "b" * 64
    assert metadata.model_generation == MODEL_GENERATION
    assert metadata.classification_method is ClassificationMethod.FUTURE_PIPELINE_EXPLICIT
    assert metadata.classification_evidence_hash is None


def test_future_metadata_producer_builds_only_exact_experimental_lineage() -> None:
    metadata = build_signal_metadata_input(
        _producer_metrics(
            strategy_profile=EXPERIMENTAL_STRATEGY_PROFILE,
            score_version="score_v2_watch_v1",
        ),
        "c" * 64,
    )

    assert metadata.signal_class is SignalClass.EXPERIMENTAL
    assert metadata.strategy_profile == EXPERIMENTAL_STRATEGY_PROFILE
    assert metadata.score_version == "score_v2_watch_v1"


@pytest.mark.parametrize(
    "metrics",
    [
        {
            "score_version": "score_v2",
            "analysis_observed_at": 1_700_000_000,
        },
        _producer_metrics(
            strategy_profile="unknown_profile",
            score_version="score_v2",
        ),
        _producer_metrics(
            strategy_profile=STRICT_STRATEGY_PROFILE,
            score_version="score_v2_watch_v1",
        ),
        _producer_metrics(
            strategy_profile=EXPERIMENTAL_STRATEGY_PROFILE,
            score_version="score_v2",
        ),
    ],
)
def test_future_metadata_producer_rejects_missing_unknown_or_mismatched_lineage(
    metrics: dict,
) -> None:
    with pytest.raises(ValueError):
        build_signal_metadata_input(metrics, "d" * 64)


def test_signal_metadata_input_rejects_noncanonical_score_version() -> None:
    with pytest.raises(ValueError):
        SignalMetadataInput(
            signal_class=SignalClass.EXPERIMENTAL,
            strategy_profile=EXPERIMENTAL_STRATEGY_PROFILE,
            score_version="score_v2",
            model_generation=MODEL_GENERATION,
            decision_contract_hash="f" * 64,
            analysis_observed_at=1_700_000_000,
            reference_observed_at=1_699_999_990,
            metadata_contract_version=METADATA_CONTRACT_VERSION,
            classification_method=ClassificationMethod.FUTURE_PIPELINE_EXPLICIT,
            classification_evidence_hash=None,
        )


def test_future_metadata_producer_requires_actual_analysis_observation_time() -> None:
    metrics = _producer_metrics(
        strategy_profile=STRICT_STRATEGY_PROFILE,
        score_version="score_v2",
    )
    metrics.pop("analysis_observed_at")

    with pytest.raises(ValueError):
        build_signal_metadata_input(metrics, "e" * 64)


def test_strict_signal_persists_metadata_with_ledger_atomically(tmp_path) -> None:
    db = _armed_db(tmp_path)
    ledger = LBankSignalLedger(db.db_path)

    signal_id = _persist(ledger, _metadata(SignalClass.STRICT))

    assert signal_id == 1
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(
            """
            SELECT
                signal_id,
                signal_class,
                strategy_profile,
                score_version,
                model_generation,
                decision_contract_hash,
                analysis_observed_at,
                reference_observed_at,
                metadata_contract_version,
                classification_method,
                classification_evidence_hash,
                created_at
            FROM signal_metadata
            WHERE signal_id = ?
            """,
            (signal_id,),
        ).fetchone()

    assert row is not None, f"No metadata row found for signal_id {signal_id}"
    assert row == (
        1,
        "STRICT",
        STRICT_STRATEGY_PROFILE,
        "score_v2",
        MODEL_GENERATION,
        "a" * 64,
        1_700_000_000,
        1_699_999_990,
        METADATA_CONTRACT_VERSION,
        "FUTURE_PIPELINE_EXPLICIT",
        None,
        1_700_000_020,
    )


def test_experimental_signal_persists_explicit_experimental_lineage(tmp_path) -> None:
    db = _armed_db(tmp_path)
    ledger = LBankSignalLedger(db.db_path)

    signal_id = _persist(ledger, _metadata(SignalClass.EXPERIMENTAL))

    assert signal_id == 1
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(
            "SELECT signal_class, strategy_profile, score_version FROM signal_metadata"
        ).fetchone()
    assert row is not None, "No metadata row found"
    assert row == (
        "EXPERIMENTAL",
        EXPERIMENTAL_STRATEGY_PROFILE,
        "score_v2_watch_v1",
    )


@pytest.mark.parametrize(
    "metadata_created_at",
    [True, "1700000020", 1_700_000_020.5],
)
def test_metadata_created_at_rejects_non_integer_values_without_mutation(
    tmp_path,
    metadata_created_at,
) -> None:
    db = _armed_db(tmp_path)
    ledger = LBankSignalLedger(db.db_path)

    signal_id = ledger.persist_trigger(
        SYMBOL,
        "ARMED",
        score=91.5,
        trigger_metrics=_metrics(),
        execution_suitability=_execution(),
        triggered_at=1_700_000_010,
        metadata=_metadata(SignalClass.STRICT),
        metadata_created_at=metadata_created_at,
    )

    assert signal_id is None
    assert _counts(db.db_path) == (0, 0)
    with sqlite3.connect(db.db_path) as conn:
        status_row = conn.execute(
            "SELECT status FROM lbank_catalog WHERE symbol = ?",
            (SYMBOL,),
        ).fetchone()
    assert status_row is not None
    assert status_row[0] == "ARMED"


def test_metadata_insert_failure_rolls_back_catalogue_ledger_and_metadata(
    tmp_path,
) -> None:
    db = _armed_db(tmp_path)
    ledger = LBankSignalLedger(db.db_path)
    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_test_metadata
            BEFORE INSERT ON signal_metadata
            BEGIN
                SELECT RAISE(ABORT, 'injected metadata insert failure');
            END
            """
        )

    assert _persist(ledger, _metadata(SignalClass.STRICT)) is None

    with sqlite3.connect(db.db_path) as conn:
        status_row = conn.execute(
            "SELECT status FROM lbank_catalog WHERE symbol = ?",
            (SYMBOL,),
        ).fetchone()
    assert status_row is not None, f"No catalog row found for {SYMBOL}"
    status = status_row[0]
    assert status == "ARMED"
    assert _counts(db.db_path) == (0, 0)


def test_stale_catalogue_cas_inserts_neither_ledger_nor_metadata(tmp_path) -> None:
    db = _armed_db(tmp_path)
    ledger = LBankSignalLedger(db.db_path)

    assert _persist(
        ledger,
        _metadata(SignalClass.STRICT),
        expected_state="PRE-TRIGGER",
    ) is None

    assert _counts(db.db_path) == (0, 0)
