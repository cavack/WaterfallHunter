import sqlite3

from schema_test_support import migrate_test_database
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.stage_lifecycle import StageLifecycleStore


SYMBOL = "CHAIN/USDT:USDT"


def _candidate(scan_eligible: bool = True) -> dict:
    return {
        "last_price": 0.01,
        "quote_volume": 3_000_000.0,
        "is_meme": False,
        "scan_eligible": scan_eligible,
    }


def _db(tmp_path) -> DBAdapter:
    db_path = tmp_path / "chain.db"
    migrate_test_database(db_path)
    return DBAdapter(str(db_path))


def _lifecycle_id(db: DBAdapter) -> int:
    with sqlite3.connect(db.db_path) as conn:
        row = conn.execute(
            "SELECT lifecycle_id FROM lbank_catalog WHERE symbol = ?",
            (SYMBOL,),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _stages(**overrides) -> dict:
    packet = {
        "hype": False,
        "damage": False,
        "setup": False,
        "setup_type": None,
        "trigger": False,
        "passed": False,
    }
    packet.update(overrides)
    return packet


def test_stage_chain_accumulates_in_order_across_evaluations(tmp_path):
    db = _db(tmp_path)
    db.update_candidates({SYMBOL: _candidate()})
    lifecycle_id = _lifecycle_id(db)
    store = StageLifecycleStore(db.db_path)

    hype = store.advance(SYMBOL, lifecycle_id, _stages(hype=True), observed_at=1000)
    damage = store.advance(SYMBOL, lifecycle_id, _stages(damage=True), observed_at=1060)
    setup = store.advance(
        SYMBOL,
        lifecycle_id,
        _stages(setup=True, setup_type="breakdown"),
        observed_at=1120,
    )
    trigger = store.advance(SYMBOL, lifecycle_id, _stages(trigger=True), observed_at=1180)

    assert hype["confirmed"] == {
        "hype": True,
        "damage": False,
        "setup": False,
        "trigger": False,
        "passed": False,
    }
    assert damage["confirmed"]["damage"] is True
    assert setup["confirmed"]["setup"] is True
    assert setup["setup_type"] == "breakdown"
    assert trigger["confirmed"]["trigger"] is True
    assert trigger["confirmed"]["passed"] is True
    assert trigger["observational_only"] is False
    assert trigger["hard_gating_allowed"] is True


def test_stage_chain_rejects_out_of_order_progress(tmp_path):
    db = _db(tmp_path)
    db.update_candidates({SYMBOL: _candidate()})
    store = StageLifecycleStore(db.db_path)

    result = store.advance(
        SYMBOL,
        _lifecycle_id(db),
        _stages(damage=True, setup=True, trigger=True),
        observed_at=1000,
    )

    assert result["confirmed"] == {
        "hype": False,
        "damage": False,
        "setup": False,
        "trigger": False,
        "passed": False,
    }


def test_stage_chain_expires_setup_before_a_late_trigger(tmp_path):
    db = _db(tmp_path)
    db.update_candidates({SYMBOL: _candidate()})
    lifecycle_id = _lifecycle_id(db)
    store = StageLifecycleStore(db.db_path)
    store.advance(SYMBOL, lifecycle_id, _stages(hype=True), observed_at=1000)
    store.advance(SYMBOL, lifecycle_id, _stages(damage=True), observed_at=1001)
    store.advance(SYMBOL, lifecycle_id, _stages(setup=True), observed_at=1002)

    result = store.advance(
        SYMBOL,
        lifecycle_id,
        _stages(trigger=True),
        observed_at=1002 + StageLifecycleStore.SETUP_TTL_SECONDS + 1,
    )

    assert result["confirmed"]["setup"] is False
    assert result["confirmed"]["trigger"] is False
    assert result["confirmed"]["passed"] is False


def test_stage_chain_survives_process_restart(tmp_path):
    db = _db(tmp_path)
    db.update_candidates({SYMBOL: _candidate()})
    lifecycle_id = _lifecycle_id(db)
    StageLifecycleStore(db.db_path).advance(
        SYMBOL, lifecycle_id, _stages(hype=True), observed_at=1000
    )
    StageLifecycleStore(db.db_path).advance(
        SYMBOL, lifecycle_id, _stages(damage=True), observed_at=1010
    )

    restarted = StageLifecycleStore(db.db_path)
    restarted.advance(SYMBOL, lifecycle_id, _stages(setup=True), observed_at=1020)
    result = restarted.advance(
        SYMBOL, lifecycle_id, _stages(trigger=True), observed_at=1030
    )

    assert result["confirmed"]["passed"] is True


def test_stale_lifecycle_cannot_advance_after_eligibility_flip(tmp_path):
    db = _db(tmp_path)
    db.update_candidates({SYMBOL: _candidate()})
    old_id = _lifecycle_id(db)
    store = StageLifecycleStore(db.db_path)
    store.advance(SYMBOL, old_id, _stages(hype=True), observed_at=1000)

    db.update_candidates({SYMBOL: _candidate(False)})
    db.update_candidates({SYMBOL: _candidate(True)})
    new_id = _lifecycle_id(db)
    stale = store.advance(SYMBOL, old_id, _stages(damage=True), observed_at=1010)
    fresh = store.advance(SYMBOL, new_id, _stages(damage=True), observed_at=1010)

    assert new_id > old_id
    assert stale["available"] is False
    assert stale["stale"] is True
    assert stale["reason"] == "lifecycle_mismatch"
    assert fresh["confirmed"]["hype"] is False
    assert fresh["confirmed"]["damage"] is False


def test_scan_ineligible_candidate_cannot_advance(tmp_path):
    db = _db(tmp_path)
    db.update_candidates({SYMBOL: _candidate(False)})
    result = StageLifecycleStore(db.db_path).advance(
        SYMBOL,
        _lifecycle_id(db),
        _stages(hype=True),
        observed_at=1000,
    )

    assert result["available"] is False
    assert result["reason"] == "scan_ineligible"


def test_same_evaluation_can_confirm_the_full_chain(tmp_path):
    db = _db(tmp_path)
    db.update_candidates({SYMBOL: _candidate()})
    result = StageLifecycleStore(db.db_path).advance(
        SYMBOL,
        _lifecycle_id(db),
        _stages(hype=True, damage=True, setup=True, trigger=True, passed=True),
        observed_at=1000,
    )

    assert result["confirmed"]["passed"] is True
    assert result["snapshot"]["passed"] is True
