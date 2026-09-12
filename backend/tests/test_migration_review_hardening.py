import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from waterfallhunter.core import migrations


def _packaged_migration():
    return migrations.discover_migrations()[0]


@pytest.mark.parametrize(
    "collision_sql",
    [
        "CREATE TABLE db_readiness_probe (probe_id TEXT PRIMARY KEY)",
        """
        CREATE TABLE user_extension (id INTEGER PRIMARY KEY);
        CREATE TRIGGER schema_migrations_no_update
        BEFORE UPDATE ON user_extension BEGIN SELECT 1; END;
        """,
    ],
)
def test_runner_rejects_reserved_infrastructure_collision_before_bootstrap(
    tmp_path,
    collision_sql,
):
    db_path = tmp_path / "registry.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(collision_sql)
    before = db_path.read_bytes()

    runner = migrations.MigrationRunner(
        db_path=db_path,
        migrations=(_packaged_migration(),),
    )

    with pytest.raises(migrations.MigrationStateError):
        runner.apply()

    assert db_path.read_bytes() == before
    with sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True) as conn:
        history = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
    assert history is None


def test_runner_rejects_history_schema_missing_required_columns(tmp_path):
    db_path = tmp_path / "registry.db"
    packaged = _packaged_migration()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, "
            "name TEXT NOT NULL, "
            "checksum_sha256 TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum_sha256) "
            "VALUES (?, ?, ?)",
            (packaged.version, packaged.name, packaged.checksum_sha256),
        )
        conn.execute("PRAGMA user_version=1")

    runner = migrations.MigrationRunner(
        db_path=db_path,
        migrations=(packaged,),
    )

    with pytest.raises(migrations.MigrationStateError):
        runner.apply()


@pytest.mark.parametrize(
    "ddl",
    [
        (
            "CREATE TABLE schema_migrations ("
            "version INTEGER, name TEXT NOT NULL, checksum_sha256 TEXT NOT NULL, "
            "applied_at INTEGER NOT NULL, source_revision TEXT)"
        ),
        (
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT, checksum_sha256 TEXT NOT NULL, "
            "applied_at INTEGER NOT NULL, source_revision TEXT)"
        ),
        (
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum_sha256 TEXT, "
            "applied_at INTEGER NOT NULL, source_revision TEXT)"
        ),
        (
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum_sha256 TEXT NOT NULL, "
            "applied_at INTEGER, source_revision TEXT)"
        ),
    ],
)
def test_runner_rejects_history_schema_with_weakened_constraints(tmp_path, ddl):
    db_path = tmp_path / "registry.db"
    packaged = _packaged_migration()
    with sqlite3.connect(db_path) as conn:
        conn.execute(ddl)
        conn.execute(
            "INSERT INTO schema_migrations "
            "(version, name, checksum_sha256, applied_at, source_revision) "
            "VALUES (?, ?, ?, ?, ?)",
            (packaged.version, packaged.name, packaged.checksum_sha256, 1, None),
        )
        conn.execute("PRAGMA user_version=1")

    runner = migrations.MigrationRunner(
        db_path=db_path,
        migrations=(packaged,),
    )

    with pytest.raises(migrations.MigrationStateError):
        runner.apply()


def test_runner_rejects_history_schema_with_extra_composite_primary_key(tmp_path):
    """Reject a composite PK even when required-column PK ordinals look valid."""
    db_path = tmp_path / "registry.db"
    packaged = _packaged_migration()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER, name TEXT NOT NULL, checksum_sha256 TEXT NOT NULL, "
            "applied_at INTEGER NOT NULL, source_revision TEXT, scope TEXT NOT NULL, "
            "PRIMARY KEY(version, scope))"
        )
        conn.execute(
            "INSERT INTO schema_migrations "
            "(version, name, checksum_sha256, applied_at, source_revision, scope) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (packaged.version, packaged.name, packaged.checksum_sha256, 1, None, "default"),
        )
        conn.execute("PRAGMA user_version=1")

    runner = migrations.MigrationRunner(db_path=db_path, migrations=(packaged,))

    with pytest.raises(migrations.MigrationStateError):
        runner.apply()


def test_runner_rejects_immutability_trigger_on_wrong_target(tmp_path):
    """Reject a same-named UPDATE trigger that protects an unrelated table."""
    db_path = tmp_path / "registry.db"
    packaged = _packaged_migration()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum_sha256 TEXT NOT NULL, "
            "applied_at INTEGER NOT NULL, source_revision TEXT)"
        )
        conn.execute("CREATE TABLE unrelated (value INTEGER)")
        conn.execute(
            "CREATE TRIGGER schema_migrations_no_update "
            "BEFORE UPDATE ON unrelated BEGIN SELECT 1; END"
        )
        conn.execute(
            "CREATE TRIGGER schema_migrations_no_delete "
            "BEFORE DELETE ON schema_migrations "
            "BEGIN SELECT RAISE(ABORT, 'schema_migrations is immutable'); END"
        )
        conn.execute(
            "INSERT INTO schema_migrations "
            "(version, name, checksum_sha256, applied_at, source_revision) "
            "VALUES (?, ?, ?, ?, ?)",
            (packaged.version, packaged.name, packaged.checksum_sha256, 1, None),
        )
        conn.execute("PRAGMA user_version=1")

    runner = migrations.MigrationRunner(db_path=db_path, migrations=(packaged,))

    with pytest.raises(migrations.MigrationStateError):
        runner.apply()


def test_runner_rejects_immutability_trigger_without_abort(tmp_path):
    """Reject a same-named DELETE trigger that does not abort the mutation."""
    db_path = tmp_path / "registry.db"
    packaged = _packaged_migration()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum_sha256 TEXT NOT NULL, "
            "applied_at INTEGER NOT NULL, source_revision TEXT)"
        )
        conn.execute(
            "CREATE TRIGGER schema_migrations_no_update "
            "BEFORE UPDATE ON schema_migrations "
            "BEGIN SELECT RAISE(ABORT, 'schema_migrations is immutable'); END"
        )
        conn.execute(
            "CREATE TRIGGER schema_migrations_no_delete "
            "BEFORE DELETE ON schema_migrations BEGIN SELECT 1; END"
        )
        conn.execute(
            "INSERT INTO schema_migrations "
            "(version, name, checksum_sha256, applied_at, source_revision) "
            "VALUES (?, ?, ?, ?, ?)",
            (packaged.version, packaged.name, packaged.checksum_sha256, 1, None),
        )
        conn.execute("PRAGMA user_version=1")

    runner = migrations.MigrationRunner(db_path=db_path, migrations=(packaged,))

    with pytest.raises(migrations.MigrationStateError):
        runner.apply()


def test_validate_migrations_rejects_forged_checksum():
    packaged = _packaged_migration()
    forged = migrations.Migration(
        version=packaged.version,
        name=packaged.name,
        filename=packaged.filename,
        sql_bytes=packaged.sql_bytes,
        checksum_sha256="0" * 64,
    )

    with pytest.raises(migrations.MigrationDiscoveryError):
        migrations.validate_migrations((forged,))


def test_validate_migrations_rejects_direct_noncanonical_identity():
    packaged = _packaged_migration()
    forged = migrations.Migration(
        version=packaged.version,
        name=packaged.name,
        filename="0002_wrong.sql",
        sql_bytes=packaged.sql_bytes,
        checksum_sha256=packaged.checksum_sha256,
    )

    with pytest.raises(migrations.MigrationDiscoveryError):
        migrations.validate_migrations((forged,))


def test_two_concurrent_runners_serialize_pending_check_and_application(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "registry.db"
    packaged = _packaged_migration()
    barrier = threading.Barrier(2)
    thread_state = threading.local()
    original_verify_state = migrations.MigrationRunner._verify_state

    def synchronized_first_verify(self, conn):
        applied = original_verify_state(self, conn)
        if not getattr(thread_state, "first_verify_complete", False):
            thread_state.first_verify_complete = True
            barrier.wait(timeout=5)
        return applied

    monkeypatch.setattr(
        migrations.MigrationRunner,
        "_verify_state",
        synchronized_first_verify,
    )

    def run_runner():
        runner = migrations.MigrationRunner(
            db_path=db_path,
            migrations=(packaged,),
            busy_timeout_ms=2_000,
        )
        return runner.apply()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_runner) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]

    assert sorted(results) == [(), (1,)]
    with sqlite3.connect(db_path) as conn:
        history = conn.execute(
            "SELECT version, name, checksum_sha256 FROM schema_migrations"
        ).fetchall()
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        probe_tables = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='db_readiness_probe'"
        ).fetchone()[0]

    assert history == [(1, packaged.name, packaged.checksum_sha256)]
    assert user_version == 1
    assert probe_tables == 1
