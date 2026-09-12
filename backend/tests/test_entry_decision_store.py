import asyncio
import time
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core import entry_decision_store as entry_store_module
from waterfallhunter.core.entry_decision_store import (
    EntryDecisionStore,
    EntryOutcomeResolutionWorker,
)


def packet(decision: str, readiness: float, now: int) -> dict:
    return {
        "contract_version": "entry_decision_v1",
        "policy_version": "entry_policy_v1",
        "evaluated_at": now,
        "decision": decision,
        "lifecycle_state": "ARMED",
        "entry_readiness": readiness,
        "evidence_coverage_pct": 82.0,
        "hard_blocked": decision in {"LATE", "INVALIDATED"},
        "block_reasons": ["ANTI_CHASE_HARD_BLOCK"] if decision == "LATE" else [],
        "reason_codes": ["SELL_PRESSURE_CONFIRMED"],
        "components": {},
        "trade_plan": None,
        "policy": {},
    }


def outcome_packet(symbol: str, *, available: bool) -> dict:
    item = packet("ENTRY_READY", 84.0, 100)
    item["trade_plan"] = {
        "entry_price": 100.0,
        "stop_loss": 105.0,
        "take_profit_1": 95.0,
        "take_profit_2": 90.0,
    }
    item["research_provenance"] = {
        "source_revision": "a" * 40,
        "decision_contract_sha256": "b" * 64,
        "contract": {
            "available": available,
            "exchange": "lbank" if available else "bybit",
            "mapped_symbol": symbol,
            "market_type": "linear_usdt_perpetual",
        },
        "outcome_contract": {
            "closed_candles_only": True,
            "complete_window_required": True,
        },
    }
    return item


def test_store_appends_only_decision_transitions(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)

    first = store.append_if_changed("SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100))
    duplicate = store.append_if_changed("SXT/USDT:USDT", packet("ENTRY_READY", 85.0, 101))
    late = store.append_if_changed("SXT/USDT:USDT", packet("LATE", 82.0, 120))

    assert first is not None
    assert duplicate is None
    assert late is not None
    latest = store.latest_for_symbol("SXT/USDT:USDT")
    assert latest is not None
    assert latest["decision"] == "LATE"
    assert latest["entry_readiness"] == 82.0


def test_entry_ready_history_survives_later_transition(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    store.append_if_changed("SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100))
    store.append_if_changed("SXT/USDT:USDT", packet("INVALIDATED", 40.0, 130))
    history = store.history_for_symbol("SXT/USDT:USDT", limit=10)
    assert [row["decision"] for row in history] == ["INVALIDATED", "ENTRY_READY"]




def test_recent_transitions_excludes_material_same_decision_events(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    first = packet("LATE", 60.0, 100)
    second = packet("LATE", 61.0, 110)
    second["block_reasons"] = ["ANTI_CHASE_HARD_BLOCK", "STALE_REFERENCE"]
    third = packet("NO_TRADE", 20.0, 120)

    assert store.append_if_changed("TEST/USDT:USDT", first) is not None
    assert store.append_if_changed("TEST/USDT:USDT", second) is not None
    assert store.append_if_changed("TEST/USDT:USDT", third) is not None

    rows = store.recent_transitions(limit=10)

    assert [(row["previous_decision"], row["decision"]) for row in rows] == [
        ("LATE", "NO_TRADE"),
    ]



def test_recent_transitions_stays_bounded_under_long_same_state_tail(tmp_path, monkeypatch) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    base = packet("LATE", 60.0, 100)
    base_json = json.dumps(base, sort_keys=True, separators=(",", ":"))
    base_hash = entry_store_module.canonical_sha256(base)
    first_packet = packet("ENTRY_READY", 84.0, 1)
    first_json = json.dumps(first_packet, sort_keys=True, separators=(",", ":"))
    first_hash = entry_store_module.canonical_sha256(first_packet)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO entry_decision_events "
            "(symbol,event_at,decision,lifecycle_state,entry_readiness,evidence_coverage_pct,"
            "policy_version,packet_json,packet_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("TAIL/USDT:USDT", 1, "ENTRY_READY", "ARMED", 84.0, 82.0, "entry_policy_v1",
             first_json, first_hash, 1),
        )
        conn.executemany(
            "INSERT INTO entry_decision_events "
            "(symbol,event_at,decision,lifecycle_state,entry_readiness,evidence_coverage_pct,"
            "policy_version,packet_json,packet_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("TAIL/USDT:USDT", index, "LATE", "ARMED", 60.0, 82.0, "entry_policy_v1", base_json, base_hash, index)
                for index in range(2, 2002)
            ],
        )

    original_connect = entry_store_module.connect_managed_sqlite
    progress_calls = 0

    def bounded_connect(*args, **kwargs):
        nonlocal progress_calls
        conn = original_connect(*args, **kwargs)

        def progress() -> int:
            nonlocal progress_calls
            progress_calls += 1
            return 1 if progress_calls > 5_000 else 0

        conn.set_progress_handler(progress, 100)
        return conn

    monkeypatch.setattr(entry_store_module, "connect_managed_sqlite", bounded_connect)
    rows = EntryDecisionStore(db_path).recent_transitions(limit=20)

    assert [(row["previous_decision"], row["decision"]) for row in rows] == [("ENTRY_READY", "LATE")]
    assert progress_calls <= 5_000

def test_recent_transitions_reuses_incremental_history_after_bootstrap(tmp_path, monkeypatch) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    base = packet("LATE", 60.0, 100)
    base_json = json.dumps(base, sort_keys=True, separators=(",", ":"))
    base_hash = entry_store_module.canonical_sha256(base)
    first_packet = packet("ENTRY_READY", 84.0, 1)
    first_json = json.dumps(first_packet, sort_keys=True, separators=(",", ":"))
    first_hash = entry_store_module.canonical_sha256(first_packet)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO entry_decision_events "
            "(symbol,event_at,decision,lifecycle_state,entry_readiness,evidence_coverage_pct,"
            "policy_version,packet_json,packet_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("CACHE/USDT:USDT", 1, "ENTRY_READY", "ARMED", 84.0, 82.0, "entry_policy_v1",
             first_json, first_hash, 1),
        )
        conn.executemany(
            "INSERT INTO entry_decision_events "
            "(symbol,event_at,decision,lifecycle_state,entry_readiness,evidence_coverage_pct,"
            "policy_version,packet_json,packet_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("CACHE/USDT:USDT", index, "LATE", "ARMED", 60.0, 82.0, "entry_policy_v1", base_json, base_hash, index)
                for index in range(2, 2002)
            ],
        )

    store = EntryDecisionStore(db_path)
    baseline = store.recent_transitions(limit=20)
    assert baseline[0]["decision"] == "LATE"
    store.append_if_changed("CACHE/USDT:USDT", packet("NO_TRADE", 20.0, 3000))

    original_connect = entry_store_module.connect_managed_sqlite
    progress_calls = 0

    def bounded_connect(*args, **kwargs):
        nonlocal progress_calls
        conn = original_connect(*args, **kwargs)

        def progress() -> int:
            nonlocal progress_calls
            progress_calls += 1
            return 1 if progress_calls > 100 else 0

        conn.set_progress_handler(progress, 100)
        return conn

    monkeypatch.setattr(entry_store_module, "connect_managed_sqlite", bounded_connect)
    rows = store.recent_transitions(limit=20)

    assert rows[0]["decision"] == "NO_TRADE"
    assert rows[0]["previous_decision"] == "LATE"
    assert progress_calls <= 100


def test_decision_events_are_immutable(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed(
        "SXT/USDT:USDT",
        packet("ENTRY_READY", 84.0, 100),
    )
    assert event_id is not None
    with sqlite3.connect(db_path) as conn:
        try:
            conn.execute(
                "UPDATE entry_decision_events SET decision='NO_TRADE' WHERE id=?",
                (event_id,),
            )
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("entry decision events must be immutable")


def test_outcome_capture_is_observational_unique_and_append_only(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    decision_event_id = store.append_if_changed(
        "SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100)
    )
    assert decision_event_id is not None

    capture_id = store.append_outcome_capture(
        decision_event_id,
        {
            "observational_only": True,
            "decision_mutated": False,
            "outcome_status": "UNOBSERVED",
            "source": "forward_capture",
        },
        captured_at=101,
    )
    assert capture_id > 0
    with sqlite3.connect(db_path) as conn:
        stored_capture = json.loads(
            conn.execute(
                "SELECT capture_json FROM decision_outcome_capture WHERE id=?", (capture_id,)
            ).fetchone()[0]
        )
    assert stored_capture["costs"]["funding"]["value"] is None
    assert stored_capture["costs"]["funding"]["classification"] == "UNAVAILABLE"
    assert stored_capture["costs"]["exit_slippage"]["value"] is None
    with pytest.raises(ValueError, match="already exists"):
        store.append_outcome_capture(
            decision_event_id,
            {
                "observational_only": True,
                "decision_mutated": False,
            },
            captured_at=102,
        )
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(
                "UPDATE decision_outcome_capture SET outcome_status='OBSERVED' WHERE id=?",
                (capture_id,),
            )
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            conn.execute(
                "DELETE FROM decision_outcome_capture WHERE id=?",
                (capture_id,),
            )


def test_outcome_capture_rejects_decision_mutation(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    decision_event_id = store.append_if_changed(
        "SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100)
    )
    assert decision_event_id is not None
    with pytest.raises(ValueError, match="observational only"):
        store.append_outcome_capture(
            decision_event_id,
            {"observational_only": False, "decision_mutated": True},
            captured_at=101,
        )


def test_outcome_capture_rejects_available_cost_without_provenance(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed("SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100))
    assert event_id is not None
    with pytest.raises(ValueError, match="funding available cost provenance invalid"):
        store.append_outcome_capture(
            event_id,
            {
                "observational_only": True,
                "decision_mutated": False,
                "costs": {
                    "funding": {"classification": "OBSERVED_COST", "value": None}
                },
            },
            captured_at=101,
        )


@pytest.mark.parametrize("field", ["trade_eligible", "eligibility", "promotion_allowed"])
def test_initial_capture_rejects_policy_changing_flags(tmp_path, field):
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed("SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100))
    assert event_id is not None
    with pytest.raises(ValueError, match="observational only"):
        store.append_outcome_capture(
            event_id,
            {"observational_only": True, "decision_mutated": False, field: True},
            captured_at=101,
        )


def test_initial_capture_normalizes_policy_flags_and_rejects_early_timestamp(tmp_path):
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed("SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100))
    assert event_id is not None
    with pytest.raises(ValueError, match="precedes decision event"):
        store.append_outcome_capture(
            event_id,
            {
                "observational_only": True,
                "decision_mutated": False,
                "trade_eligible": False,
                "eligibility": None,
                "promotion_allowed": False,
            },
            captured_at=99,
        )
    capture_id = store.append_outcome_capture(
        event_id,
        {
            "observational_only": True,
            "decision_mutated": False,
            "trade_eligible": False,
            "eligibility": "SHOULD_NOT_PERSIST",
            "promotion_allowed": False,
        },
        captured_at=100,
    )
    with sqlite3.connect(db_path) as conn:
        payload = json.loads(
            conn.execute("SELECT capture_json FROM decision_outcome_capture WHERE id=?", (capture_id,)).fetchone()[0]
        )
    assert payload["trade_eligible"] is False
    assert payload["eligibility"] is None
    assert payload["promotion_allowed"] is False


def test_initial_capture_resolves_in_separate_immutable_table(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    decision_event_id = store.append_if_changed(
        "SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100)
    )
    assert decision_event_id is not None
    capture_id = store.append_outcome_capture(
        decision_event_id,
        {"observational_only": True, "decision_mutated": False},
        captured_at=101,
    )
    resolution_id = store.resolve_outcome_capture(
        decision_event_id,
        {"outcome_status": "OBSERVED", "mfe_pct": 1.0},
        resolved_at=200,
    )
    assert capture_id > 0
    assert resolution_id > 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT outcome_status FROM decision_outcome_capture WHERE id=?",
            (capture_id,),
        ).fetchone()[0] == "UNOBSERVED"
    with pytest.raises(ValueError, match="already exists"):
        store.resolve_outcome_capture(
            decision_event_id,
            {"outcome_status": "OBSERVED", "mfe_pct": 2.0},
            resolved_at=201,
        )


def test_canonical_append_with_capture_and_observed_not_pending(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed_with_capture(
        "SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100), captured_at=101
    )
    assert event_id is not None
    assert len(store.pending_outcome_captures(mature_before=101)) == 1
    store.resolve_outcome_capture(
        event_id, {"outcome_status": "OBSERVED", "mfe_pct": 1.0}, resolved_at=200
    )
    assert store.pending_outcome_captures(mature_before=200) == []
    with sqlite3.connect(db_path) as conn:
        payload = json.loads(
            conn.execute(
                "SELECT resolution_json FROM decision_outcome_resolution WHERE decision_event_id=?",
                (event_id,),
            ).fetchone()[0]
        )
    assert payload["observational_only"] is True
    assert payload["decision_mutated"] is False
    assert payload["trade_eligible"] is False
    assert payload["promotion_allowed"] is False


def test_unchanged_retry_repairs_capture_after_initial_capture_failure(tmp_path, monkeypatch):
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    original = store.append_outcome_capture
    failed = True

    def fail_once(*args, **kwargs):
        nonlocal failed
        if failed:
            failed = False
            raise sqlite3.OperationalError("research store down")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "append_outcome_capture", fail_once)
    p = packet("ENTRY_READY", 84.0, 100)
    event_id = store.append_if_changed_with_capture("SXT/USDT:USDT", p, captured_at=101)
    assert event_id is not None
    assert store.append_if_changed_with_capture("SXT/USDT:USDT", p, captured_at=101) is None
    assert len(store.pending_outcome_captures(mature_before=101)) == 1


def test_automatic_capture_binds_exact_parent_packet_and_runtime_provenance(tmp_path):
    db_path = migrate_test_database(tmp_path / "registry.db")
    source_revision = "a" * 40
    store = EntryDecisionStore(db_path, source_revision=source_revision)
    p = packet("ENTRY_READY", 84.0, 100)
    p["research_provenance"] = {
        "source_revision": source_revision,
        "decision_contract_sha256": "b" * 64,
        "contract": {
            "exchange": "lbank",
            "mapped_symbol": "SXT/USDT:USDT",
            "market_type": "linear_usdt_perpetual",
        },
        "costs": {
            "fees": {"value": None, "classification": "UNAVAILABLE", "reason": "not observed"},
            "entry_slippage": {
                "value": 0.05,
                "source": "decision_time_microstructure",
                "classification": "MODELED_COST",
            },
            "exit_slippage": {"value": None, "classification": "UNAVAILABLE", "reason": "not observed"},
            "funding": {"value": None, "classification": "UNAVAILABLE", "reason": "not observed"},
        },
    }

    event_id = store.append_if_changed_with_capture("SXT/USDT:USDT", p, captured_at=100)
    assert event_id is not None
    with sqlite3.connect(db_path) as conn:
        packet_hash = conn.execute(
            "SELECT packet_hash FROM entry_decision_events WHERE id=?", (event_id,)
        ).fetchone()[0]
        capture = json.loads(
            conn.execute(
                "SELECT capture_json FROM decision_outcome_capture WHERE decision_event_id=?",
                (event_id,),
            ).fetchone()[0]
        )
    assert capture["decision_packet_sha256"] == packet_hash
    assert capture["source_revision"] == source_revision
    assert capture["source_revision_status"] == "VERIFIED_GIT_REVISION"
    assert capture["decision_contract_sha256"] == "b" * 64
    assert capture["contract"]["exchange"] == "lbank"
    assert capture["trade_plan"] is None
    assert set(capture["costs"]) == {"fees", "entry_slippage", "exit_slippage", "funding"}


def test_new_transition_uses_current_runtime_revision_not_stale_embedded_provenance(tmp_path):
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path, source_revision="b" * 40)
    p = packet("EXPIRED", 84.0, 200)
    p["research_provenance"] = {
        "source_revision": "a" * 40,
        "decision_contract_sha256": "c" * 64,
    }

    event_id = store.append_if_changed_with_capture("SXT/USDT:USDT", p, captured_at=200)
    assert event_id is not None
    with sqlite3.connect(db_path) as conn:
        capture = json.loads(conn.execute(
            "SELECT capture_json FROM decision_outcome_capture WHERE decision_event_id=?",
            (event_id,),
        ).fetchone()[0])
    assert capture["source_revision"] == "b" * 40
    assert capture["decision_contract_sha256"] is None


def test_tampered_capture_is_rejected_before_resolver(tmp_path, caplog):
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed("SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100))
    assert event_id is not None
    store.append_outcome_capture(
        event_id, {"observational_only": True, "decision_mutated": False}, captured_at=101
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER decision_outcome_capture_no_update")
        conn.execute(
            "UPDATE decision_outcome_capture SET capture_json=? WHERE decision_event_id=?",
            ('{"observational_only":true,"decision_mutated":false}', event_id),
        )
    with caplog.at_level("ERROR"):
        assert store.resolve_matured_outcomes(lambda _: pytest.fail("resolver called"), mature_before=101) == 0
    assert "Rejecting tampered outcome capture" in caplog.text


def test_resolution_persistence_failure_remains_retryable(tmp_path, monkeypatch, caplog):
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed_with_capture(
        "SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100), captured_at=101
    )
    assert event_id is not None
    original = store.resolve_outcome_capture
    monkeypatch.setattr(
        store, "resolve_outcome_capture",
        lambda *args, **kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
    )
    with caplog.at_level("WARNING"):
        assert store.resolve_matured_outcomes(
            lambda _: {"outcome_status": "OBSERVED", "mfe_pct": 1.0},
            mature_before=101,
        ) == 0
    assert store.pending_outcome_captures(mature_before=101)
    monkeypatch.setattr(store, "resolve_outcome_capture", original)


@pytest.mark.parametrize(
    "failure",
    [sqlite3.OperationalError("database is locked"), RuntimeError("research store down")],
)
def test_capture_persistence_failure_does_not_change_canonical_result(
    tmp_path, monkeypatch, caplog, failure
) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    monkeypatch.setattr(store, "append_outcome_capture", lambda *args, **kwargs: (_ for _ in ()).throw(failure))

    with caplog.at_level("ERROR"):
        event_id = store.append_if_changed_with_capture(
            "SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100), captured_at=101
        )

    assert event_id is not None
    assert store.latest_for_symbol("SXT/USDT:USDT")["decision"] == "ENTRY_READY"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM entry_decision_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM entry_notification_outbox").fetchone()[0] == 1
    assert "Research outcome capture persistence failed" in caplog.text


def test_concurrent_capture_repair_is_unique_and_preserves_noop_result(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    p = packet("ENTRY_READY", 84.0, 100)
    event_id = store.append_if_changed("SXT/USDT:USDT", p)
    assert event_id is not None

    def repair():
        return store.append_if_changed_with_capture(
            "SXT/USDT:USDT", p, captured_at=101
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: repair(), range(2)))

    assert results == [None, None]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM entry_decision_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM decision_outcome_capture").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM entry_notification_outbox").fetchone()[0] == 1


def test_resolution_rejects_mutating_policy_fields(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed("SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100))
    assert event_id is not None
    store.append_outcome_capture(
        event_id, {"observational_only": True, "decision_mutated": False}, captured_at=101
    )
    with pytest.raises(ValueError, match="observational only"):
        store.resolve_outcome_capture(
            event_id,
            {"outcome_status": "OBSERVED", "decision_mutated": True},
            resolved_at=200,
        )


def test_resolution_rejects_timestamp_before_capture(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed("SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100))
    assert event_id is not None
    store.append_outcome_capture(
        event_id, {"observational_only": True, "decision_mutated": False}, captured_at=101
    )
    with pytest.raises(ValueError, match="precedes initial capture"):
        store.resolve_outcome_capture(
            event_id, {"outcome_status": "OBSERVED"}, resolved_at=100
        )


def test_capture_parent_hash_mismatch_is_rejected_before_resolver(tmp_path, caplog) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path, source_revision="a" * 40)
    event_id = store.append_if_changed_with_capture(
        "SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100), captured_at=101
    )
    assert event_id is not None
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER decision_outcome_capture_no_update")
        raw = conn.execute(
            "SELECT capture_json FROM decision_outcome_capture WHERE decision_event_id=?",
            (event_id,),
        ).fetchone()[0]
        payload = json.loads(raw)
        payload["decision_packet_sha256"] = "f" * 64
        encoded = json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))
        from waterfallhunter.core.signal_metadata import canonical_sha256

        conn.execute(
            "UPDATE decision_outcome_capture SET capture_json=?,capture_hash=? WHERE decision_event_id=?",
            (encoded, canonical_sha256(payload), event_id),
        )
    with caplog.at_level("ERROR"):
        assert store.resolve_matured_outcomes(
            lambda _: pytest.fail("resolver called"), mature_before=101
        ) == 0
    assert "parent decision packet hash mismatch" in caplog.text


def test_pending_and_resolution_are_idempotent_and_isolate_research_failures(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed("SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100))
    assert event_id is not None
    store.append_outcome_capture(
        event_id, {"observational_only": True, "decision_mutated": False},
        captured_at=101,
    )
    assert len(store.pending_outcome_captures(mature_before=101)) == 1
    assert store.resolve_matured_outcomes(
        lambda _: {"outcome_status": "UNAVAILABLE", "cost": None, "provenance": None},
        mature_before=101,
    ) == 1
    assert store.pending_outcome_captures(mature_before=101) == []
    assert store.resolve_matured_outcomes(
        lambda _: (_ for _ in ()).throw(RuntimeError("research unavailable")),
        mature_before=101,
    ) == 0


def test_unknown_resolution_failure_is_retryable_and_does_not_starve_later_rows(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    first = store.append_if_changed("FIRST", packet("ENTRY_READY", 84.0, 100))
    second = store.append_if_changed("SECOND", packet("ENTRY_READY", 84.0, 100))
    assert first is not None
    assert second is not None
    for event_id in (first, second):
        store.append_outcome_capture(
            event_id, {"observational_only": True, "decision_mutated": False}, captured_at=101
        )
    calls = []

    def resolver(capture):
        calls.append(capture["decision_event_id"])
        if capture["decision_event_id"] == first:
            raise RuntimeError("permanent")
        return {"outcome_status": "OBSERVED"}

    assert store.resolve_matured_outcomes(resolver, mature_before=101, limit=2) == 1
    assert calls == [first, second]
    assert [row["decision_event_id"] for row in store.pending_outcome_captures(mature_before=101)] == [first]


def test_terminal_scientific_unavailable_resolution_is_finalized(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed("FIRST", packet("ENTRY_READY", 84.0, 100))
    assert event_id is not None
    store.append_outcome_capture(
        event_id, {"observational_only": True, "decision_mutated": False}, captured_at=101
    )

    class ExplicitScientificAbsence(RuntimeError):
        terminal = True

    assert store.resolve_matured_outcomes(
        lambda _: (_ for _ in ()).throw(ExplicitScientificAbsence("domain proof")),
        mature_before=101,
    ) == 0
    assert store.pending_outcome_captures(mature_before=101) == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT outcome_status FROM decision_outcome_resolution WHERE decision_event_id=?",
            (event_id,),
        ).fetchone()[0] == "UNAVAILABLE"


def test_file_not_found_and_temporary_provider_failures_remain_retryable(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    first = store.append_if_changed_with_capture(
        "FIRST", packet("ENTRY_READY", 84.0, 100), captured_at=101
    )
    second = store.append_if_changed_with_capture(
        "SECOND", packet("ENTRY_READY", 84.0, 100), captured_at=101
    )
    assert first is not None
    assert second is not None
    failures = iter((FileNotFoundError("mount not ready"), TimeoutError("provider timeout")))

    def resolver(_capture):
        raise next(failures)

    assert store.resolve_matured_outcomes(resolver, mature_before=101, limit=2) == 0
    assert [row["decision_event_id"] for row in store.pending_outcome_captures(mature_before=101)] == [first, second]


def test_entry_outcome_worker_runs_bounded_cycle_and_stops(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    calls = []
    class StubStore:
        def resolve_matured_outcomes(self, resolver, *, mature_before, limit):
            assert limit == 2
            return resolver({"decision_event_id": 7}) or 1

    worker = EntryOutcomeResolutionWorker(
        StubStore(),
        lambda capture: calls.append(capture["decision_event_id"]) or None,
        batch_size=2,
        interval_seconds=0,
    )

    assert worker.batch_size == 2
    assert worker.interval_seconds == 60.0
    assert asyncio.run(worker.run_once(now=100_000)) == 1
    assert calls == [7]
    worker.stop()
    assert worker._running is False


def test_entry_outcome_worker_round_robin_prevents_retryable_row_starvation(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    first = store.append_if_changed_with_capture(
        "FIRST", packet("ENTRY_READY", 84.0, 100), captured_at=101
    )
    second = store.append_if_changed_with_capture(
        "SECOND", packet("ENTRY_READY", 84.0, 100), captured_at=101
    )
    assert first is not None
    assert second is not None
    calls = []

    async def resolver(capture):
        calls.append(capture["decision_event_id"])
        if capture["decision_event_id"] == first:
            raise TimeoutError("temporary provider timeout")
        return {"outcome_status": "OBSERVED"}

    worker = EntryOutcomeResolutionWorker(store, resolver, batch_size=1, interval_seconds=60)
    assert asyncio.run(worker.run_once(now=100_000)) == 0
    assert asyncio.run(worker.run_once(now=100_000)) == 1
    assert calls == [first, second]
    assert [row["decision_event_id"] for row in store.pending_outcome_captures(mature_before=101)] == [first]


def test_entry_outcome_worker_prioritizes_provider_ready_capture_over_local_backlog(
    tmp_path,
) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path, source_revision="a" * 40)

    def append_capture(symbol: str, *, available: bool) -> int:
        event_id = store.append_if_changed_with_capture(
            symbol,
            outcome_packet(symbol, available=available),
            captured_at=101,
        )
        assert event_id is not None
        return event_id

    unavailable_ids = [
        append_capture(f"UNAVAILABLE{index}/USDT:USDT", available=False)
        for index in range(3)
    ]
    provider_ready_id = append_capture("READY/USDT:USDT", available=True)
    calls = []

    async def resolver(capture):
        calls.append(capture["decision_event_id"])
        return {
            "outcome_status": (
                "OBSERVED" if capture["decision_event_id"] == provider_ready_id else "UNAVAILABLE"
            )
        }

    worker = EntryOutcomeResolutionWorker(
        store,
        resolver,
        batch_size=1,
        local_batch_size=3,
        interval_seconds=60,
    )

    assert asyncio.run(worker.run_once(now=100_000)) == 4
    assert calls == [provider_ready_id, *unavailable_ids]


def test_entry_outcome_worker_keeps_provider_attempts_bounded_across_lanes(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path, source_revision="a" * 40)

    def append_capture(symbol: str, *, available: bool) -> int:
        event_id = store.append_if_changed_with_capture(
            symbol,
            outcome_packet(symbol, available=available),
            captured_at=101,
        )
        assert event_id is not None
        return event_id

    provider_ids = [
        append_capture(f"READY{index}/USDT:USDT", available=True)
        for index in range(2)
    ]
    local_ids = [
        append_capture(f"LOCAL{index}/USDT:USDT", available=False)
        for index in range(2)
    ]
    calls = []

    async def resolver(capture):
        event_id = capture["decision_event_id"]
        calls.append(event_id)
        return {
            "outcome_status": "OBSERVED" if event_id in provider_ids else "UNAVAILABLE"
        }

    worker = EntryOutcomeResolutionWorker(
        store,
        resolver,
        batch_size=1,
        local_batch_size=2,
        interval_seconds=60,
    )

    assert asyncio.run(worker.run_once(now=100_000)) == 3
    assert calls == [provider_ids[0], *local_ids]
    assert provider_ids[1] in [
        row["decision_event_id"]
        for row in store.pending_outcome_captures(mature_before=101)
    ]


def test_entry_outcome_worker_round_robins_retryable_provider_lane(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path, source_revision="a" * 40)
    provider_ids = []
    for symbol in ("FIRST/USDT:USDT", "SECOND/USDT:USDT"):
        event_id = store.append_if_changed_with_capture(
            symbol,
            outcome_packet(symbol, available=True),
            captured_at=101,
        )
        assert event_id is not None
        provider_ids.append(event_id)
    calls = []

    async def resolver(capture):
        event_id = capture["decision_event_id"]
        calls.append(event_id)
        if event_id == provider_ids[0]:
            raise TimeoutError("temporary provider timeout")
        return {"outcome_status": "OBSERVED"}

    worker = EntryOutcomeResolutionWorker(
        store,
        resolver,
        batch_size=1,
        local_batch_size=1,
        interval_seconds=60,
    )

    assert asyncio.run(worker.run_once(now=100_000)) == 0
    assert asyncio.run(worker.run_once(now=100_000)) == 1
    assert calls == provider_ids



def test_worker_advances_cursor_past_three_tampered_captures(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_ids = []
    for index in range(4):
        event_id = store.append_if_changed_with_capture(
            f"SYM{index}", packet("ENTRY_READY", 84.0, 100 + index), captured_at=110 + index
        )
        assert event_id is not None
        event_ids.append(event_id)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER decision_outcome_capture_no_update")
        for event_id in event_ids[:3]:
            conn.execute(
                "UPDATE decision_outcome_capture SET capture_json=? WHERE decision_event_id=?",
                ('{"observational_only":true,"decision_mutated":false}', event_id),
            )

    resolved = []

    async def resolver(capture):
        resolved.append(capture["decision_event_id"])
        return {"outcome_status": "OBSERVED"}

    worker = EntryOutcomeResolutionWorker(store, resolver, batch_size=3, interval_seconds=60)
    assert asyncio.run(worker.run_once(now=100_000)) == 0
    assert asyncio.run(worker.run_once(now=100_000)) == 1
    assert resolved == [event_ids[3]]


def test_latest_transition_uses_append_order_when_clock_moves_backward(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    store.append_if_changed("SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 200))
    late_id = store.append_if_changed("SXT/USDT:USDT", packet("LATE", 80.0, 100))
    assert late_id is not None

    latest = store.latest_for_symbol("SXT/USDT:USDT")
    assert latest is not None
    assert latest["decision"] == "LATE"

    duplicate = store.append_if_changed("SXT/USDT:USDT", packet("LATE", 79.0, 150))
    assert duplicate is None
    history = store.history_for_symbol("SXT/USDT:USDT", limit=10)
    assert [row["decision"] for row in history] == ["LATE", "ENTRY_READY"]


def test_latest_for_symbol_includes_latest_durable_advisory(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed(
        "SXT/USDT:USDT",
        packet("ENTRY_READY", 84.0, 100),
    )
    assert event_id is not None
    store.append_advisory(
        event_id,
        {
            "observational_only": True,
            "decision_mutated": False,
            "ai_advice": "SHORT",
            "ai_confidence": 81,
            "ai_reasoning": "Evidence agrees.",
            "ai_provider": "gemini",
            "ai_model": "gemini-test",
            "ai_status": "AVAILABLE",
        },
        advisory_at=110,
    )

    latest = EntryDecisionStore(db_path).latest_for_symbol("SXT/USDT:USDT")
    assert latest is not None
    assert latest["event_id"] == event_id
    assert latest["ai_advisory"]["ai_advice"] == "SHORT"


def test_advisory_is_append_only_and_survives_store_restart(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    decision_event_id = store.append_if_changed(
        "SXT/USDT:USDT",
        packet("ENTRY_READY", 84.0, 100),
    )
    assert decision_event_id is not None

    advisory_event_id = store.append_advisory(
        decision_event_id,
        {
            "observational_only": True,
            "decision_mutated": False,
            "ai_advice": "SHORT",
            "ai_confidence": 81,
            "ai_reasoning": "Derivatives and sell flow agree.",
            "ai_provider": "gemini",
            "ai_model": "gemini-test",
            "ai_status": "AVAILABLE",
        },
        advisory_at=110,
    )

    restarted = EntryDecisionStore(db_path)
    history = restarted.history_for_symbol("SXT/USDT:USDT", limit=10)
    assert advisory_event_id > 0
    assert history[0]["decision"] == "ENTRY_READY"
    assert history[0]["ai_advisory"]["ai_advice"] == "SHORT"
    assert history[0]["ai_advisory"]["ai_model"] == "gemini-test"
    assert history[0]["ai_advisory"]["advisory_at"] == 110


def test_guarded_append_rejects_recycled_lifecycle_before_event_or_outbox(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    symbol = "TEST/USDT:USDT"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE lbank_catalog SET lifecycle_id=2, scan_eligible=1, status='WATCH' WHERE symbol=?",
            (symbol,),
        )

    with pytest.raises(RuntimeError, match="candidate lifecycle is no longer current"):
        store.append_if_changed(
            symbol,
            packet("ENTRY_READY", 84.0, 100),
            expected_lifecycle_id=1,
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM entry_decision_events WHERE symbol=?", (symbol,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM entry_notification_outbox"
        ).fetchone()[0] == 0


def test_guarded_append_rejects_scan_ineligible_candidate(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    symbol = "TEST/USDT:USDT"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE lbank_catalog SET scan_eligible=0 WHERE symbol=?",
            (symbol,),
        )

    with pytest.raises(RuntimeError, match="candidate lifecycle is no longer current"):
        store.append_if_changed(
            symbol,
            packet("ENTRY_READY", 84.0, 100),
            expected_lifecycle_id=1,
        )


def test_history_rejects_packet_hash_mismatch(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed(
        "SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100)
    )
    assert event_id is not None
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER entry_decision_events_no_update")
        corrupted = packet("ENTRY_READY", 99.0, 100)
        conn.execute(
            "UPDATE entry_decision_events SET packet_json=? WHERE id=?",
            (json.dumps(corrupted, sort_keys=True, separators=(",", ":")), event_id),
        )

    with pytest.raises(ValueError, match="packet hash mismatch"):
        store.latest_for_symbol("SXT/USDT:USDT")


def test_history_rejects_advisory_hash_mismatch(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    event_id = store.append_if_changed(
        "SXT/USDT:USDT", packet("ENTRY_READY", 84.0, 100)
    )
    assert event_id is not None
    advisory_id = store.append_advisory(
        event_id,
        {
            "observational_only": True,
            "decision_mutated": False,
            "ai_advice": "SHORT",
            "ai_confidence": 81,
            "ai_reasoning": "Evidence agrees.",
            "ai_provider": "gemini",
            "ai_model": "gemini-test",
            "ai_status": "AVAILABLE",
        },
        advisory_at=110,
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TRIGGER entry_decision_advisories_no_update")
        row = conn.execute(
            "SELECT advisory_json FROM entry_decision_advisories WHERE id=?", (advisory_id,)
        ).fetchone()
        corrupted = json.loads(row[0])
        corrupted["ai_confidence"] = 1
        conn.execute(
            "UPDATE entry_decision_advisories SET advisory_json=? WHERE id=?",
            (json.dumps(corrupted, sort_keys=True, separators=(",", ":")), advisory_id),
        )

    with pytest.raises(ValueError, match="advisory hash mismatch"):
        store.latest_for_symbol("SXT/USDT:USDT")


def test_runtime_binds_canonical_append_to_expected_lifecycle(monkeypatch) -> None:
    from waterfallhunter import main

    symbol = "CAS/USDT:USDT"
    expected_lifecycle = 23
    observed: list[int | None] = []
    monkeypatch.setattr(main.scanner, "active_candidates", {symbol: {}})
    monkeypatch.setattr(main.scanner, "get_live_reference", lambda _symbol: (0.01, time.time()))
    monkeypatch.setattr(main.execution_decision_logger, "observe_evaluation", lambda *args, **kwargs: None)

    async def cross_check_symbol(*args, **kwargs):
        return {
            "is_valid": True, "score": 80.0, "suggested_status": "ARMED",
            "metrics": {"exchange": "binance", "mapped_symbol": symbol},
        }

    monkeypatch.setattr(main.validator, "cross_check_symbol", cross_check_symbol)
    monkeypatch.setattr(main, "_apply_deterministic_entry_gate", lambda _s, state, _m: (state, False))
    monkeypatch.setattr(main, "build_signal_leverage_advisory", lambda metrics, execution_suitability=None, **kwargs: {
        "policy_version": "adaptive_signal_leverage_v1",
        "minimum": 4, "maximum": 18, "symbol_agnostic": True,
        "signal_only": True, "advisory_only": True,
        "status": "UNAVAILABLE", "leverage": None, "reason": "test unavailable",
    })
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _symbol: None)
    monkeypatch.setattr(main, "build_entry_decision", lambda *args, **kwargs: {"decision": "FORMING"})

    def stop_append(*args, **kwargs):
        observed.append(kwargs.get("expected_lifecycle_id"))
        raise RuntimeError("stop-after-canonical-append")

    monkeypatch.setattr(main.entry_decision_store, "append_if_changed", stop_append)
    with pytest.raises(RuntimeError, match="stop-after-canonical-append"):
        asyncio.run(main.evaluate_candidate(symbol, {
            "status": "ARMED", "lifecycle_id": expected_lifecycle, "scan_eligible": True,
            "quote_volume": 3_000_000.0, "last_price": 0.01,
        }))
    assert observed == [expected_lifecycle]


def _actionable_packet(*, leverage: int, now: int = 100, lifecycle_id: int = 1) -> dict:
    value = packet("ENTRY_READY", 90.0, now)
    value["lifecycle_id"] = lifecycle_id
    value["trade_plan"] = {
        "entry_price": 100.0, "stop_loss": 102.0,
        "take_profit_1": 98.0, "take_profit_2": 96.0,
        "take_profit_3": None, "reward_to_risk": 2.0,
        "leverage": leverage,
    }
    value["leverage_advisory"] = {
        "status": "AVAILABLE", "leverage": leverage,
        "policy_version": "adaptive_signal_leverage_v1", "reason": None,
        "execution_suitability_input": {"status": "SUITABLE"},
    }
    return value


def test_same_entry_ready_persists_material_leverage_projection_change_without_duplicate_notification(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    first = store.append_if_changed("SXT/USDT:USDT", _actionable_packet(leverage=6, now=100))
    changed = store.append_if_changed("SXT/USDT:USDT", _actionable_packet(leverage=10, now=110))
    assert first is not None
    assert changed is not None
    latest = store.latest_for_symbol("SXT/USDT:USDT")
    assert latest["trade_plan"]["leverage"] == 10
    assert latest["leverage_advisory"]["leverage"] == 10
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM entry_notification_outbox").fetchone()[0] == 1


def test_same_entry_ready_persists_execution_suitability_provenance_change(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    first_packet = _actionable_packet(leverage=8, now=100)
    second_packet = _actionable_packet(leverage=8, now=110)
    second_packet["leverage_advisory"]["execution_suitability_input"] = {
        "status": "MARGINAL", "maximum_leverage": 10, "observed_samples": 55
    }
    assert store.append_if_changed("SXT/USDT:USDT", first_packet) is not None
    changed = store.append_if_changed("SXT/USDT:USDT", second_packet)
    assert changed is not None
    latest = store.latest_for_symbol("SXT/USDT:USDT")
    assert latest["leverage_advisory"]["execution_suitability_input"]["status"] == "MARGINAL"


def test_same_entry_ready_persists_changed_leverage_causal_input(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    first = _actionable_packet(leverage=8, now=100)
    second = _actionable_packet(leverage=8, now=110)
    first["leverage_advisory"]["causal_input"] = {"score": 91.0, "microstructure": {"spread_pct": 0.04}}
    second["leverage_advisory"]["causal_input"] = {"score": 92.0, "microstructure": {"spread_pct": 0.04}}
    assert store.append_if_changed("SXT/USDT:USDT", first) is not None
    changed = store.append_if_changed("SXT/USDT:USDT", second)
    assert changed is not None
    latest = store.latest_for_symbol("SXT/USDT:USDT")
    assert latest["leverage_advisory"]["causal_input"]["score"] == 92.0


def test_same_entry_ready_persists_leverage_reason_change(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    first = _actionable_packet(leverage=8, now=100)
    second = _actionable_packet(leverage=8, now=110)
    first["leverage_advisory"].update({"status": "NOT_RECOMMENDED", "leverage": None, "reason": "score bound"})
    second["leverage_advisory"].update({"status": "NOT_RECOMMENDED", "leverage": None, "reason": "position setup rejected"})
    first["trade_plan"]["leverage"] = None
    second["trade_plan"]["leverage"] = None
    assert store.append_if_changed("SXT/USDT:USDT", first) is not None
    changed = store.append_if_changed("SXT/USDT:USDT", second)
    assert changed is not None
    latest = store.latest_for_symbol("SXT/USDT:USDT")
    assert latest["leverage_advisory"]["reason"] == "position setup rejected"


def test_same_entry_ready_ignores_volatile_execution_suitability_counters(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    first = _actionable_packet(leverage=8, now=100)
    second = _actionable_packet(leverage=8, now=110)
    first["leverage_advisory"]["execution_suitability_input"] = {
        "available": True, "status": "SUITABLE", "maximum_leverage": 12,
        "observed_samples": 40, "observation_span_hours": 24.0, "availability_rate": 0.95,
    }
    second["leverage_advisory"]["execution_suitability_input"] = {
        "available": True, "status": "SUITABLE", "maximum_leverage": 12,
        "observed_samples": 55, "observation_span_hours": 36.0, "availability_rate": 0.97,
    }
    assert store.append_if_changed("SXT/USDT:USDT", first) is not None
    assert store.append_if_changed("SXT/USDT:USDT", second) is None


def test_same_entry_ready_same_material_projection_remains_deduplicated(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    assert store.append_if_changed("SXT/USDT:USDT", _actionable_packet(leverage=8, now=100)) is not None
    duplicate = _actionable_packet(leverage=8, now=120)
    duplicate["entry_readiness"] = 91.0
    assert store.append_if_changed("SXT/USDT:USDT", duplicate) is None


def test_same_late_persists_lifecycle_and_terminal_origin_change(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    first = packet("LATE", 60.0, 100)
    first["lifecycle_state"] = "PRE-TRIGGER"
    first["late_origin"] = "ANTI_CHASE"
    second = packet("LATE", 30.0, 110)
    second["lifecycle_state"] = "EXHAUSTED"
    second["late_origin"] = "LIFECYCLE_EXHAUSTED"

    assert store.append_if_changed("SXT/USDT:USDT", first) is not None
    changed = store.append_if_changed("SXT/USDT:USDT", second)

    assert changed is not None
    latest = store.latest_for_symbol("SXT/USDT:USDT")
    assert latest["lifecycle_state"] == "EXHAUSTED"
    assert latest["late_origin"] == "LIFECYCLE_EXHAUSTED"


def test_same_exhausted_late_persists_new_measured_anti_chase_blocker(tmp_path) -> None:
    db_path = migrate_test_database(tmp_path / "registry.db")
    store = EntryDecisionStore(db_path)
    first = packet("LATE", 30.0, 100)
    first["lifecycle_state"] = "EXHAUSTED"
    first["late_origin"] = "LIFECYCLE_EXHAUSTED"
    first["hard_blocked"] = False
    first["block_reasons"] = []
    second = packet("LATE", 30.0, 110)
    second["lifecycle_state"] = "EXHAUSTED"
    second["late_origin"] = "LIFECYCLE_EXHAUSTED"
    second["hard_blocked"] = True
    second["block_reasons"] = ["ANTI_CHASE_HARD_BLOCK"]

    assert store.append_if_changed("SXT/USDT:USDT", first) is not None
    changed = store.append_if_changed("SXT/USDT:USDT", second)

    assert changed is not None
    latest = store.latest_for_symbol("SXT/USDT:USDT")
    assert latest["block_reasons"] == ["ANTI_CHASE_HARD_BLOCK"]
