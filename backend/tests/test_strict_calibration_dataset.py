from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from schema_test_support import migrate_test_database
from signal_metadata_test_support import strict_signal_metadata
from waterfallhunter.core.contracts import SignalClass
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.lbank_signal_ledger import LBankSignalLedger
from waterfallhunter.core.signal_metadata import (
    ClassificationMethod,
    EXPERIMENTAL_SCORE_VERSION,
    EXPERIMENTAL_STRATEGY_PROFILE,
    METADATA_CONTRACT_VERSION,
    MODEL_GENERATION,
    SignalMetadataInput,
)
from waterfallhunter.core.strict_calibration_dataset import (
    StrictCalibrationDatasetBuilder,
    StrictCalibrationDatasetError,
)


def _metadata(signal_class: SignalClass, observed_at: int) -> SignalMetadataInput:
    if signal_class is SignalClass.STRICT:
        return strict_signal_metadata(
            analysis_observed_at=observed_at,
            reference_observed_at=observed_at,
        )
    return SignalMetadataInput(
        signal_class=SignalClass.EXPERIMENTAL,
        strategy_profile=EXPERIMENTAL_STRATEGY_PROFILE,
        score_version=EXPERIMENTAL_SCORE_VERSION,
        model_generation=MODEL_GENERATION,
        decision_contract_hash="b" * 64,
        analysis_observed_at=observed_at,
        reference_observed_at=observed_at,
        metadata_contract_version=METADATA_CONTRACT_VERSION,
        classification_method=ClassificationMethod.FUTURE_PIPELINE_EXPLICIT,
        classification_evidence_hash=None,
    )


def _signal(
    db: DBAdapter,
    *,
    symbol: str,
    triggered_at: int,
    signal_class: SignalClass,
) -> int:
    db.update_candidates(
        {
            symbol: {
                "last_price": 1.0,
                "quote_volume": 3_000_000.0,
                "is_meme": False,
                "scan_eligible": True,
            }
        }
    )
    assert db.update_candidate_state(symbol, "ARMED")
    signal_id = LBankSignalLedger(db.db_path).persist_trigger(
        symbol,
        "ARMED",
        score=80.0,
        trigger_metrics={
            "position_setup": {
                "entry_price": 1.0,
                "stop_loss": 1.05,
                "take_profit_1": 0.95,
                "take_profit_2": 0.90,
            }
        },
        execution_suitability={"status": "SUITABLE", "failed_checks": []},
        metadata=_metadata(signal_class, triggered_at - 1),
        metadata_created_at=triggered_at,
        triggered_at=triggered_at,
    )
    assert signal_id is not None
    return signal_id


def _outcome(
    db_path: str,
    *,
    signal_id: int,
    symbol: str,
    triggered_at: int,
    resolved_at: int,
    complete: bool = True,
) -> None:
    observed = 1_440 if complete else 1_439
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO lbank_signal_outcomes (
                signal_id, symbol, outcome_status, signal_triggered_at,
                observation_started_at, observation_ended_at, horizon_seconds,
                price_source, first_tp2_at, observed_candles, expected_candles,
                details_json, observational_only, trade_eligible, resolved_at
            ) VALUES (?, ?, 'TP2_FIRST', ?, ?, ?, 86400, 'test', ?, ?, 1440,
                      '{}', 1, NULL, ?)
            """,
            (
                signal_id,
                symbol,
                triggered_at,
                triggered_at,
                resolved_at,
                triggered_at + 60,
                observed,
                resolved_at,
            ),
        )


def _database(path: Path) -> DBAdapter:
    return DBAdapter(db_path=str(migrate_test_database(path)))


def test_builder_is_strict_only_with_exact_half_open_signal_boundaries(tmp_path: Path) -> None:
    db = _database(tmp_path / "calibration.db")
    included = _signal(
        db, symbol="STRICT-IN/USDT:USDT", triggered_at=100, signal_class=SignalClass.STRICT
    )
    experimental = _signal(
        db, symbol="EXP/USDT:USDT", triggered_at=120, signal_class=SignalClass.EXPERIMENTAL
    )
    at_end = _signal(
        db, symbol="STRICT-END/USDT:USDT", triggered_at=200, signal_class=SignalClass.STRICT
    )
    for signal_id, symbol, triggered_at in (
        (included, "STRICT-IN/USDT:USDT", 100),
        (experimental, "EXP/USDT:USDT", 120),
        (at_end, "STRICT-END/USDT:USDT", 200),
    ):
        _outcome(
            db.db_path,
            signal_id=signal_id,
            symbol=symbol,
            triggered_at=triggered_at,
            resolved_at=250,
        )

    dataset = StrictCalibrationDatasetBuilder(db.db_path).build(
        signal_window_start=100,
        signal_window_end=200,
        outcome_as_of=300,
        generated_at=301,
        source_revision="test-revision",
    )

    assert [row["signal_id"] for row in dataset.rows] == [included]
    assert dataset.manifest["signal_window"]["boundary"] == "[start,end)"
    assert dataset.manifest["cohort"]["signal_class"] == "STRICT"


def test_builder_excludes_incomplete_or_not_yet_observable_outcomes(tmp_path: Path) -> None:
    db = _database(tmp_path / "no-lookahead.db")
    incomplete = _signal(
        db, symbol="INCOMPLETE/USDT:USDT", triggered_at=110, signal_class=SignalClass.STRICT
    )
    future = _signal(
        db, symbol="FUTURE/USDT:USDT", triggered_at=120, signal_class=SignalClass.STRICT
    )
    _outcome(
        db.db_path,
        signal_id=incomplete,
        symbol="INCOMPLETE/USDT:USDT",
        triggered_at=110,
        resolved_at=250,
        complete=False,
    )
    _outcome(
        db.db_path,
        signal_id=future,
        symbol="FUTURE/USDT:USDT",
        triggered_at=120,
        resolved_at=400,
    )

    dataset = StrictCalibrationDatasetBuilder(db.db_path).build(
        signal_window_start=100,
        signal_window_end=200,
        outcome_as_of=300,
        generated_at=301,
        source_revision="test-revision",
    )

    assert dataset.rows == ()
    assert dataset.manifest["row_count"] == 0


def test_manifest_identity_is_deterministic_and_clock_is_explicit(tmp_path: Path) -> None:
    db = _database(tmp_path / "identity.db")
    builder = StrictCalibrationDatasetBuilder(db.db_path)
    arguments = {
        "signal_window_start": 100,
        "signal_window_end": 200,
        "outcome_as_of": 300,
        "generated_at": 301,
        "source_revision": "test-revision",
    }

    first = builder.build(**arguments)
    second = builder.build(**arguments)

    assert first == second
    assert len(first.manifest["dataset_manifest_sha256"]) == 64
    assert len(first.manifest["dataset_rows_sha256"]) == 64
    with pytest.raises(StrictCalibrationDatasetError, match="start < end"):
        builder.build(**{**arguments, "signal_window_end": 100})
