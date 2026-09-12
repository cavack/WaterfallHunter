from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from waterfallhunter.core.schema_contract import (
    CURRENT_RUNTIME_SCHEMA_VERSION,
    verify_managed_schema,
)

from schema_test_support import migrate_test_database


EXPECTED_METADATA_COLUMNS = (
    "signal_id",
    "signal_class",
    "strategy_profile",
    "score_version",
    "model_generation",
    "decision_contract_hash",
    "analysis_observed_at",
    "reference_observed_at",
    "metadata_contract_version",
    "classification_method",
    "classification_evidence_hash",
    "created_at",
)


def _insert_ledger_row(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    triggered_at: int,
) -> int:
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
            triggered_at,
        ),
    )
    return int(cursor.lastrowid)


def _insert_metadata(
    conn: sqlite3.Connection,
    *,
    signal_id: int,
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
            "STRICT",
            "strict_score_v2",
            "score_v2",
            "waterfall_signal_model_v1",
            "a" * 64,
            1_700_000_000,
            1_699_999_990,
            "signal_metadata_v1",
            "FUTURE_PIPELINE_EXPLICIT",
            None,
            1_700_000_001,
        ),
    )


def test_packaged_migrations_create_current_metadata_and_view(
    tmp_path: Path,
) -> None:
    db_path = migrate_test_database(tmp_path / "metadata-v3.db")

    with sqlite3.connect(db_path) as conn:
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        columns = tuple(
            str(row[1])
            for row in conn.execute("PRAGMA table_info(signal_metadata)").fetchall()
        )
        view_sql = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='view' AND name='canonical_signal_view'"
        ).fetchone()

    assert CURRENT_RUNTIME_SCHEMA_VERSION == 10
    assert user_version == 10
    assert columns == EXPECTED_METADATA_COLUMNS
    assert view_sql is not None
    normalized = " ".join(str(view_sql[0]).split()).upper()
    assert "INNER JOIN SIGNAL_METADATA" in normalized
    assert "LEFT JOIN SIGNAL_METADATA" not in normalized
    assert "SELECT *" not in normalized


def test_signal_metadata_is_immutable(
    tmp_path: Path,
) -> None:
    db_path = migrate_test_database(tmp_path / "immutable.db")

    with sqlite3.connect(db_path) as conn:
        signal_id = _insert_ledger_row(
            conn,
            symbol="STRICT/USDT:USDT",
            triggered_at=1_700_000_000,
        )
        _insert_metadata(conn, signal_id=signal_id)
        conn.commit()

        for sql in (
            "UPDATE signal_metadata SET score_version='changed' WHERE signal_id=?",
            "DELETE FROM signal_metadata WHERE signal_id=?",
        ):
            try:
                conn.execute(sql, (signal_id,))
            except sqlite3.IntegrityError as exc:
                assert "signal_metadata is immutable" in str(exc)
            else:
                raise AssertionError("signal_metadata mutation unexpectedly succeeded")


def test_signal_metadata_rejects_noncanonical_score_version_and_timestamp_types(
    tmp_path: Path,
) -> None:
    db_path = migrate_test_database(tmp_path / "metadata-constraints.db")

    with sqlite3.connect(db_path) as conn:
        signal_id = _insert_ledger_row(
            conn,
            symbol="CONSTRAINT/USDT:USDT",
            triggered_at=1_700_000_000,
        )
        values = (
            signal_id,
            "STRICT",
            "strict_score_v2",
            "score_v2_watch_v1",
            "waterfall_signal_model_v1",
            "a" * 64,
            1_700_000_000,
            None,
            "signal_metadata_v1",
            "FUTURE_PIPELINE_EXPLICIT",
            None,
            1_700_000_001,
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO signal_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )

        noninteger_timestamp = list(values)
        noninteger_timestamp[3] = "score_v2"
        noninteger_timestamp[6] = 1.5
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO signal_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(noninteger_timestamp),
            )


@pytest.mark.parametrize(
    ("column_index", "blob_value"),
    [
        (4, b"waterfall_signal_model_v1"),
        (5, b"a" * 64),
        (10, b"a" * 64),
    ],
)
def test_signal_metadata_rejects_blobs_in_required_text_evidence(
    tmp_path: Path,
    column_index: int,
    blob_value: bytes,
) -> None:
    db_path = migrate_test_database(tmp_path / f"metadata-blob-{column_index}.db")

    with sqlite3.connect(db_path) as conn:
        signal_id = _insert_ledger_row(
            conn,
            symbol=f"BLOB{column_index}/USDT:USDT",
            triggered_at=1_700_000_000,
        )
        values = [
            signal_id,
            "STRICT",
            "strict_score_v2",
            "score_v2",
            "waterfall_signal_model_v1",
            "a" * 64,
            1_700_000_000,
            None,
            "signal_metadata_v1",
            "FUTURE_PIPELINE_EXPLICIT",
            None,
            1_700_000_001,
        ]
        values[column_index] = blob_value
        if column_index == 10:
            values[9] = "LEGACY_PROFILE_EXACT_MATCH"

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO signal_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(values),
            )


def test_canonical_view_hides_ledger_rows_without_metadata(
    tmp_path: Path,
) -> None:
    db_path = migrate_test_database(tmp_path / "inner-join.db")

    with sqlite3.connect(db_path) as conn:
        canonical_id = _insert_ledger_row(
            conn,
            symbol="CANONICAL/USDT:USDT",
            triggered_at=1_700_000_000,
        )
        unresolved_id = _insert_ledger_row(
            conn,
            symbol="UNRESOLVED/USDT:USDT",
            triggered_at=1_700_000_001,
        )
        _insert_metadata(conn, signal_id=canonical_id)
        rows = conn.execute(
            "SELECT signal_id, signal_class, strategy_profile "
            "FROM canonical_signal_view ORDER BY signal_id"
        ).fetchall()

    assert rows == [
        (canonical_id, "STRICT", "strict_score_v2"),
    ]
    assert unresolved_id != canonical_id


def test_schema_verifier_rejects_canonical_view_drift(
    tmp_path: Path,
) -> None:
    db_path = migrate_test_database(tmp_path / "view-drift.db")

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP VIEW canonical_signal_view")
        conn.execute(
            "CREATE VIEW canonical_signal_view AS "
            "SELECT id AS signal_id FROM lbank_signal_ledger"
        )

    result = verify_managed_schema(
        db_path,
        check_user_version=CURRENT_RUNTIME_SCHEMA_VERSION,
    )
    assert result.valid is False
    assert any(
        issue.code == "VIEW_MISMATCH"
        and issue.object_name == "canonical_signal_view"
        for issue in result.issues
    )
