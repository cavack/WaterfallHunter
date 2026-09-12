import sqlite3

import pytest

from waterfallhunter.core import migrations


def _migration(version: int, name: str, sql: bytes):
    return migrations.Migration.from_bytes(
        version=version,
        name=name,
        filename=f"{version:04d}_{name}.sql",
        sql_bytes=sql,
    )


def test_migration_identity_must_match_canonical_filename():
    with pytest.raises(migrations.MigrationDiscoveryError):
        migrations.Migration.from_bytes(
            version=1,
            name="expected_name",
            filename="0002_other_name.sql",
            sql_bytes=b"SELECT 1;\n",
        )


def test_runner_does_not_create_missing_parent_directory(tmp_path):
    missing_parent = tmp_path / "missing" / "nested"
    db_path = missing_parent / "registry.db"

    runner = migrations.MigrationRunner(db_path=db_path)

    with pytest.raises(migrations.MigrationError):
        runner.apply()

    assert missing_parent.exists() is False
    assert db_path.exists() is False


def test_incomplete_sql_fails_before_state_advances(tmp_path):
    db_path = tmp_path / "registry.db"
    first = migrations.discover_migrations()[0]
    migrations.MigrationRunner(db_path=db_path, migrations=(first,)).apply()

    incomplete = _migration(
        2,
        "incomplete",
        b"CREATE TABLE incomplete_table (id INTEGER PRIMARY KEY",
    )
    runner = migrations.MigrationRunner(
        db_path=db_path,
        migrations=(first, incomplete),
    )

    with pytest.raises(migrations.MigrationError):
        runner.apply()

    with sqlite3.connect(db_path) as conn:
        table_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='incomplete_table'"
        ).fetchone()[0]
        history = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert table_count == 0
    assert history == [(1,)]
    assert user_version == 1


def test_sql_splitter_preserves_semicolons_inside_string_literals(tmp_path):
    db_path = tmp_path / "registry.db"
    first = migrations.discover_migrations()[0]
    second = _migration(
        2,
        "literal_semicolon",
        b"""
        CREATE TABLE literal_semicolon (value TEXT NOT NULL);
        INSERT INTO literal_semicolon (value) VALUES ('alpha;beta');
        """,
    )

    applied = migrations.MigrationRunner(
        db_path=db_path,
        migrations=(first, second),
    ).apply()

    assert applied == (1, 2)
    with sqlite3.connect(db_path) as conn:
        value = conn.execute("SELECT value FROM literal_semicolon").fetchone()[0]
    assert value == "alpha;beta"


@pytest.mark.parametrize(
    "trailing_comment",
    [
        b"-- explanation",
        b"/* explanation */",
    ],
)
def test_sql_splitter_accepts_comment_only_remainder(tmp_path, trailing_comment):
    db_path = tmp_path / "registry.db"
    migration = _migration(
        1,
        "trailing_comment",
        b"CREATE TABLE trailing_comment (id INTEGER PRIMARY KEY); "
        + trailing_comment,
    )

    applied = migrations.MigrationRunner(
        db_path=db_path,
        migrations=(migration,),
    ).apply()

    assert applied == (1,)
    with sqlite3.connect(db_path) as conn:
        table_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='trailing_comment'"
        ).fetchone()[0]
    assert table_count == 1


@pytest.mark.parametrize(
    "transaction_statement",
    [
        b"BEGIN;",
        b"COMMIT;",
        b"END;",
        b"ROLLBACK;",
        b"SAVEPOINT nested;",
        b"RELEASE nested;",
    ],
)
def test_runner_rejects_transaction_control_before_migration_sql_executes(
    tmp_path,
    transaction_statement,
):
    db_path = tmp_path / "registry.db"
    migration = _migration(
        1,
        "transaction_escape",
        (
            b"CREATE TABLE leaked (id INTEGER PRIMARY KEY); "
            + transaction_statement
            + b" INSERT INTO missing_table VALUES (1);"
        ),
    )

    with pytest.raises(
        migrations.MigrationError,
        match="must not control transactions",
    ):
        migrations.MigrationRunner(
            db_path=db_path,
            migrations=(migration,),
        ).apply()

    with sqlite3.connect(db_path) as conn:
        leaked_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type='table' AND name='leaked'"
        ).fetchone()[0]
        history = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]

    assert leaked_count == 0
    assert history == []
    assert user_version == 0


def test_malformed_migration_history_missing_columns_is_typed_state_failure(tmp_path):
    db_path = tmp_path / "registry.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, checksum_sha256 TEXT)"
        )
    runner = migrations.MigrationRunner(db_path=db_path)

    with pytest.raises(migrations.MigrationStateError):
        runner.apply()


def test_malformed_migration_history_invalid_version_is_typed_state_failure(tmp_path):
    db_path = tmp_path / "registry.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "version TEXT, name TEXT, checksum_sha256 TEXT)"
        )
        conn.execute(
            "INSERT INTO schema_migrations (version, name, checksum_sha256) "
            "VALUES ('not-an-int', 'broken', 'broken')"
        )
    runner = migrations.MigrationRunner(db_path=db_path)

    with pytest.raises(migrations.MigrationStateError):
        runner.apply()
