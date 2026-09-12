import asyncio
import json
import sqlite3

import pytest

from schema_test_support import migrate_test_database
from signal_metadata_test_support import strict_signal_metadata
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.lbank_signal_ledger import LBankSignalLedger
from waterfallhunter.core.lbank_signal_outcome import (
    LBankSignalOutcomeEvaluator,
    LBankSignalOutcomeStore,
    LBankSignalSettlementWorker,
    MINUTE_MS,
)


SYMBOL = "OUTCOME/USDT:USDT"


def _signal(triggered_at=1_700_000_010):
    return {
        "id": 1,
        "symbol": SYMBOL,
        "triggered_at": triggered_at,
        "entry_price": 100.0,
        "stop_loss": 102.0,
        "take_profit_1": 98.0,
        "take_profit_2": 96.0,
        "trigger_metrics_json": json.dumps(
            {
                "exchange": "binance",
                "mapped_symbol": SYMBOL,
            }
        ),
    }


def _candles(signal, horizon_seconds=86_400):
    trigger_ms = signal["triggered_at"] * 1000
    first = trigger_ms // MINUTE_MS * MINUTE_MS
    start = (
        trigger_ms
        if trigger_ms % MINUTE_MS == 0
        else first + MINUTE_MS
    )
    end = start + horizon_seconds * 1000
    return [
        [timestamp, 100.0, 100.5, 99.5, 100.0, 1.0]
        for timestamp in range(first, end, MINUTE_MS)
    ]


def _replace(candles, timestamp, *, high, low):
    index = next(
        index
        for index, row in enumerate(candles)
        if row[0] == timestamp
    )
    candles[index] = [timestamp, 100.0, high, low, 100.0, 1.0]


def _observation_start(signal):
    trigger_ms = signal["triggered_at"] * 1000
    return (
        trigger_ms
        if trigger_ms % MINUTE_MS == 0
        else (trigger_ms // MINUTE_MS + 1) * MINUTE_MS
    )


def test_no_level_hit_uses_exact_full_observation_window():
    signal = _signal()
    outcome = LBankSignalOutcomeEvaluator.evaluate(
        signal,
        _candles(signal),
    )

    assert outcome["status"] == "NO_LEVEL_HIT_24H"
    assert outcome["observed_candles"] == 1440
    assert outcome["expected_candles"] == 1440
    assert outcome["observation_ended_at"] - outcome[
        "observation_started_at"
    ] == 86_400


def test_trigger_minute_touch_is_not_guessed():
    signal = _signal()
    candles = _candles(signal)
    _replace(
        candles,
        candles[0][0],
        high=100.5,
        low=97.0,
    )

    outcome = LBankSignalOutcomeEvaluator.evaluate(
        signal,
        candles,
    )

    assert outcome["status"] == "UNRESOLVABLE_TRIGGER_MINUTE"


def test_same_candle_stop_and_target_is_ambiguous():
    signal = _signal()
    candles = _candles(signal)
    timestamp = _observation_start(signal) + 5 * MINUTE_MS
    _replace(
        candles,
        timestamp,
        high=103.0,
        low=95.0,
    )

    outcome = LBankSignalOutcomeEvaluator.evaluate(
        signal,
        candles,
    )

    assert outcome["status"] == "AMBIGUOUS_INTRACANDLE_PATH"
    assert outcome["first_stop_at"] == timestamp // 1000
    assert outcome["first_tp2_at"] == timestamp // 1000


def test_tp1_then_later_stop_preserves_order():
    signal = _signal()
    candles = _candles(signal)
    start = _observation_start(signal)
    _replace(
        candles,
        start + 5 * MINUTE_MS,
        high=100.5,
        low=97.0,
    )
    _replace(
        candles,
        start + 8 * MINUTE_MS,
        high=103.0,
        low=99.0,
    )

    outcome = LBankSignalOutcomeEvaluator.evaluate(
        signal,
        candles,
    )

    assert outcome["status"] == "TP1_THEN_STOP"
    assert outcome["first_tp1_at"] < outcome["first_stop_at"]


def test_missing_candle_is_recorded_as_incomplete_data():
    signal = _signal()
    candles = _candles(signal)
    del candles[100]

    outcome = LBankSignalOutcomeEvaluator.evaluate(
        signal,
        candles,
    )

    assert outcome["status"] == "DATA_INCOMPLETE"
    assert outcome["details"]["missing_candles"] == 1


def _persist_signal(tmp_path, *, triggered_at=1_000):
    db_path = tmp_path / "outcomes.db"
    migrate_test_database(db_path)
    db = DBAdapter(str(db_path))
    db.update_candidates(
        {
            SYMBOL: {
                "last_price": 100.0,
                "quote_volume": 3_000_000.0,
                "is_meme": False,
                "scan_eligible": True,
            }
        }
    )
    assert db.update_candidate_state(SYMBOL, "ARMED")
    ledger = LBankSignalLedger(db.db_path)
    signal_id = ledger.persist_trigger(
        SYMBOL,
        "ARMED",
        score=90.0,
        trigger_metrics={
            "exchange": "binance",
            "mapped_symbol": SYMBOL,
            "position_setup": {
                "entry_price": 100.0,
                "stop_loss": 102.0,
                "take_profit_1": 98.0,
                "take_profit_2": 96.0,
            },
        },
        execution_suitability={
            "status": "MARGINAL",
            "observational_only": True,
            "trade_eligible": None,
        },
        metadata=strict_signal_metadata(analysis_observed_at=triggered_at),
        triggered_at=triggered_at,
    )
    assert signal_id == 1
    return db


def test_outcome_rows_are_unique_observational_and_immutable(tmp_path):
    db = _persist_signal(tmp_path)
    store = LBankSignalOutcomeStore(db.db_path)
    signal = store.pending_signals(
        mature_before=1_000,
    )[0]
    outcome = LBankSignalOutcomeEvaluator.evaluate(
        signal,
        _candles(signal, horizon_seconds=120),
        horizon_seconds=120,
    )

    assert store.append_outcome(
        signal,
        outcome,
        source_exchange="binance",
        source_mapped_symbol=SYMBOL,
        resolved_at=2_000,
    )
    assert not store.append_outcome(
        signal,
        outcome,
        source_exchange="binance",
        source_mapped_symbol=SYMBOL,
        resolved_at=2_001,
    )

    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(
            """
            SELECT
                price_source,
                observational_only,
                trade_eligible
            FROM lbank_signal_outcomes
            """
        ).fetchone()
        assert row == (
            "closed_1m_trade_ohlcv_proxy",
            1,
            None,
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE lbank_signal_outcomes SET outcome_status='X'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "DELETE FROM lbank_signal_outcomes"
            )


def test_worker_only_fetches_mature_signal_and_appends_once(tmp_path):
    db = _persist_signal(tmp_path)
    store = LBankSignalOutcomeStore(db.db_path)
    fetched = []

    async def fetcher(signal, start_ms, end_ms):
        fetched.append((signal["id"], start_ms, end_ms))
        return _candles(signal, horizon_seconds=120)

    worker = LBankSignalSettlementWorker(
        store,
        fetcher,
        horizon_seconds=120,
        close_delay_seconds=60,
    )

    assert asyncio.run(worker.settle_once(now=1_200)) == 0
    assert fetched == []
    assert asyncio.run(worker.settle_once(now=1_241)) == 1
    assert len(fetched) == 1
    assert asyncio.run(worker.settle_once(now=1_300)) == 0
    assert len(fetched) == 1


def test_worker_health_snapshot_tracks_successful_cycle(monkeypatch, tmp_path):
    db_path = tmp_path / "health.db"
    migrate_test_database(db_path)
    store = LBankSignalOutcomeStore(str(db_path))

    async def fetcher(signal, start_ms, end_ms):
        return []

    worker = LBankSignalSettlementWorker(store, fetcher)

    async def stop_after_cycle(_seconds):
        worker.stop()

    monkeypatch.setattr(asyncio, "sleep", stop_after_cycle)

    asyncio.run(worker.run_forever(interval_seconds=60))
    health = worker.health_snapshot()

    assert health["running"] is False
    assert health["total_cycles"] == 1
    assert health["total_failures"] == 0
    assert health["last_started_at"] is not None
    assert health["last_progress_at"] is not None
    assert health["last_completed_at"] is not None
    assert health["last_error_at"] is None


def test_worker_health_snapshot_tracks_failed_cycle(monkeypatch, tmp_path):
    db_path = tmp_path / "failure.db"
    migrate_test_database(db_path)
    store = LBankSignalOutcomeStore(str(db_path))

    async def fetcher(signal, start_ms, end_ms):
        return []

    worker = LBankSignalSettlementWorker(store, fetcher)

    async def fail_cycle():
        raise RuntimeError("cycle failed")

    async def stop_after_cycle(_seconds):
        worker.stop()

    monkeypatch.setattr(worker, "settle_once", fail_cycle)
    monkeypatch.setattr(asyncio, "sleep", stop_after_cycle)

    asyncio.run(worker.run_forever(interval_seconds=60))
    health = worker.health_snapshot()

    assert health["running"] is False
    assert health["total_cycles"] == 1
    assert health["total_failures"] == 1
    assert health["last_completed_at"] is None
    assert health["last_error_at"] is not None
