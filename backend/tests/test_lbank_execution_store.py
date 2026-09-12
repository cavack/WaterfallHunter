import sqlite3

from schema_test_support import migrate_test_database
from waterfallhunter.core.db import (
    DBAdapter,
)
from waterfallhunter.core.lbank_execution_store import (
    EXECUTION_STATUS_OBSERVED,
    EXECUTION_STATUS_UNAVAILABLE,
    LBankExecutionStore,
)


def seed_catalog(
    db_path,
):
    migrate_test_database(db_path)
    db = DBAdapter(
        str(db_path)
    )

    db.update_candidates(
        {
            "MEME/USDT:USDT": {
                "last_price": 0.1,
                "quote_volume": 100_000,
                "is_meme": True,
                "scan_eligible": False,
            },
            "LOWVOL/USDT:USDT": {
                "last_price": 0.2,
                "quote_volume": 50_000,
                "is_meme": False,
                "scan_eligible": False,
            },
            "ACTIVE/USDT:USDT": {
                "last_price": 0.3,
                "quote_volume": 5_000_000,
                "is_meme": False,
                "scan_eligible": True,
            },
            "TOOEXPENSIVE/USDT:USDT": {
                "last_price": 2.0,
                "quote_volume": 10_000_000,
                "is_meme": True,
                "scan_eligible": False,
            },
        }
    )

    return db


def successful_packet(
    *,
    observed_at=1000.0,
    spread_pct=0.05,
    cost_25=0.05,
    cost_50=0.06,
    cost_100=0.08,
):
    return {
        "available": True,
        "symbol": "MEME/USDT:USDT",
        "source_exchange": "lbank",
        "observed_at": observed_at,
        "spread_pct": spread_pct,
        "depth": {
            "bounded": {
                "10": {
                    "minimum_side_depth_usdt": 100.0,
                },
                "25": {
                    "minimum_side_depth_usdt": 250.0,
                },
                "50": {
                    "minimum_side_depth_usdt": 500.0,
                },
                "100": {
                    "minimum_side_depth_usdt": 1000.0,
                },
            }
        },
        "execution": {
            "25": {
                "effective_crossing_cost_pct": cost_25,
            },
            "50": {
                "effective_crossing_cost_pct": cost_50,
            },
            "100": {
                "effective_crossing_cost_pct": cost_100,
            },
        },
    }


def test_execution_store_is_additive_and_does_not_change_catalog_state(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    assert (
        store.record_observation(
            "MEME/USDT:USDT",
            successful_packet(),
        )
        is True
    )

    with sqlite3.connect(
        str(db_path)
    ) as conn:
        row = conn.execute(
            """
            SELECT
                scan_eligible,
                status
            FROM lbank_catalog
            WHERE symbol = ?
            """,
            (
                "MEME/USDT:USDT",
            ),
        ).fetchone()

    assert row == (
        0,
        "WATCH",
    )


def test_successful_observation_persists_execution_measurements(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    store.record_observation(
        "MEME/USDT:USDT",
        successful_packet(),
    )

    row = store.get_observation(
        "MEME/USDT:USDT"
    )

    assert row is not None

    assert (
        row["observation_status"]
        == EXECUTION_STATUS_OBSERVED
    )

    assert (
        row["observed_at"]
        == 1000.0
    )

    assert (
        row["spread_pct"]
        == 0.05
    )

    assert (
        row["cost_25_pct"]
        == 0.05
    )

    assert (
        row["cost_50_pct"]
        == 0.06
    )

    assert (
        row["cost_100_pct"]
        == 0.08
    )

    assert (
        row[
            "depth_25bps_min_usdt"
        ]
        == 250.0
    )

    assert (
        row["failures"]
        == 0
    )

    assert (
        row["payload"][
            "available"
        ]
        is True
    )


def test_unavailable_observation_increments_failure_counter(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    packet = {
        "available": False,
        "symbol": "MEME/USDT:USDT",
        "reason": "rate limited",
    }

    assert (
        store.record_observation(
            "MEME/USDT:USDT",
            packet,
        )
        is True
    )

    first = store.get_observation(
        "MEME/USDT:USDT"
    )

    assert (
        first["observation_status"]
        == EXECUTION_STATUS_UNAVAILABLE
    )

    assert (
        first["failures"]
        == 1
    )

    assert (
        first["reason"]
        == "rate limited"
    )

    store.record_observation(
        "MEME/USDT:USDT",
        packet,
    )

    second = store.get_observation(
        "MEME/USDT:USDT"
    )

    assert (
        second["failures"]
        == 2
    )


def test_success_after_failure_resets_failure_counter(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    store.record_observation(
        "MEME/USDT:USDT",
        {
            "available": False,
            "reason": "temporary failure",
        },
    )

    store.record_observation(
        "MEME/USDT:USDT",
        successful_packet(),
    )

    row = store.get_observation(
        "MEME/USDT:USDT"
    )

    assert (
        row["observation_status"]
        == EXECUTION_STATUS_OBSERVED
    )

    assert (
        row["failures"]
        == 0
    )

    assert (
        row["reason"]
        is None
    )


def test_queue_has_no_volume_floor(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    queue = store.get_queue(
        limit=20,
        now=1000.0,
    )

    symbols = {
        row["symbol"]
        for row in queue
    }

    assert (
        "MEME/USDT:USDT"
        in symbols
    )

    assert (
        "LOWVOL/USDT:USDT"
        in symbols
    )

    assert (
        "ACTIVE/USDT:USDT"
        in symbols
    )

    assert (
        "TOOEXPENSIVE/USDT:USDT"
        not in symbols
    )


def test_queue_does_not_require_meme_classification(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    queue = store.get_queue(
        limit=20,
        now=1000.0,
    )

    rows = {
        row["symbol"]: row
        for row in queue
    }

    assert (
        "LOWVOL/USDT:USDT"
        in rows
    )

    assert (
        rows[
            "LOWVOL/USDT:USDT"
        ][
            "is_meme"
        ]
        == 0
    )


def test_queue_respects_next_check_at(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    packet = successful_packet()

    store.record_observation(
        "MEME/USDT:USDT",
        packet,
        next_check_at=2000.0,
    )

    early = store.get_queue(
        limit=20,
        now=1500.0,
    )

    early_symbols = {
        row["symbol"]
        for row in early
    }

    assert (
        "MEME/USDT:USDT"
        not in early_symbols
    )

    later = store.get_queue(
        limit=20,
        now=2001.0,
    )

    later_symbols = {
        row["symbol"]
        for row in later
    }

    assert (
        "MEME/USDT:USDT"
        in later_symbols
    )


def test_never_observed_contracts_are_prioritized_before_observed_contracts(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    store.record_observation(
        "ACTIVE/USDT:USDT",
        successful_packet(),
    )

    queue = store.get_queue(
        limit=20,
        now=2000.0,
    )

    symbols = [
        row["symbol"]
        for row in queue
    ]

    assert (
        symbols.index(
            "MEME/USDT:USDT"
        )
        < symbols.index(
            "ACTIVE/USDT:USDT"
        )
    )

    assert (
        symbols.index(
            "LOWVOL/USDT:USDT"
        )
        < symbols.index(
            "ACTIVE/USDT:USDT"
        )
    )


def test_status_counts_are_observational_only(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    store.ensure_symbol(
        "MEME/USDT:USDT"
    )

    store.record_observation(
        "ACTIVE/USDT:USDT",
        successful_packet(),
    )

    store.record_observation(
        "LOWVOL/USDT:USDT",
        {
            "available": False,
            "reason": "unavailable",
        },
    )

    counts = (
        store.count_statuses()
    )

    assert (
        counts["UNKNOWN"]
        == 1
    )

    assert (
        counts["OBSERVED"]
        == 1
    )

    assert (
        counts["UNAVAILABLE"]
        == 1
    )


def test_ensure_symbol_does_not_create_history(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    store.ensure_symbol(
        "MEME/USDT:USDT"
    )

    assert (
        store.count_history()
        == 0
    )


def test_ensure_symbols_does_not_create_history(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    store.ensure_symbols(
        [
            "MEME/USDT:USDT",
            "LOWVOL/USDT:USDT",
        ]
    )

    assert (
        store.count_history()
        == 0
    )


def test_successful_observation_appends_history(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    assert (
        store.record_observation(
            "MEME/USDT:USDT",
            successful_packet(),
        )
        is True
    )

    history = store.get_history(
        "MEME/USDT:USDT"
    )

    assert len(
        history
    ) == 1

    row = history[0]

    assert (
        row["observation_status"]
        == EXECUTION_STATUS_OBSERVED
    )

    assert (
        row["observed_at"]
        == 1000.0
    )

    assert (
        row["spread_pct"]
        == 0.05
    )

    assert (
        row["cost_100_pct"]
        == 0.08
    )

    assert (
        row["depth_25bps_min_usdt"]
        == 250.0
    )

    assert (
        row["payload"]["available"]
        is True
    )


def test_unavailable_observation_appends_history(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    assert (
        store.record_observation(
            "MEME/USDT:USDT",
            {
                "available": False,
                "reason": "rate limited",
            },
        )
        is True
    )

    history = store.get_history(
        "MEME/USDT:USDT"
    )

    assert len(
        history
    ) == 1

    assert (
        history[0]["observation_status"]
        == EXECUTION_STATUS_UNAVAILABLE
    )

    assert (
        history[0]["reason"]
        == "rate limited"
    )


def test_repeated_observations_keep_one_latest_row_and_multiple_history_rows(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    first_packet = successful_packet(
        observed_at=1000.0,
        spread_pct=0.05,
        cost_25=0.05,
        cost_50=0.06,
        cost_100=0.08,
    )

    second_packet = successful_packet(
        observed_at=2000.0,
        spread_pct=0.10,
        cost_25=0.11,
        cost_50=0.12,
        cost_100=0.15,
    )

    store.record_observation(
        "MEME/USDT:USDT",
        first_packet,
    )

    store.record_observation(
        "MEME/USDT:USDT",
        second_packet,
    )

    with sqlite3.connect(
        str(db_path)
    ) as conn:
        latest_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM lbank_execution_observations
            WHERE symbol = ?
            """,
            (
                "MEME/USDT:USDT",
            ),
        ).fetchone()[0]

    assert (
        latest_count
        == 1
    )

    assert (
        store.count_history(
            "MEME/USDT:USDT"
        )
        == 2
    )

    latest = store.get_observation(
        "MEME/USDT:USDT"
    )

    assert (
        latest["observed_at"]
        == 2000.0
    )

    assert (
        latest["spread_pct"]
        == 0.10
    )

    assert (
        latest["cost_100_pct"]
        == 0.15
    )


def test_history_preserves_each_observation_and_time_order(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    store.record_observation(
        "MEME/USDT:USDT",
        successful_packet(
            observed_at=1000.0,
            spread_pct=0.05,
            cost_100=0.08,
        ),
    )

    store.record_observation(
        "MEME/USDT:USDT",
        successful_packet(
            observed_at=2000.0,
            spread_pct=0.20,
            cost_100=0.30,
        ),
    )

    history = store.get_history(
        "MEME/USDT:USDT"
    )

    assert [
        row["observed_at"]
        for row in history
    ] == [
        1000.0,
        2000.0,
    ]

    assert [
        row["spread_pct"]
        for row in history
    ] == [
        0.05,
        0.20,
    ]

    assert [
        row["cost_100_pct"]
        for row in history
    ] == [
        0.08,
        0.30,
    ]


def test_history_does_not_change_catalog_state(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    store.record_observation(
        "MEME/USDT:USDT",
        successful_packet(),
    )

    store.record_observation(
        "MEME/USDT:USDT",
        successful_packet(
            observed_at=2000.0,
        ),
    )

    with sqlite3.connect(
        str(db_path)
    ) as conn:
        row = conn.execute(
            """
            SELECT
                scan_eligible,
                status
            FROM lbank_catalog
            WHERE symbol = ?
            """,
            (
                "MEME/USDT:USDT",
            ),
        ).fetchone()

    assert row == (
        0,
        "WATCH",
    )


def test_latest_and_history_are_atomic_when_history_insert_fails(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    with sqlite3.connect(
        str(db_path)
    ) as conn:
        conn.execute(
            """
            CREATE TRIGGER
            fail_execution_history_insert
            BEFORE INSERT
            ON lbank_execution_observation_history
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'forced history failure'
                );
            END
            """
        )

    result = store.record_observation(
        "MEME/USDT:USDT",
        successful_packet(),
    )

    assert (
        result
        is False
    )

    assert (
        store.get_observation(
            "MEME/USDT:USDT"
        )
        is None
    )

    assert (
        store.count_history(
            "MEME/USDT:USDT"
        )
        == 0
    )
