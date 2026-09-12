from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core.schema_contract import (
    CURRENT_RUNTIME_SCHEMA_VERSION,
    verify_managed_schema,
)
from waterfallhunter.core import signal_metadata_store as metadata_store


def _insert_ledger_row(conn: sqlite3.Connection, *, triggered_at: int) -> int:
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
            "REVIEW/USDT:USDT",
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
            "STRICT",
            "strict_score_v2",
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


def _balanced_parenthesis_end(sql: str, open_index: int) -> int:
    depth = 0
    quote: str | None = None
    index = open_index
    while index < len(sql):
        char = sql[index]
        if quote is not None:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 2
                    continue
                quote = None
        elif char in ("'", '"'):
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise AssertionError("unterminated CHECK expression")


def _check_span(sql: str, marker: str) -> tuple[int, int]:
    marker_folded = marker.casefold()
    for match in re.finditer(r"CHECK\s*\(", sql, flags=re.IGNORECASE):
        end = _balanced_parenthesis_end(sql, match.end() - 1)
        if marker_folded in sql[match.start():end].casefold():
            return match.start(), end
    raise AssertionError(f"CHECK containing {marker!r} not found")


def _include_adjacent_comma(sql: str, start: int, end: int) -> tuple[int, int]:
    tail = end
    while tail < len(sql) and sql[tail].isspace():
        tail += 1
    if tail < len(sql) and sql[tail] == ",":
        return start, tail + 1

    head = start
    while head > 0 and sql[head - 1].isspace():
        head -= 1
    if head > 0 and sql[head - 1] == ",":
        head -= 1
    return head, end


def _remove_check_containing(sql: str, marker: str) -> str:
    start, end = _check_span(sql, marker)
    start, end = _include_adjacent_comma(sql, start, end)
    return sql[:start] + sql[end:]


def _remove_projection(sql: str, projection: str) -> str:
    match = re.search(re.escape(projection), sql)
    if match is None:
        raise AssertionError(f"projection {projection!r} not found")

    start, end = match.span()
    tail = end
    while tail < len(sql) and sql[tail].isspace():
        tail += 1
    if tail < len(sql) and sql[tail] == ",":
        tail += 1
        return sql[:start] + sql[tail:]

    head = start
    while head > 0 and sql[head - 1].isspace():
        head -= 1
    if head > 0 and sql[head - 1] == ",":
        head -= 1
    return sql[:head] + sql[end:]


def _rewrite_sqlite_schema(
    db_path: Path,
    *,
    object_type: str,
    object_name: str,
    transform,
) -> None:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type=? AND name=?",
            (object_type, object_name),
        ).fetchone()
        assert row is not None
        assert isinstance(row[0], str)
        original = str(row[0])
        rewritten = transform(original)
        assert rewritten != original
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql=? WHERE type=? AND name=?",
            (rewritten, object_type, object_name),
        )
        schema_version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
        conn.execute(f"PRAGMA schema_version={schema_version + 1}")
        conn.execute("PRAGMA writable_schema=OFF")


@pytest.mark.parametrize(
    "marker",
    [
        "classification_method IN",
        "classification_method = 'FUTURE_PIPELINE_EXPLICIT'",
        "signal_class = 'STRICT'",
    ],
)
def test_schema_verifier_rejects_missing_binding_metadata_check(
    tmp_path: Path,
    marker: str,
) -> None:
    db_path = migrate_test_database(tmp_path / f"check-{len(marker)}.db")
    _rewrite_sqlite_schema(
        db_path,
        object_type="table",
        object_name="signal_metadata",
        transform=lambda sql: _remove_check_containing(sql, marker),
    )

    result = verify_managed_schema(
        db_path,
        check_user_version=CURRENT_RUNTIME_SCHEMA_VERSION,
    )

    assert result.valid is False
    assert any(
        issue.code == "CHECK_MISSING" and issue.object_name == "signal_metadata"
        for issue in result.issues
    )


@pytest.mark.parametrize(
    "projection",
    [
        "m.score_version",
        "m.model_generation",
        "m.analysis_observed_at",
        "m.reference_observed_at",
        "m.metadata_contract_version",
        "m.classification_method",
        "m.classification_evidence_hash",
    ],
)
def test_schema_verifier_rejects_missing_canonical_metadata_projection(
    tmp_path: Path,
    projection: str,
) -> None:
    db_path = migrate_test_database(tmp_path / f"view-{projection.split('.')[-1]}.db")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='view' AND name='canonical_signal_view'"
        ).fetchone()
        assert row is not None
        assert isinstance(row[0], str)
        original = str(row[0])
        rewritten = _remove_projection(original, projection)
        assert rewritten != original
        conn.execute("DROP VIEW canonical_signal_view")
        conn.execute(rewritten)

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


def test_completeness_uses_one_read_snapshot_during_concurrent_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = migrate_test_database(tmp_path / "snapshot.db")
    original_scalar_count = metadata_store._scalar_count
    calls = 0

    def interleaved_scalar_count(conn: sqlite3.Connection, sql: str) -> int:
        nonlocal calls
        value = original_scalar_count(conn, sql)
        calls += 1
        if calls == 4:
            with sqlite3.connect(db_path, isolation_level=None) as writer:
                signal_id = _insert_ledger_row(writer, triggered_at=1_700_000_100)
                writer.execute("PRAGMA ignore_check_constraints=ON")
                _insert_metadata(
                    writer,
                    signal_id=signal_id,
                    decision_contract_hash="NOT-A-SHA",
                )
                writer.execute("PRAGMA ignore_check_constraints=OFF")
        return value

    monkeypatch.setattr(metadata_store, "_scalar_count", interleaved_scalar_count)

    result = metadata_store.verify_signal_metadata_completeness(db_path)

    assert result.complete is True
    assert result.ledger_count == 0
    assert result.metadata_count == 0
    assert result.canonical_count == 0
    assert result.missing_metadata_count == 0
    assert result.orphan_metadata_count == 0
    assert result.invalid_metadata_count == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM lbank_signal_ledger").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM signal_metadata").fetchone()[0] == 1


def test_completeness_fails_when_canonical_view_count_is_inconsistent(
    tmp_path: Path,
) -> None:
    db_path = migrate_test_database(tmp_path / "canonical-count.db")
    with sqlite3.connect(db_path) as conn:
        signal_id = _insert_ledger_row(conn, triggered_at=1_700_000_200)
        _insert_metadata(conn, signal_id=signal_id)
        conn.execute("DROP VIEW canonical_signal_view")
        conn.execute(
            "CREATE VIEW canonical_signal_view AS "
            "SELECT s.id AS signal_id FROM lbank_signal_ledger AS s WHERE 0"
        )

    result = metadata_store.verify_signal_metadata_completeness(db_path)

    assert result.complete is False
    assert result.ledger_count == 1
    assert result.metadata_count == 1
    assert result.canonical_count == 0
    assert "CANONICAL_VIEW_COUNT_MISMATCH" in result.reason_codes
