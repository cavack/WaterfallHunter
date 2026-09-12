from __future__ import annotations

import sqlite3

import pytest

from waterfallhunter.core.schema_contract import (
    CURRENT_RUNTIME_SCHEMA_VERSION,
    SchemaContractError,
    _sql_compact,
    _sql_structure,
    managed_runtime_table_names,
    require_managed_schema_connection,
    verify_managed_schema_connection,
)


EXPECTED_RUNTIME_TABLES = frozenset(
    {
        "lbank_catalog",
        "catalog_events",
        "lbank_signal_ledger",
        "lbank_signal_outcomes",
        "lbank_stage_lifecycle",
        "production_evidence_snapshots",
        "production_feature_replay_results_v2",
        "lbank_execution_observations",
        "lbank_execution_observation_history",
        "lbank_execution_decision_log",
        "operational_historical_outcome_datasets",
        "operational_historical_signal_outcomes",
        "provider_states",
        "signal_metadata",
        "signal_decisions",
        "domain_outbox_events",
        "lifecycle_v2_shadow_events",
        "entry_decision_events",
        "entry_notification_outbox",
        "entry_decision_advisories",
        "decision_outcome_capture",
        "decision_outcome_resolution",
    }
)


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_manifest_covers_exact_runtime_tables_and_version():
    assert CURRENT_RUNTIME_SCHEMA_VERSION == 10
    assert managed_runtime_table_names() == EXPECTED_RUNTIME_TABLES


def test_sql_normalization_preserves_case_inside_quoted_literals():
    assert _sql_compact("CHECK(status = 'PENDING')") != _sql_compact(
        "CHECK(status = 'pending')"
    )
    assert _sql_compact("check ( status = 'PENDING' )") == _sql_compact(
        "CHECK(status='PENDING')"
    )


def test_sql_structure_preserves_literal_identity_without_exposing_literal_sql():
    upper_structure, upper_literals = _sql_structure("CHECK(status = 'PENDING')")
    lower_structure, lower_literals = _sql_structure("CHECK(status = 'pending')")
    spoof_structure, spoof_literals = _sql_structure(
        "CHECK(note = 'CHECK(observational_only = 1)')"
    )

    assert upper_structure != lower_structure
    assert upper_literals == ("PENDING",)
    assert lower_literals == ("pending",)
    assert "check(observational_only=1)" not in spoof_structure
    assert spoof_literals == ("CHECK(observational_only = 1)",)


def test_sql_normalization_strips_comments_but_preserves_comment_markers_in_quotes():
    normalized = _sql_compact(
        "CHECK(x = 1) /* CHECK(observational_only = 1) */ "
        "-- RAISE(ABORT, 'spoof')\n"
        "CHECK(note = '/* retained */ -- retained')"
    )

    assert "check(observational_only=1)" not in normalized
    assert "raise(abort" not in normalized
    assert "'/* retained */ -- retained'" in normalized


def test_schema_verifier_rejects_missing_managed_table():
    conn = sqlite3.connect(":memory:")
    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"provider_states"}),
    )
    assert result.valid is False
    assert _codes(result) == {"TABLE_MISSING"}


def test_schema_verifier_rejects_extra_managed_column():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE provider_states (
            provider_id TEXT PRIMARY KEY,
            upstream_identity TEXT NOT NULL,
            status TEXT NOT NULL,
            failure_class TEXT NOT NULL,
            consecutive_failures INTEGER NOT NULL,
            circuit_open_until REAL NOT NULL,
            replacement_generation INTEGER NOT NULL,
            last_success_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            unexpected TEXT
        )
        """
    )
    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"provider_states"}),
    )
    assert "COLUMN_SET_MISMATCH" in _codes(result)


def test_schema_verifier_rejects_extra_generated_column_hidden_from_table_info():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE lbank_catalog (
            symbol TEXT PRIMARY KEY,
            last_price REAL,
            quote_volume REAL,
            is_meme BOOLEAN,
            scan_eligible BOOLEAN DEFAULT 0,
            status TEXT DEFAULT 'WATCH',
            first_seen_at INTEGER,
            last_added_at INTEGER,
            last_seen_at INTEGER,
            removed_at INTEGER,
            consecutive_missing_snapshots INTEGER DEFAULT 0,
            lifecycle_id INTEGER NOT NULL DEFAULT 1,
            trigger_data TEXT,
            hidden_poison INTEGER GENERATED ALWAYS AS (NULL) VIRTUAL NOT NULL
        )
        """
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_catalog"}),
    )

    assert "COLUMN_SET_MISMATCH" in _codes(result)


def test_schema_verifier_rejects_desc_integer_primary_key_without_rowid_alias():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE catalog_events (
            id INTEGER PRIMARY KEY DESC,
            symbol TEXT,
            event_type TEXT,
            timestamp INTEGER
        )
        """
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"catalog_events"}),
    )

    assert "COLUMN_CONSTRAINT_MISMATCH" in _codes(result)


def test_schema_verifier_rejects_wrong_primary_key():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE provider_states (
            provider_id TEXT NOT NULL,
            upstream_identity TEXT NOT NULL,
            status TEXT NOT NULL,
            failure_class TEXT NOT NULL,
            consecutive_failures INTEGER NOT NULL,
            circuit_open_until REAL NOT NULL,
            replacement_generation INTEGER NOT NULL,
            last_success_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"provider_states"}),
    )
    assert "COLUMN_CONSTRAINT_MISMATCH" in _codes(result)


def test_schema_verifier_rejects_wrong_named_index_columns():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE lbank_stage_lifecycle (
            symbol TEXT NOT NULL,
            lifecycle_id INTEGER NOT NULL,
            hype_seen_at INTEGER,
            damage_seen_at INTEGER,
            setup_seen_at INTEGER,
            setup_type TEXT,
            trigger_seen_at INTEGER,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (symbol, lifecycle_id)
        );
        CREATE INDEX idx_lbank_stage_lifecycle_updated
        ON lbank_stage_lifecycle(symbol);
        """
    )
    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_stage_lifecycle"}),
    )
    assert "INDEX_MISMATCH" in _codes(result)


@pytest.mark.parametrize(
    "index_clause",
    [
        "(updated_at) WHERE 0",
        "(updated_at COLLATE NOCASE)",
        "(updated_at DESC)",
    ],
)
def test_schema_verifier_rejects_noncanonical_named_index_structure(
    index_clause: str,
):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        f"""
        CREATE TABLE lbank_stage_lifecycle (
            symbol TEXT NOT NULL,
            lifecycle_id INTEGER NOT NULL,
            hype_seen_at INTEGER,
            damage_seen_at INTEGER,
            setup_seen_at INTEGER,
            setup_type TEXT,
            trigger_seen_at INTEGER,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY(symbol, lifecycle_id)
        );
        CREATE INDEX idx_lbank_stage_lifecycle_updated
        ON lbank_stage_lifecycle {index_clause};
        """
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_stage_lifecycle"}),
    )

    assert "INDEX_MISMATCH" in _codes(result)


def test_schema_verifier_rejects_noncanonical_column_collation():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE provider_states (
            provider_id TEXT COLLATE NOCASE PRIMARY KEY,
            upstream_identity TEXT NOT NULL,
            status TEXT NOT NULL,
            failure_class TEXT NOT NULL,
            consecutive_failures INTEGER NOT NULL,
            circuit_open_until REAL NOT NULL,
            replacement_generation INTEGER NOT NULL,
            last_success_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"provider_states"}),
    )

    assert "COLUMN_COLLATION_MISMATCH" in _codes(result)


def _create_signal_ledger_parent(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE lbank_signal_ledger (id INTEGER PRIMARY KEY)")


def _create_outcomes(
    conn: sqlite3.Connection,
    *,
    fk_target: str = "lbank_signal_ledger",
    observational_check: str = "observational_only = 1",
    immutable_update: bool = True,
    unique_signal_id: bool = True,
    unique_conflict_policy: str | None = None,
    extra_table_check: str | None = None,
) -> None:
    update_raise = (
        "SELECT RAISE(ABORT, 'lbank_signal_outcomes is immutable');"
        if immutable_update
        else "SELECT RAISE(IGNORE);"
    )
    signal_id_constraint = " UNIQUE" if unique_signal_id else ""
    conflict_clause = (
        f" ON CONFLICT {unique_conflict_policy}"
        if unique_conflict_policy is not None
        else ""
    )
    extra_check_clause = (
        f", CHECK ({extra_table_check})" if extra_table_check is not None else ""
    )
    conn.executescript(
        f"""
        CREATE TABLE lbank_signal_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL{signal_id_constraint}{conflict_clause},
            symbol TEXT NOT NULL,
            outcome_status TEXT NOT NULL,
            signal_triggered_at INTEGER NOT NULL,
            observation_started_at INTEGER,
            observation_ended_at INTEGER,
            horizon_seconds INTEGER NOT NULL,
            price_source TEXT NOT NULL,
            source_exchange TEXT,
            source_mapped_symbol TEXT,
            first_tp1_at INTEGER,
            first_tp2_at INTEGER,
            first_stop_at INTEGER,
            min_price REAL,
            max_price REAL,
            mfe_pct REAL,
            mae_pct REAL,
            observed_candles INTEGER NOT NULL,
            expected_candles INTEGER NOT NULL,
            details_json TEXT NOT NULL,
            observational_only INTEGER NOT NULL DEFAULT 1 CHECK ({observational_check}),
            trade_eligible INTEGER CHECK (trade_eligible IS NULL),
            resolved_at INTEGER NOT NULL{extra_check_clause},
            FOREIGN KEY(signal_id) REFERENCES {fk_target}(id)
        );
        CREATE INDEX idx_lbank_signal_outcomes_status
        ON lbank_signal_outcomes(outcome_status, resolved_at);
        CREATE TRIGGER lbank_signal_outcomes_no_update
        BEFORE UPDATE ON lbank_signal_outcomes
        BEGIN
            {update_raise}
        END;
        CREATE TRIGGER lbank_signal_outcomes_no_delete
        BEFORE DELETE ON lbank_signal_outcomes
        BEGIN
            SELECT RAISE(ABORT, 'lbank_signal_outcomes is immutable');
        END;
        """
    )


def test_schema_verifier_rejects_wrong_foreign_key_target():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE wrong_parent (id INTEGER PRIMARY KEY)")
    _create_outcomes(conn, fk_target="wrong_parent")
    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )
    assert "FOREIGN_KEY_MISMATCH" in _codes(result)


def test_schema_verifier_rejects_missing_critical_check():
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(conn, observational_check="observational_only IN (0, 1)")
    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )
    assert "CHECK_MISSING" in _codes(result)


def test_schema_verifier_rejects_unexpected_check_on_table_without_canonical_checks():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE lbank_catalog (
            symbol TEXT PRIMARY KEY,
            last_price REAL,
            quote_volume REAL,
            is_meme BOOLEAN,
            scan_eligible BOOLEAN DEFAULT 0,
            status TEXT DEFAULT 'WATCH',
            first_seen_at INTEGER,
            last_added_at INTEGER,
            last_seen_at INTEGER,
            removed_at INTEGER,
            consecutive_missing_snapshots INTEGER DEFAULT 0,
            lifecycle_id INTEGER NOT NULL DEFAULT 1,
            trigger_data TEXT,
            CHECK(symbol = 'BTCUSDT')
        )
        """
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_catalog"}),
    )

    assert "CHECK_UNEXPECTED" in _codes(result)


def test_schema_verifier_rejects_extra_check_beside_canonical_checks():
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(conn, extra_table_check="symbol = 'BTCUSDT'")

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert "CHECK_MISSING" not in _codes(result)
    assert "CHECK_UNEXPECTED" in _codes(result)


def test_schema_verifier_rejects_check_fragment_present_only_in_comment():
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(
        conn,
        observational_check=(
            "observational_only IN (0, 1) "
            "/* CHECK(observational_only = 1) */"
        ),
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert "CHECK_MISSING" in _codes(result)


def test_schema_verifier_rejects_check_fragment_present_only_in_string_literal():
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(
        conn,
        observational_check=(
            "observational_only IN (0, 1) AND "
            "'CHECK(observational_only = 1)' != ''"
        ),
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert "CHECK_MISSING" in _codes(result)


def test_schema_verifier_rejects_abort_guard_present_only_in_comment():
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(conn)
    conn.execute("DROP TRIGGER lbank_signal_outcomes_no_update")
    conn.executescript(
        """
        CREATE TRIGGER lbank_signal_outcomes_no_update
        BEFORE UPDATE ON lbank_signal_outcomes
        BEGIN
            /* SELECT RAISE(ABORT, 'lbank_signal_outcomes is immutable'); */
            SELECT RAISE(IGNORE);
        END;
        """
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert "TRIGGER_MISMATCH" in _codes(result)


def test_schema_verifier_rejects_abort_guard_present_only_in_string_literal():
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(conn)
    conn.execute("DROP TRIGGER lbank_signal_outcomes_no_update")
    conn.executescript(
        """
        CREATE TRIGGER lbank_signal_outcomes_no_update
        BEFORE UPDATE ON lbank_signal_outcomes
        BEGIN
            SELECT 'raise(abort, ''lbank_signal_outcomes is immutable'')';
            SELECT RAISE(IGNORE);
        END;
        """
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert "TRIGGER_MISMATCH" in _codes(result)


@pytest.mark.parametrize(
    ("trigger_name", "event"),
    [
        ("lbank_signal_outcomes_no_update", "UPDATE"),
        ("lbank_signal_outcomes_no_delete", "DELETE"),
    ],
)
def test_schema_verifier_rejects_conditional_immutable_trigger(
    trigger_name: str,
    event: str,
):
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(conn)
    conn.execute(f"DROP TRIGGER {trigger_name}")
    conn.executescript(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE {event} ON lbank_signal_outcomes
        WHEN 0
        BEGIN
            SELECT RAISE(ABORT, 'lbank_signal_outcomes is immutable');
        END;
        """
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert "TRIGGER_MISMATCH" in _codes(result)


def test_schema_verifier_rejects_abort_guard_in_unreachable_trigger_expression():
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(conn)
    conn.execute("DROP TRIGGER lbank_signal_outcomes_no_update")
    conn.executescript(
        """
        CREATE TRIGGER lbank_signal_outcomes_no_update
        BEFORE UPDATE ON lbank_signal_outcomes
        BEGIN
            SELECT CASE WHEN 0
                THEN RAISE(ABORT, 'lbank_signal_outcomes is immutable')
                ELSE 1
            END;
        END;
        """
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert "TRIGGER_MISMATCH" in _codes(result)


@pytest.mark.parametrize(
    ("opening_quote", "closing_quote"),
    [("\"", "\""), ("`", "`"), ("[", "]")],
)
@pytest.mark.parametrize(
    ("trigger_name", "event"),
    [
        ("lbank_signal_outcomes_no_update", "UPDATE"),
        ("lbank_signal_outcomes_no_delete", "DELETE"),
    ],
)
def test_schema_verifier_accepts_semantically_canonical_quoted_trigger_identifiers(
    opening_quote: str,
    closing_quote: str,
    trigger_name: str,
    event: str,
):
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(conn)
    conn.execute(f"DROP TRIGGER {trigger_name}")
    quoted_trigger = f"{opening_quote}{trigger_name}{closing_quote}"
    quoted_table = (
        f"{opening_quote}lbank_signal_outcomes{closing_quote}"
    )
    conn.executescript(
        f"""
        CREATE TRIGGER {quoted_trigger}
        BEFORE {event} ON {quoted_table}
        BEGIN
            SELECT RAISE(ABORT, 'lbank_signal_outcomes is immutable');
        END;
        """
    )

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert "TRIGGER_MISMATCH" not in _codes(result)


def test_schema_verifier_rejects_non_aborting_immutable_trigger():
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(conn, immutable_update=False)
    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )
    assert "TRIGGER_MISMATCH" in _codes(result)


def test_schema_verifier_rejects_missing_unique_key():
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(conn, unique_signal_id=False)

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert "UNIQUE_KEY_MISMATCH" in _codes(result)


@pytest.mark.parametrize(
    "conflict_policy",
    ("REPLACE", "IGNORE", "FAIL", "ROLLBACK"),
)
def test_schema_verifier_rejects_noncanonical_unique_conflict_policy(
    conflict_policy: str,
):
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(conn, unique_conflict_policy=conflict_policy)

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert "UNIQUE_KEY_MISMATCH" in _codes(result)


def test_schema_verifier_accepts_explicit_abort_unique_conflict_policy():
    conn = sqlite3.connect(":memory:")
    _create_signal_ledger_parent(conn)
    _create_outcomes(conn, unique_conflict_policy="ABORT")

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"lbank_signal_outcomes"}),
    )

    assert "UNIQUE_KEY_MISMATCH" not in _codes(result)


def test_schema_verifier_reports_but_preserves_unknown_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE user_extension (id INTEGER PRIMARY KEY)")
    before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='user_extension'"
    ).fetchone()[0]

    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset(),
    )

    after = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='user_extension'"
    ).fetchone()[0]
    assert result.valid is True
    assert result.unknown_user_objects == ("user_extension",)
    assert before == after


def test_schema_verifier_can_allow_absent_optional_legacy_tables():
    conn = sqlite3.connect(":memory:")
    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset({"provider_states"}),
        allow_missing_tables=frozenset({"provider_states"}),
    )
    assert result.valid is True
    assert result.issues == ()


def test_schema_verifier_rejects_user_version_mismatch():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA user_version=1")
    result = verify_managed_schema_connection(
        conn,
        required_tables=frozenset(),
        check_user_version=2,
    )
    assert result.valid is False
    assert _codes(result) == {"USER_VERSION_MISMATCH"}


def test_require_managed_schema_connection_raises_typed_error():
    conn = sqlite3.connect(":memory:")
    with pytest.raises(SchemaContractError):
        require_managed_schema_connection(
            conn,
            required_tables=frozenset({"provider_states"}),
        )
