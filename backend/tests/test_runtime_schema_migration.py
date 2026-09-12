from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from schema_test_support import (
    build_legacy_runtime_database,
    business_row_hashes,
    migrate_test_database,
)
from waterfallhunter.core.migration_preflight import (
    PreflightState,
    require_migration_compatible,
)
from waterfallhunter.core.migrations import MigrationRunner, discover_migrations
from waterfallhunter.core.schema_contract import (
    CURRENT_RUNTIME_SCHEMA_VERSION,
    verify_managed_schema,
)


_PRESERVED_TABLES = (
    "lbank_catalog",
    "catalog_events",
    "lbank_signal_ledger",
    "lbank_signal_outcomes",
    "lbank_stage_lifecycle",
    "production_evidence_snapshots",
    "production_feature_replay_results_v2",
    "lbank_execution_decision_log",
    "operational_historical_outcome_datasets",
    "operational_historical_signal_outcomes",
)


def test_packaged_migrations_include_runtime_baseline():
    migrations = discover_migrations()

    assert [item.version for item in migrations] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert migrations[1].filename == "0002_runtime_schema_baseline.sql"


def test_clean_install_reaches_current_runtime_schema(tmp_path: Path):
    db_path = tmp_path / "registry.db"
    runner = MigrationRunner(db_path=db_path, source_revision="test")

    assert runner.apply() == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    assert runner.verify() == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)

    result = verify_managed_schema(
        db_path,
        check_user_version=CURRENT_RUNTIME_SCHEMA_VERSION,
    )
    assert result.valid is True, result.issues

    assert runner.apply() == ()


def test_shared_test_migration_helper_rejects_incompatible_database_before_write(
    tmp_path: Path,
):
    db_path = tmp_path / "incompatible.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")

    with pytest.raises(RuntimeError):
        migrate_test_database(db_path)

    with sqlite3.connect(db_path) as conn:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        migration_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        unrelated = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='unrelated'"
        ).fetchone()

    assert user_version == 0
    assert migration_table is None
    assert unrelated is not None


def test_canonical_legacy_adoption_preserves_business_and_evidence_rows(tmp_path: Path):
    db_path = build_legacy_runtime_database(tmp_path / "legacy.db")
    before_hashes = business_row_hashes(db_path, _PRESERVED_TABLES)

    preflight = require_migration_compatible(db_path=db_path)
    assert preflight.state is PreflightState.LEGACY_CANONICAL

    runner = MigrationRunner(
        db_path=db_path,
        source_revision="test-legacy-adoption",
    )
    assert runner.apply() == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    assert runner.verify() == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)

    after_hashes = business_row_hashes(db_path, _PRESERVED_TABLES)
    assert after_hashes == before_hashes

    schema = verify_managed_schema(
        db_path,
        check_user_version=CURRENT_RUNTIME_SCHEMA_VERSION,
    )
    assert schema.valid is True, schema.issues

    with sqlite3.connect(db_path) as conn:
        history = conn.execute(
            "SELECT version, source_revision FROM schema_migrations ORDER BY version"
        ).fetchall()
        optional_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?)",
                (
                    "lbank_execution_observations",
                    "lbank_execution_observation_history",
                    "provider_states",
                ),
            ).fetchall()
        }

    assert history == [
        (1, "test-legacy-adoption"),
        (2, "test-legacy-adoption"),
        (3, "test-legacy-adoption"),
        (4, "test-legacy-adoption"),
        (5, "test-legacy-adoption"),
            (6, "test-legacy-adoption"),
            (7, "test-legacy-adoption"),
            (8, "test-legacy-adoption"),
            (9, "test-legacy-adoption"),
            (10, "test-legacy-adoption"),
        ]
    assert optional_tables == {
        "lbank_execution_observations",
        "lbank_execution_observation_history",
        "provider_states",
    }
    assert runner.apply() == ()


def test_legacy_adoption_with_existing_optional_tables_is_idempotent(tmp_path: Path):
    db_path = build_legacy_runtime_database(
        tmp_path / "legacy-optional.db",
        include_optional=True,
    )
    before_hashes = business_row_hashes(db_path, _PRESERVED_TABLES)

    assert (
        require_migration_compatible(db_path=db_path).state
        is PreflightState.LEGACY_CANONICAL
    )
    runner = MigrationRunner(db_path=db_path, source_revision="test")
    assert runner.apply() == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)
    assert business_row_hashes(db_path, _PRESERVED_TABLES) == before_hashes
    assert runner.apply() == ()


def test_schema_v9_upgrades_only_runtime_performance_migration(tmp_path: Path):
    db_path = tmp_path / "runtime-v9.db"
    first_nine = discover_migrations()[:9]
    MigrationRunner(
        db_path=db_path,
        migrations=first_nine,
        source_revision="test-v9",
    ).apply()

    runner = MigrationRunner(db_path=db_path, source_revision="test-v10")
    assert runner.apply() == (10,)
    assert runner.verify() == (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)

    schema = verify_managed_schema(
        db_path,
        check_user_version=CURRENT_RUNTIME_SCHEMA_VERSION,
    )
    assert schema.valid is True, schema.issues
