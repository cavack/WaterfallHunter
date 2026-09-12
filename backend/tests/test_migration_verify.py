from __future__ import annotations

from pathlib import Path

import pytest

from waterfallhunter.core.migrations import (
    MigrationError,
    MigrationRunner,
    MigrationStateError,
)


def test_verify_reads_valid_history_without_mutation(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    runner = MigrationRunner(db_path=db_path, source_revision="test")

    assert runner.apply() == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    before = db_path.read_bytes()

    assert runner.verify() == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    assert db_path.read_bytes() == before


def test_verify_does_not_create_missing_database(tmp_path: Path):
    db_path = tmp_path / "missing.db"
    runner = MigrationRunner(db_path=db_path)

    with pytest.raises(MigrationError, match="does not exist"):
        runner.verify()

    assert not db_path.exists()


def test_verify_rejects_malformed_history_without_mutation(tmp_path: Path):
    import sqlite3

    db_path = tmp_path / "registry.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, "
            "checksum_sha256 TEXT NOT NULL)"
        )
        conn.execute("PRAGMA user_version=1")

    before = db_path.read_bytes()
    runner = MigrationRunner(db_path=db_path)

    with pytest.raises(MigrationStateError):
        runner.verify()

    assert db_path.read_bytes() == before
