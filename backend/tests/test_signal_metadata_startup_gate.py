from __future__ import annotations

import hashlib
import inspect
import sqlite3
from pathlib import Path

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core.signal_metadata_store import (
    SignalMetadataError,
    SignalMetadataStore,
    require_signal_metadata_completeness,
    verify_signal_metadata_completeness,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cutover_created_at(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT applied_at FROM schema_migrations WHERE version = 3"
    ).fetchone()
    assert row is not None
    return int(row[0])


def _insert_ledger_row(
    conn: sqlite3.Connection,
    *,
    symbol: str = "TEST/USDT:USDT",
    triggered_at: int = 1_700_000_000,
    created_at: int | None = None,
) -> int:
    effective_created_at = (
        _cutover_created_at(conn) + 1
        if created_at is None
        else int(created_at)
    )
    cursor = conn.execute(
        """
        INSERT INTO lbank_signal_ledger (
            symbol,
            triggered_at,
            state_before,
            score,
            position_setup_json,
            trigger_metrics_json,
            execution_status,
            execution_failed_checks_json,
            execution_suitability_json,
            observational_only,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            symbol,
            triggered_at,
            "PRE-TRIGGER",
            55.0,
            "{}",
            "{}",
            "SUITABLE",
            "[]",
            "{}",
            effective_created_at,
        ),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _insert_metadata(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
    signal_class: str = "STRICT",
    strategy_profile: str = "strict_score_v2",
    decision_contract_hash: str = "a" * 64,
) -> None:
    conn.execute(
        """
        INSERT INTO signal_metadata (
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signal_id,
            signal_class,
            strategy_profile,
            "score_v2",
            "waterfall_signal_model_v1",
            decision_contract_hash,
            1_700_000_000,
            1_699_999_990,
            "signal_metadata_v1",
            "FUTURE_PIPELINE_EXPLICIT",
            None,
            1_700_000_001,
        ),
    )


def _patch_startup_workers(monkeypatch, main):
    started: list[object] = []

    def fake_start_background_task(awaitable):
        started.append(awaitable)
        if inspect.iscoroutine(awaitable):
            awaitable.close()

    class FakeSettlementWorker:
        async def run_forever(self, *, interval_seconds: float = 900.0) -> None:
            return None

    monkeypatch.setattr(main, "_start_background_task", fake_start_background_task)
    monkeypatch.setattr(main, "_build_lbank_execution_shadow_worker", lambda: None)
    monkeypatch.setattr(main, "_build_signal_settlement_worker", FakeSettlementWorker)
    return started


def test_zero_signal_database_is_complete_and_read_only(tmp_path: Path) -> None:
    db_path = migrate_test_database(tmp_path / "empty.db")
    before = _sha256(db_path)

    result = verify_signal_metadata_completeness(db_path)

    assert result.complete is True
    assert result.ledger_count == 0
    assert result.metadata_count == 0
    assert result.canonical_count == 0
    assert result.missing_metadata_count == 0
    assert result.orphan_metadata_count == 0
    assert result.invalid_metadata_count == 0
    assert result.reason_codes == ()
    assert _sha256(db_path) == before
    assert require_signal_metadata_completeness(db_path) == result


def test_complete_canonical_database_passes_without_mutation(tmp_path: Path) -> None:
    db_path = migrate_test_database(tmp_path / "complete.db")
    with sqlite3.connect(db_path) as conn:
        signal_id = _insert_ledger_row(conn)
        _insert_metadata(conn, signal_id=signal_id)
    before = _sha256(db_path)

    result = SignalMetadataStore(db_path).verify_completeness()

    assert result.complete is True
    assert result.ledger_count == 1
    assert result.metadata_count == 1
    assert result.canonical_count == 1
    assert result.reason_codes == ()
    assert _sha256(db_path) == before


def test_pre_cutover_legacy_row_without_metadata_is_quarantined(
    tmp_path: Path,
) -> None:
    db_path = migrate_test_database(tmp_path / "legacy.db")
    with sqlite3.connect(db_path) as conn:
        cutover = _cutover_created_at(conn)
        _insert_ledger_row(conn, created_at=max(0, cutover - 1))
    before = _sha256(db_path)

    result = verify_signal_metadata_completeness(db_path)

    assert result.complete is True
    assert result.ledger_count == 1
    assert result.metadata_count == 0
    assert result.canonical_count == 0
    assert result.missing_metadata_count == 0
    assert result.reason_codes == ()
    assert _sha256(db_path) == before


def test_missing_post_cutover_metadata_fails_closed(tmp_path: Path) -> None:
    db_path = migrate_test_database(tmp_path / "missing-metadata.db")
    with sqlite3.connect(db_path) as conn:
        _insert_ledger_row(conn)
    before = _sha256(db_path)

    result = verify_signal_metadata_completeness(db_path)

    assert result.complete is False
    assert result.ledger_count == 1
    assert result.metadata_count == 0
    assert result.canonical_count == 0
    assert result.missing_metadata_count == 1
    assert "MISSING_METADATA" in result.reason_codes
    assert _sha256(db_path) == before
    with pytest.raises(SignalMetadataError, match="SIGNAL_METADATA_INCOMPLETE"):
        require_signal_metadata_completeness(db_path)
    assert _sha256(db_path) == before


def test_orphan_metadata_fails_closed(tmp_path: Path) -> None:
    db_path = migrate_test_database(tmp_path / "orphan.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        _insert_metadata(conn, signal_id=999)
    before = _sha256(db_path)

    result = verify_signal_metadata_completeness(db_path)

    assert result.complete is False
    assert result.ledger_count == 0
    assert result.metadata_count == 1
    assert result.canonical_count == 0
    assert result.orphan_metadata_count == 1
    assert "ORPHAN_METADATA" in result.reason_codes
    assert _sha256(db_path) == before


def test_invalid_metadata_fails_closed_even_if_database_checks_were_bypassed(
    tmp_path: Path,
) -> None:
    db_path = migrate_test_database(tmp_path / "invalid.db")
    with sqlite3.connect(db_path) as conn:
        signal_id = _insert_ledger_row(conn)
        conn.execute("PRAGMA ignore_check_constraints=ON")
        _insert_metadata(
            conn,
            signal_id=signal_id,
            signal_class="STRICT",
            strategy_profile="experimental_pretrigger_v1",
            decision_contract_hash="NOT-A-SHA",
        )
        conn.execute("PRAGMA ignore_check_constraints=OFF")
    before = _sha256(db_path)

    result = verify_signal_metadata_completeness(db_path)

    assert result.complete is False
    assert result.invalid_metadata_count == 1
    assert "INVALID_METADATA" in result.reason_codes
    assert _sha256(db_path) == before


def test_missing_database_is_rejected_without_creation(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.db"

    with pytest.raises(SignalMetadataError):
        verify_signal_metadata_completeness(db_path)

    assert db_path.exists() is False


@pytest.mark.asyncio
async def test_startup_rejects_incomplete_metadata_before_background_workers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import waterfallhunter.main as main

    db_path = migrate_test_database(tmp_path / "startup-incomplete.db")
    with sqlite3.connect(db_path) as conn:
        _insert_ledger_row(conn)

    monkeypatch.setattr(main.db, "db_path", str(db_path))
    started = _patch_startup_workers(monkeypatch, main)

    with pytest.raises(SignalMetadataError, match="SIGNAL_METADATA_INCOMPLETE"):
        await main.startup_event()

    assert started == []


@pytest.mark.asyncio
async def test_startup_complete_metadata_reaches_worker_scheduling(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import waterfallhunter.main as main

    db_path = migrate_test_database(tmp_path / "startup-complete.db")
    monkeypatch.setattr(main.db, "db_path", str(db_path))
    started = _patch_startup_workers(monkeypatch, main)

    await main.startup_event()

    assert started
