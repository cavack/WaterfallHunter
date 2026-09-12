import hashlib
import sqlite3
from importlib import resources

import pytest


def _migrations_module():
    from waterfallhunter.core import migrations

    return migrations


def _packaged_migration():
    return _migrations_module().discover_migrations()[0]


def _migration(version: int, name: str, sql: bytes):
    migrations = _migrations_module()
    return migrations.Migration.from_bytes(
        version=version,
        name=name,
        filename=f"{version:04d}_{name}.sql",
        sql_bytes=sql,
    )


def test_package_migration_discovery_is_contiguous_and_hashes_exact_bytes():
    migrations = _migrations_module()

    discovered = migrations.discover_migrations()

    assert [item.version for item in discovered] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert [item.name for item in discovered] == [
        "db_readiness_probe",
        "runtime_schema_baseline",
        "signal_metadata",
        "signal_decision_outbox",
        "lifecycle_v2_shadow",
        "entry_decisions",
        "entry_decision_advisories",
        "decision_outcome_capture",
        "decision_outcome_resolution",
        "runtime_query_performance",
    ]

    for migration in discovered:
        raw = (
            resources.files("waterfallhunter.migrations")
            .joinpath(migration.filename)
            .read_bytes()
        )
        assert migration.sql_bytes == raw
        assert migration.checksum_sha256 == hashlib.sha256(raw).hexdigest()


def test_packaged_migrations_include_runtime_baseline():
    discovered = _migrations_module().discover_migrations()

    assert [item.version for item in discovered] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert discovered[1].name == "runtime_schema_baseline"
    assert discovered[1].filename == "0002_runtime_schema_baseline.sql"


def test_validate_migrations_rejects_duplicate_versions():
    migrations = _migrations_module()
    first = migrations.Migration.from_bytes(
        version=1,
        name="first",
        filename="0001_first.sql",
        sql_bytes=b"SELECT 1;\n",
    )
    duplicate = migrations.Migration.from_bytes(
        version=1,
        name="duplicate",
        filename="0001_duplicate.sql",
        sql_bytes=b"SELECT 2;\n",
    )

    with pytest.raises(migrations.MigrationDiscoveryError):
        migrations.validate_migrations((first, duplicate))


def test_validate_migrations_rejects_gaps_and_non_one_start():
    migrations = _migrations_module()
    version_two = migrations.Migration.from_bytes(
        version=2,
        name="second",
        filename="0002_second.sql",
        sql_bytes=b"SELECT 2;\n",
    )

    with pytest.raises(migrations.MigrationDiscoveryError):
        migrations.validate_migrations((version_two,))

    version_one = migrations.Migration.from_bytes(
        version=1,
        name="first",
        filename="0001_first.sql",
        sql_bytes=b"SELECT 1;\n",
    )
    version_three = migrations.Migration.from_bytes(
        version=3,
        name="third",
        filename="0003_third.sql",
        sql_bytes=b"SELECT 3;\n",
    )

    with pytest.raises(migrations.MigrationDiscoveryError):
        migrations.validate_migrations((version_one, version_three))


def test_migration_checksum_is_stable_and_content_sensitive():
    migrations = _migrations_module()
    left = migrations.Migration.from_bytes(
        version=1,
        name="probe",
        filename="0001_probe.sql",
        sql_bytes=b"SELECT 1;\n",
    )
    same = migrations.Migration.from_bytes(
        version=1,
        name="probe",
        filename="0001_probe.sql",
        sql_bytes=b"SELECT 1;\n",
    )
    changed = migrations.Migration.from_bytes(
        version=1,
        name="probe",
        filename="0001_probe.sql",
        sql_bytes=b"SELECT 2;\n",
    )

    assert left.checksum_sha256 == same.checksum_sha256
    assert left.checksum_sha256 != changed.checksum_sha256


def test_runner_clean_install_records_history_and_user_version(tmp_path):
    migrations = _migrations_module()
    db_path = tmp_path / "registry.db"
    packaged = _packaged_migration()

    runner = migrations.MigrationRunner(
        db_path=db_path,
        migrations=(packaged,),
        source_revision="test-revision",
    )
    applied = runner.apply()

    assert applied == (1,)

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        row = conn.execute(
            "SELECT version, name, checksum_sha256, source_revision "
            "FROM schema_migrations"
        ).fetchone()
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert "schema_migrations" in tables
    assert "db_readiness_probe" in tables
    assert row == (
        1,
        "db_readiness_probe",
        packaged.checksum_sha256,
        "test-revision",
    )
    assert user_version == 1


def test_runner_rerun_is_idempotent(tmp_path):
    migrations = _migrations_module()
    db_path = tmp_path / "registry.db"
    runner = migrations.MigrationRunner(
        db_path=db_path,
        migrations=(_packaged_migration(),),
    )

    assert runner.apply() == (1,)
    assert runner.apply() == ()

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert count == 1
    assert user_version == 1


def test_runner_fails_closed_on_applied_checksum_mismatch(tmp_path):
    migrations = _migrations_module()
    db_path = tmp_path / "registry.db"
    packaged = _packaged_migration()
    migrations.MigrationRunner(
        db_path=db_path,
        migrations=(packaged,),
    ).apply()

    tampered = _migration(
        1,
        "db_readiness_probe",
        packaged.sql_bytes + b"\n-- changed after application\n",
    )
    runner = migrations.MigrationRunner(
        db_path=db_path,
        migrations=(tampered,),
    )

    with pytest.raises(migrations.MigrationChecksumMismatch):
        runner.apply()


def test_runner_fails_closed_when_history_and_user_version_disagree(tmp_path):
    migrations = _migrations_module()
    db_path = tmp_path / "registry.db"
    runner = migrations.MigrationRunner(
        db_path=db_path,
        migrations=(_packaged_migration(),),
    )
    runner.apply()

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 0")

    with pytest.raises(migrations.MigrationStateError):
        runner.apply()


def test_failed_migration_rolls_back_schema_history_and_version_atomically(tmp_path):
    migrations = _migrations_module()
    db_path = tmp_path / "registry.db"
    first = _packaged_migration()
    migrations.MigrationRunner(
        db_path=db_path,
        migrations=(first,),
    ).apply()

    failing_second = _migration(
        2,
        "failing_second",
        b"""
        CREATE TABLE should_rollback (id INTEGER PRIMARY KEY);
        INSERT INTO table_that_does_not_exist (id) VALUES (1);
        """,
    )
    runner = migrations.MigrationRunner(
        db_path=db_path,
        migrations=(first, failing_second),
    )

    with pytest.raises(migrations.MigrationError):
        runner.apply()

    with sqlite3.connect(db_path) as conn:
        rolled_back_table = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='should_rollback'"
        ).fetchone()[0]
        history = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert rolled_back_table == 0
    assert history == [(1,)]
    assert user_version == 1


def test_schema_migration_history_is_immutable(tmp_path):
    db_path = tmp_path / "registry.db"
    _migrations_module().MigrationRunner(
        db_path=db_path,
        migrations=(_packaged_migration(),),
    ).apply()

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                "UPDATE schema_migrations SET name='rewritten' WHERE version=1"
            )
        conn.rollback()

        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM schema_migrations WHERE version=1")
        conn.rollback()

        count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]

    assert count == 1
