import sqlite3

from schema_test_support import migrate_test_database
from waterfallhunter.core.db import DBAdapter


SYMBOL = "RACE/USDT:USDT"


def _db(tmp_path) -> DBAdapter:
    db_path = tmp_path / "state.db"
    migrate_test_database(db_path)
    return DBAdapter(db_path=str(db_path))


def _candidate(
    *,
    scan_eligible: bool,
) -> dict:
    return {
        "last_price": 0.01,
        "quote_volume": 3_000_000.0,
        "is_meme": False,
        "scan_eligible": scan_eligible,
    }


def _row(
    db: DBAdapter,
):
    with sqlite3.connect(
        db.db_path,
    ) as conn:
        return conn.execute(
            """
            SELECT
                status,
                scan_eligible,
                trigger_data,
                lifecycle_id
            FROM lbank_catalog
            WHERE symbol = ?
            """,
            (SYMBOL,),
        ).fetchone()


def test_candidate_state_update_still_persists_for_scan_eligible_symbol(
    tmp_path,
):
    db = _db(tmp_path)

    db.update_candidates(
        {
            SYMBOL: _candidate(
                scan_eligible=True,
            )
        }
    )

    assert (
        db.update_candidate_state(
            SYMBOL,
            "FUEL-RICH",
            {
                "score": 42,
            },
        )
        is True
    )

    row = _row(
        db
    )

    assert row is not None
    assert row[0] == "FUEL-RICH"
    assert row[1] == 1
    assert '"score": 42' in row[2]


def test_inflight_state_write_is_benignly_skipped_after_scan_ineligible_transition(
    tmp_path,
):
    db = _db(tmp_path)

    db.update_candidates(
        {
            SYMBOL: _candidate(
                scan_eligible=True,
            )
        }
    )

    assert (
        db.update_candidate_state(
            SYMBOL,
            "PRE-TRIGGER",
        )
        is True
    )

    db.update_candidates(
        {
            SYMBOL: _candidate(
                scan_eligible=False,
            )
        }
    )

    row_before = _row(
        db
    )

    assert row_before is not None
    assert row_before[0] == "WATCH"
    assert row_before[1] == 0
    assert row_before[3] == 2

    assert (
        db.update_candidate_state(
            SYMBOL,
            "FUEL-RICH",
            {
                "stale_analysis": True,
            },
        )
        is True
    )

    row_after = _row(
        db
    )

    assert row_after is not None
    assert row_after[0] == "WATCH"
    assert row_after[1] == 0
    assert row_after[2] == "{}"


def test_each_eligibility_flip_starts_a_new_lifecycle(tmp_path):
    db = _db(tmp_path)
    db.update_candidates({SYMBOL: _candidate(scan_eligible=True)})
    assert _row(db)[3] == 1

    db.update_candidates({SYMBOL: _candidate(scan_eligible=False)})
    assert _row(db)[3] == 2

    db.update_candidates({SYMBOL: _candidate(scan_eligible=True)})
    row = _row(db)
    assert row[0] == "WATCH"
    assert row[1] == 1
    assert row[3] == 3


def test_inflight_state_write_cannot_resurrect_removed_symbol(
    tmp_path,
):
    db = _db(tmp_path)

    db.update_candidates(
        {
            SYMBOL: _candidate(
                scan_eligible=True,
            )
        }
    )

    db.mark_removed(
        SYMBOL
    )

    assert (
        db.update_candidate_state(
            SYMBOL,
            "ARMED",
        )
        is True
    )

    row = _row(
        db
    )

    assert row is not None
    assert row[0] == "REMOVED"
    assert row[1] == 0
    assert row[2] == "{}"


def test_missing_catalogue_row_remains_a_real_state_persistence_failure(
    tmp_path,
):
    db = _db(tmp_path)

    assert (
        db.update_candidate_state(
            "MISSING/USDT:USDT",
            "FUEL-RICH",
        )
        is False
    )


def test_atomic_trigger_transition_rejects_stale_expected_state(
    tmp_path,
):
    db = _db(tmp_path)

    db.update_candidates(
        {
            SYMBOL: _candidate(
                scan_eligible=True,
            )
        }
    )

    assert db.update_candidate_state(
        SYMBOL,
        "ARMED",
    ) is True

    assert db.transition_candidate_state(
        SYMBOL,
        "PRE-TRIGGER",
        "TRIGGERED",
        {"stale": True},
    ) is False

    row = _row(db)
    assert row is not None
    assert row[0] == "ARMED"
    assert row[2] == "{}"


def test_atomic_trigger_transition_rejects_scan_ineligible_symbol(
    tmp_path,
):
    db = _db(tmp_path)

    db.update_candidates(
        {
            SYMBOL: _candidate(
                scan_eligible=True,
            )
        }
    )

    assert db.update_candidate_state(
        SYMBOL,
        "ARMED",
    ) is True

    db.update_candidates(
        {
            SYMBOL: _candidate(
                scan_eligible=False,
            )
        }
    )

    assert db.transition_candidate_state(
        SYMBOL,
        "ARMED",
        "TRIGGERED",
        {"stale": True},
    ) is False

    row = _row(db)
    assert row is not None
    assert row[0] == "WATCH"
    assert row[1] == 0
    assert row[2] == "{}"
