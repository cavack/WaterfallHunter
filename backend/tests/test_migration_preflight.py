from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from schema_test_support import build_legacy_runtime_database, migrate_test_database
from waterfallhunter.core.migration_preflight import (
    MigrationPreflightError,
    PreflightState,
    classify_database,
    require_migration_compatible,
)
from waterfallhunter.core.migrations import MigrationRunner, discover_migrations


_FIXTURE = Path(__file__).with_name("fixtures") / "legacy_runtime_schema_v0.fixture"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_preflight_classifies_missing_path_without_creating_database(tmp_path: Path):
    db_path = tmp_path / "new.db"

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.CLEAN_NEW
    assert result.compatible is True
    assert result.reason_codes == ()
    assert db_path.exists() is False


def test_preflight_classifies_existing_empty_sqlite_without_mutation(tmp_path: Path):
    db_path = tmp_path / "empty.db"
    with sqlite3.connect(db_path):
        pass
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.CLEAN_EMPTY
    assert result.compatible is True
    assert _sha256(db_path) == before


@pytest.mark.parametrize(
    "reserved_name",
    (
        "SCHEMA_MIGRATIONS",
        "lbank_catalog",
        "IDX_LBANK_EXECUTION_QUEUE",
    ),
)
def test_preflight_rejects_reserved_view_in_otherwise_tableless_database(
    tmp_path: Path,
    reserved_name: str,
):
    db_path = tmp_path / "reserved-view.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(f'CREATE VIEW "{reserved_name}" AS SELECT 1 AS value')
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert result.reason_codes == ("LEGACY_SCHEMA_MISMATCH",)
    assert _sha256(db_path) == before


def test_preflight_accepts_canonical_legacy_schema_without_mutation(tmp_path: Path):
    db_path = build_legacy_runtime_database(tmp_path / "legacy.db")
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.LEGACY_CANONICAL
    assert result.compatible is True
    assert result.user_version == 0
    assert result.reason_codes == ()
    assert _sha256(db_path) == before


def test_preflight_accepts_legacy_schema_with_canonical_optional_tables(tmp_path: Path):
    db_path = build_legacy_runtime_database(
        tmp_path / "legacy-optional.db",
        include_optional=True,
    )

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.LEGACY_CANONICAL
    assert result.compatible is True


def test_preflight_accepts_migrated_schema_read_only(tmp_path: Path):
    db_path = migrate_test_database(tmp_path / "migrated.db")
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.MIGRATED_COMPATIBLE
    assert result.compatible is True
    assert result.applied_versions == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    assert result.user_version == 10
    assert _sha256(db_path) == before


def test_preflight_accepts_schema_v5_before_entry_decision_migration(tmp_path: Path):
    db_path = tmp_path / "migrated-v5.db"
    MigrationRunner(
        db_path=db_path,
        migrations=discover_migrations()[:5],
        source_revision="test-v5",
    ).apply()
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.MIGRATED_COMPATIBLE
    assert result.compatible is True
    assert result.applied_versions == (1, 2, 3, 4, 5)
    assert result.user_version == 5
    assert _sha256(db_path) == before


def test_preflight_accepts_schema_v3_before_outcome_migrations(tmp_path: Path):
    db_path = tmp_path / "migrated-v3.db"
    MigrationRunner(
        db_path=db_path,
        migrations=discover_migrations()[:3],
        source_revision="test-v3",
    ).apply()
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.MIGRATED_COMPATIBLE
    assert result.compatible is True
    assert result.applied_versions == (1, 2, 3)
    assert result.user_version == 3
    assert _sha256(db_path) == before


def test_preflight_rejects_missing_required_legacy_table_before_write(tmp_path: Path):
    db_path = build_legacy_runtime_database(tmp_path / "partial.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE catalog_events")
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert "LEGACY_SCHEMA_MISMATCH" in result.reason_codes
    assert _sha256(db_path) == before

    with pytest.raises(MigrationPreflightError):
        require_migration_compatible(db_path=db_path)
    assert _sha256(db_path) == before


def test_preflight_rejects_hidden_generated_column_before_write(tmp_path: Path):
    db_path = build_legacy_runtime_database(tmp_path / "generated-column.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "ALTER TABLE lbank_catalog ADD COLUMN hidden_poison INTEGER "
            "GENERATED ALWAYS AS (0) VIRTUAL NOT NULL"
        )
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert result.reason_codes == ("LEGACY_SCHEMA_MISMATCH",)
    assert _sha256(db_path) == before


def test_preflight_rejects_desc_integer_primary_key_before_write(tmp_path: Path):
    db_path = build_legacy_runtime_database(tmp_path / "desc-primary-key.db")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            DROP TABLE catalog_events;
            CREATE TABLE catalog_events (
                id INTEGER PRIMARY KEY DESC,
                symbol TEXT,
                event_type TEXT,
                timestamp INTEGER
            );
            """
        )
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert result.reason_codes == ("LEGACY_SCHEMA_MISMATCH",)
    assert _sha256(db_path) == before


def test_preflight_rejects_nonzero_legacy_user_version_before_write(tmp_path: Path):
    db_path = build_legacy_runtime_database(tmp_path / "bad-version.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version=7")
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert "LEGACY_USER_VERSION_INVALID" in result.reason_codes
    assert _sha256(db_path) == before


def test_preflight_rejects_partial_migration_metadata_without_repair(tmp_path: Path):
    db_path = tmp_path / "partial-history.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version=1")
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert "MIGRATION_HISTORY_INVALID" in result.reason_codes
    assert _sha256(db_path) == before


def test_preflight_accepts_canonical_legacy_schema_with_only_migration_1_applied(
    tmp_path: Path,
):
    db_path = build_legacy_runtime_database(tmp_path / "legacy-v1.db")
    MigrationRunner(
        db_path=db_path,
        migrations=discover_migrations()[:1],
    ).apply()

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.MIGRATED_COMPATIBLE
    assert result.compatible is True
    assert result.applied_versions == (1,)
    assert result.user_version == 1


def test_preflight_validates_schema_with_migrations_1_and_2_applied(
    tmp_path: Path,
):
    db_path = tmp_path / "migrated-v2-drift.db"
    MigrationRunner(
        db_path=db_path,
        migrations=discover_migrations()[:2],
    ).apply()
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER lbank_signal_ledger_no_update")
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert result.reason_codes == ("MIGRATION_SCHEMA_MISMATCH",)
    assert _sha256(db_path) == before


def test_preflight_rejects_partial_managed_schema_before_migration_2(
    tmp_path: Path,
):
    db_path = tmp_path / "partial-v1.db"
    MigrationRunner(
        db_path=db_path,
        migrations=discover_migrations()[:1],
    ).apply()
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE lbank_catalog (symbol TEXT PRIMARY KEY)")
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert result.reason_codes == ("MIGRATION_SCHEMA_MISMATCH",)
    assert _sha256(db_path) == before


def test_preflight_rejects_managed_table_name_occupied_by_view_before_migration_2(
    tmp_path: Path,
):
    db_path = tmp_path / "managed-view-v1.db"
    MigrationRunner(
        db_path=db_path,
        migrations=discover_migrations()[:1],
    ).apply()
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE VIEW lbank_catalog AS SELECT 1 AS symbol")
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert result.reason_codes == ("MIGRATION_SCHEMA_MISMATCH",)
    assert _sha256(db_path) == before


def test_preflight_rejects_reserved_index_name_on_unknown_legacy_table(
    tmp_path: Path,
):
    db_path = build_legacy_runtime_database(tmp_path / "reserved-index.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE user_extension (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE INDEX idx_lbank_execution_queue ON user_extension(id)"
        )
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert result.reason_codes == ("LEGACY_SCHEMA_MISMATCH",)
    assert _sha256(db_path) == before


def test_preflight_rejects_case_variant_reserved_index_on_unknown_table(
    tmp_path: Path,
):
    db_path = build_legacy_runtime_database(tmp_path / "reserved-index-case.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE user_extension (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE INDEX IDX_LBANK_EXECUTION_QUEUE ON user_extension(id)"
        )
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert result.reason_codes == ("LEGACY_SCHEMA_MISMATCH",)
    assert _sha256(db_path) == before


@pytest.mark.parametrize(
    "collision_sql",
    [
        "CREATE TABLE db_readiness_probe (probe_id TEXT PRIMARY KEY)",
        "CREATE VIEW SCHEMA_MIGRATIONS AS SELECT 1 AS version",
        """
        CREATE TABLE user_extension (id INTEGER PRIMARY KEY);
        CREATE TRIGGER SCHEMA_MIGRATIONS_NO_UPDATE
        BEFORE UPDATE ON user_extension BEGIN SELECT 1; END;
        """,
        """
        CREATE TABLE user_extension (id INTEGER PRIMARY KEY);
        CREATE TRIGGER schema_migrations_no_delete
        BEFORE DELETE ON user_extension BEGIN SELECT 1; END;
        """,
    ],
)
def test_preflight_rejects_migration_infrastructure_name_collision_before_write(
    tmp_path: Path,
    collision_sql: str,
):
    db_path = build_legacy_runtime_database(tmp_path / "infra-collision.db")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(collision_sql)
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert result.reason_codes == ("LEGACY_SCHEMA_MISMATCH",)
    assert _sha256(db_path) == before


@pytest.mark.parametrize(
    ("canonical", "weakened"),
    [
        ("signal_id INTEGER NOT NULL UNIQUE,", "signal_id INTEGER NOT NULL,"),
        (
            "signal_id INTEGER NOT NULL UNIQUE,",
            "signal_id INTEGER NOT NULL UNIQUE ON CONFLICT REPLACE,",
        ),
        ("UNIQUE(bucket_started_at, symbol)", "CHECK(bucket_started_at >= 0)"),
        ("UNIQUE(snapshot_id, replay_version)", "CHECK(snapshot_id >= 0)"),
        (
            "UNIQUE (bucket_started_at, source, symbol)",
            "CHECK (bucket_started_at >= 0)",
        ),
        ("report_sha256 TEXT NOT NULL UNIQUE,", "report_sha256 TEXT NOT NULL,"),
        ("event_key TEXT NOT NULL UNIQUE,", "event_key TEXT NOT NULL,"),
    ],
)
def test_preflight_rejects_weakened_legacy_unique_constraints_before_write(
    tmp_path: Path,
    canonical: str,
    weakened: str,
):
    sql = _FIXTURE.read_text(encoding="utf-8")
    assert sql.count(canonical) == 1
    sql = sql.replace(canonical, weakened)

    db_path = tmp_path / "weakened.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(sql)
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert "LEGACY_SCHEMA_MISMATCH" in result.reason_codes
    assert _sha256(db_path) == before


def test_preflight_rejects_unexpected_legacy_unique_constraint_before_write(
    tmp_path: Path,
):
    sql = _FIXTURE.read_text(encoding="utf-8")
    marker = "trigger_data TEXT\n);"
    assert sql.count(marker) == 1
    sql = sql.replace(
        marker,
        "trigger_data TEXT,\n    UNIQUE(last_price)\n);",
    )

    db_path = tmp_path / "over-constrained.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(sql)
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.PARTIAL_OR_INCOMPATIBLE
    assert result.compatible is False
    assert "LEGACY_SCHEMA_MISMATCH" in result.reason_codes
    assert _sha256(db_path) == before


def test_preflight_accepts_schema_v9_before_runtime_performance_migration(tmp_path: Path):
    db_path = tmp_path / "migrated-v9.db"
    MigrationRunner(
        db_path=db_path,
        migrations=discover_migrations()[:9],
        source_revision="test-v9",
    ).apply()
    before = _sha256(db_path)

    result = classify_database(db_path=db_path)

    assert result.state is PreflightState.MIGRATED_COMPATIBLE
    assert result.compatible is True
    assert result.applied_versions == (1, 2, 3, 4, 5, 6, 7, 8, 9)
    assert result.user_version == 9
    assert _sha256(db_path) == before
