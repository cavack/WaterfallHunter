from __future__ import annotations

import sqlite3

import pytest

from waterfallhunter.core.schema_unique_constraints import (
    verify_unique_constraints_connection,
)


@pytest.mark.parametrize(
    "conflict_policy",
    ("REPLACE", "IGNORE", "FAIL", "ROLLBACK"),
)
def test_composite_unique_constraint_rejects_non_abort_conflict_policy(
    conflict_policy: str,
):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        f"""
        CREATE TABLE production_evidence_snapshots (
            bucket_started_at INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            UNIQUE(bucket_started_at, symbol) ON CONFLICT {conflict_policy}
        )
        """
    )

    result = verify_unique_constraints_connection(
        conn,
        tables=frozenset({"production_evidence_snapshots"}),
    )

    assert result.valid is False
    assert conflict_policy.casefold() in result.issues[0].detail


def test_unique_constraint_parser_ignores_comments_and_string_literals():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE lbank_signal_outcomes (
            signal_id INTEGER UNIQUE /* ON CONFLICT REPLACE */,
            note TEXT CHECK(note <> 'UNIQUE ON CONFLICT IGNORE')
        )
        """
    )

    result = verify_unique_constraints_connection(
        conn,
        tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert result.valid is True
    assert result.issues == ()


def test_unique_index_cannot_replace_canonical_unique_constraint():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE lbank_signal_outcomes (signal_id INTEGER NOT NULL);
        CREATE UNIQUE INDEX user_signal_id_unique
        ON lbank_signal_outcomes(signal_id);
        """
    )

    result = verify_unique_constraints_connection(
        conn,
        tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert result.valid is False
    assert result.issues[0].actual == (("signal_id",),)
    assert "found ()" in result.issues[0].detail


def test_duplicate_unique_constraint_is_rejected():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE lbank_signal_outcomes (
            signal_id INTEGER NOT NULL UNIQUE,
            UNIQUE(signal_id)
        )
        """
    )

    result = verify_unique_constraints_connection(
        conn,
        tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert result.valid is False
    assert result.issues[0].detail.count("abort") == 3
