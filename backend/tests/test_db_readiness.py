import sqlite3
import time

import pytest

from waterfallhunter.core.migrations import MigrationRunner
from waterfallhunter.core.schema_contract import CURRENT_RUNTIME_SCHEMA_VERSION


def _migrated_db(tmp_path):
    db_path = tmp_path / "registry.db"
    MigrationRunner(db_path=db_path).apply()
    return db_path


def test_deep_readiness_writes_reads_rolls_back_and_leaves_zero_residue(tmp_path):
    from waterfallhunter.core import db_readiness

    db_path = _migrated_db(tmp_path)
    result = db_readiness.probe_database(
        db_path=db_path,
        expected_schema_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        busy_timeout_ms=100,
        check_integrity=True,
        check_foreign_keys=True,
    )

    assert result.ready is True
    assert result.schema_version == CURRENT_RUNTIME_SCHEMA_VERSION
    assert result.expected_schema_version == CURRENT_RUNTIME_SCHEMA_VERSION
    assert result.read_ok is True
    assert result.write_rollback_ok is True
    assert result.integrity_ok is True
    assert result.foreign_keys_ok is True
    assert result.residue_count == 0
    assert result.reason_codes == ()
    assert result.checked_at > 0

    with sqlite3.connect(db_path) as conn:
        residue = conn.execute("SELECT COUNT(*) FROM db_readiness_probe").fetchone()[0]
    assert residue == 0


def test_deep_readiness_fails_closed_when_required_probe_table_is_missing(tmp_path):
    from waterfallhunter.core import db_readiness

    db_path = tmp_path / "unmigrated.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version=1")

    result = db_readiness.probe_database(
        db_path=db_path,
        expected_schema_version=1,
        busy_timeout_ms=100,
    )

    assert result.ready is False
    assert "REQUIRED_TABLE_MISSING" in result.reason_codes
    assert result.write_rollback_ok is False


def test_deep_readiness_fails_closed_on_schema_version_mismatch(tmp_path):
    from waterfallhunter.core import db_readiness

    db_path = _migrated_db(tmp_path)
    result = db_readiness.probe_database(
        db_path=db_path,
        expected_schema_version=CURRENT_RUNTIME_SCHEMA_VERSION + 1,
        busy_timeout_ms=100,
    )

    assert result.ready is False
    assert result.schema_version == CURRENT_RUNTIME_SCHEMA_VERSION
    assert "SCHEMA_VERSION_MISMATCH" in result.reason_codes
    assert result.write_rollback_ok is False


def test_deep_readiness_fails_closed_on_managed_schema_mismatch(tmp_path):
    from waterfallhunter.core import db_readiness

    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE lbank_catalog")

    result = db_readiness.probe_database(
        db_path=db_path,
        expected_schema_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        busy_timeout_ms=100,
    )

    assert result.ready is False
    assert result.read_ok is False
    assert result.reason_codes == ("MANAGED_SCHEMA_MISMATCH",)
    assert result.write_rollback_ok is False


def test_deep_readiness_lock_failure_is_bounded_and_non_mutating(tmp_path):
    from waterfallhunter.core import db_readiness

    db_path = _migrated_db(tmp_path)
    blocker = sqlite3.connect(db_path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        result = db_readiness.probe_database(
            db_path=db_path,
            expected_schema_version=CURRENT_RUNTIME_SCHEMA_VERSION,
            busy_timeout_ms=50,
        )
        elapsed = time.monotonic() - started
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    assert result.ready is False
    assert result.read_ok is True
    assert result.write_rollback_ok is False
    assert "WRITE_ROLLBACK_FAILED" in result.reason_codes
    assert elapsed < 1.0

    with sqlite3.connect(db_path) as conn:
        residue = conn.execute("SELECT COUNT(*) FROM db_readiness_probe").fetchone()[0]
    assert residue == 0


def test_deep_readiness_reports_foreign_key_failure_without_repairing_data(tmp_path):
    from waterfallhunter.core import db_readiness

    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE child ("
            "id INTEGER PRIMARY KEY, "
            "parent_id INTEGER REFERENCES parent(id))"
        )
        conn.execute("INSERT INTO child (id, parent_id) VALUES (1, 999)")

    result = db_readiness.probe_database(
        db_path=db_path,
        expected_schema_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        busy_timeout_ms=100,
        check_integrity=True,
        check_foreign_keys=True,
    )

    assert result.ready is False
    assert result.integrity_ok is True
    assert result.foreign_keys_ok is False
    assert "FOREIGN_KEY_CHECK_FAILED" in result.reason_codes

    with sqlite3.connect(db_path) as conn:
        child = conn.execute("SELECT id, parent_id FROM child").fetchone()
    assert child == (1, 999)


def test_optional_integrity_and_fk_checks_remain_explicitly_unavailable(tmp_path):
    from waterfallhunter.core import db_readiness

    result = db_readiness.probe_database(
        db_path=_migrated_db(tmp_path),
        expected_schema_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        busy_timeout_ms=100,
        check_integrity=False,
        check_foreign_keys=False,
    )

    assert result.ready is True
    assert result.integrity_ok is None
    assert result.foreign_keys_ok is None


def test_require_ready_raises_typed_error_for_not_ready_result(tmp_path):
    from waterfallhunter.core import db_readiness

    result = db_readiness.probe_database(
        db_path=_migrated_db(tmp_path),
        expected_schema_version=99,
        busy_timeout_ms=100,
    )

    with pytest.raises(db_readiness.DatabaseNotReadyError):
        db_readiness.require_ready(result)


def test_missing_database_path_is_not_created_by_readiness_probe(tmp_path):
    from waterfallhunter.core import db_readiness

    db_path = tmp_path / "does-not-exist.db"
    result = db_readiness.probe_database(
        db_path=db_path,
        expected_schema_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        busy_timeout_ms=100,
    )

    assert result.ready is False
    assert "DB_PATH_MISSING" in result.reason_codes
    assert db_path.exists() is False


def test_readiness_reports_open_failure_when_managed_fk_setup_fails(
    tmp_path,
    monkeypatch,
):
    from waterfallhunter.core import db_readiness
    from waterfallhunter.core.managed_sqlite import ManagedSQLiteError

    db_path = _migrated_db(tmp_path)

    def reject_managed_connection(*args, **kwargs):
        raise ManagedSQLiteError("MANAGED_SQLITE_FOREIGN_KEYS_UNAVAILABLE")

    monkeypatch.setattr(
        db_readiness,
        "connect_managed_sqlite",
        reject_managed_connection,
    )

    result = db_readiness.probe_database(
        db_path=db_path,
        expected_schema_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        busy_timeout_ms=100,
    )

    assert result.ready is False
    assert result.reason_codes == ("OPEN_FAILED",)


@pytest.mark.parametrize("filename", ["hash#name.db", "question?name.db"])
def test_readiness_targets_exact_database_path_with_uri_special_characters(
    tmp_path,
    filename,
):
    from waterfallhunter.core import db_readiness

    db_path = tmp_path / filename
    MigrationRunner(db_path=db_path).apply()

    result = db_readiness.probe_database(
        db_path=db_path,
        expected_schema_version=CURRENT_RUNTIME_SCHEMA_VERSION,
        busy_timeout_ms=100,
    )

    assert result.ready is True
    assert result.reason_codes == ()
    assert db_path.exists() is True
