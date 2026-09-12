from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from waterfallhunter.core.dashboard_stream import (
    DashboardEventBuffer,
    DashboardSnapshot,
    serialize_sse_event,
)


def _payload(symbol: str = "TEST") -> dict:
    return {
        "total": 1,
        "candidates": {symbol: {"status": "WATCH"}},
        "decision_terminal": {
            "contract_version": "decision_terminal_v1",
            "counts": {
                "ENTRY_READY": 0, "FORMING": 0, "ACTIVE": 0, "LATE": 0,
                "INVALIDATED": 0, "EXPIRED": 0, "NO_TRADE": 1, "UNAVAILABLE": 0,
            },
            "entry_ready": [], "forming": [], "active": [], "late": [],
            "zero_entry_ready_diagnostics": {
                "entry_ready_zero": True,
                "evaluated_candidates": 1,
                "top_reasons": [],
            },
            "recent_changes": [],
        },
        "final_ranking": {"version": "test"},
        "signal_funnel": {"version": "test"},
    }


def test_snapshot_boundary_rejects_mismatched_counts_and_nonfinite_clock() -> None:
    buffer = DashboardEventBuffer()
    invalid_count_payload = {**_payload(), "total": 0}
    with pytest.raises(ValidationError, match="candidate count"):
        buffer.publish_snapshot(
            invalid_count_payload,
            generated_at=10.0,
            full_snapshot=True,
        )
    valid_payload = _payload()
    with pytest.raises(ValidationError):
        buffer.publish_snapshot(
            valid_payload,
            generated_at=float("nan"),
            full_snapshot=True,
        )


def test_preview_and_latest_snapshot_are_read_only() -> None:
    buffer = DashboardEventBuffer()
    preview = buffer.preview_snapshot(_payload(), generated_at=10.0)

    assert preview.snapshot_version == 1
    assert buffer.snapshot_version == 0
    assert buffer.latest_snapshot() is None
    published = buffer.publish_snapshot(_payload(), generated_at=11.0, full_snapshot=True)
    assert buffer.latest_snapshot() == published.payload
    assert buffer.snapshot_version == 1


def test_periodic_snapshot_deduplicates_unchanged_business_payload() -> None:
    buffer = DashboardEventBuffer()
    first = buffer.publish_snapshot_if_changed(_payload(), generated_at=10.0)
    duplicate = buffer.publish_snapshot_if_changed(_payload(), generated_at=11.0)
    changed = buffer.publish_snapshot_if_changed(
        _payload("CHANGED"),
        generated_at=12.0,
    )

    assert first is not None
    assert duplicate is None
    assert changed is not None
    assert changed.snapshot_version == 2


def test_sse_contract_has_hash_monotonic_ids_and_parseable_wire_format() -> None:
    buffer = DashboardEventBuffer()
    first = buffer.publish_snapshot(_payload(), generated_at=10.0, full_snapshot=True)
    heartbeat = buffer.publish_heartbeat(generated_at=11.0)

    assert first.event_id == "1"
    assert first.snapshot_version == 1
    assert first.full_snapshot is True
    assert len(first.payload_hash) == 64
    assert heartbeat.event_id == "2"
    assert heartbeat.last_event_id == "1"
    assert heartbeat.payload is None
    wire = serialize_sse_event(first)
    assert wire.startswith("id: 1\nevent: snapshot\ndata: ")
    parsed = json.loads(wire.split("data: ", 1)[1])
    assert DashboardSnapshot.model_validate(parsed["payload"]).snapshot_version == 1


def test_last_event_id_replays_in_order_or_requires_full_snapshot() -> None:
    buffer = DashboardEventBuffer(replay_limit=3)
    first = buffer.publish_snapshot(_payload("ONE"), generated_at=10.0, full_snapshot=True)
    second = buffer.publish_snapshot(_payload("TWO"), generated_at=11.0, full_snapshot=False)
    third = buffer.publish_heartbeat(generated_at=12.0)

    replay = buffer.replay_after(first.event_id)
    assert replay is not None
    assert [event.event_id for event in replay] == [second.event_id, third.event_id]
    assert all(event.replayed is True for event in replay)
    assert buffer.replay_after(third.event_id) == []
    assert buffer.replay_after(None) is None
    assert buffer.replay_after("invalid") is None

    fourth = buffer.publish_heartbeat(generated_at=13.0)
    replay_from_superseded = buffer.replay_after(first.event_id)
    assert replay_from_superseded is not None
    assert [event.event_id for event in replay_from_superseded] == [
        second.event_id,
        third.event_id,
        fourth.event_id,
    ]

    buffer.publish_heartbeat(generated_at=14.0)
    assert buffer.replay_after(first.event_id) is None


def test_periodic_snapshot_ignores_nested_entry_decision_evaluation_clock() -> None:
    buffer = DashboardEventBuffer()
    first_payload = _payload()
    first_payload["candidates"]["TEST"]["entry_decision"] = {
        "decision": "INVALIDATED",
        "evaluated_at": 100,
        "block_reasons": ["STALE_ANALYSIS"],
    }
    second_payload = _payload()
    second_payload["candidates"]["TEST"]["entry_decision"] = {
        "decision": "INVALIDATED",
        "evaluated_at": 101,
        "block_reasons": ["STALE_ANALYSIS"],
    }

    first = buffer.publish_snapshot_if_changed(first_payload, generated_at=100.0)
    duplicate = buffer.publish_snapshot_if_changed(second_payload, generated_at=101.0)

    assert first is not None
    assert duplicate is None


def test_new_snapshot_supersedes_older_full_snapshot_payloads() -> None:
    buffer = DashboardEventBuffer(replay_limit=8)
    first = buffer.publish_snapshot(
        _payload("ONE"), generated_at=10.0, full_snapshot=True
    )
    first_heartbeat = buffer.publish_heartbeat(generated_at=11.0)
    second = buffer.publish_snapshot(
        _payload("TWO"), generated_at=12.0, full_snapshot=False
    )
    second_heartbeat = buffer.publish_heartbeat(generated_at=13.0)

    retained_snapshots = [event for event in buffer._events if event.payload is not None]

    assert [event.event_id for event in retained_snapshots] == [second.event_id]
    assert [event.event_id for event in buffer._events] == [
        second.event_id,
        second_heartbeat.event_id,
    ]
    assert buffer.latest_snapshot() == second.payload
    replay_from_first = buffer.replay_after(first.event_id)
    replay_from_heartbeat = buffer.replay_after(first_heartbeat.event_id)
    assert replay_from_first is not None
    assert replay_from_heartbeat is not None
    assert [event.event_id for event in replay_from_first] == [
        second.event_id,
        second_heartbeat.event_id,
    ]
    assert [event.event_id for event in replay_from_heartbeat] == [
        second.event_id,
        second_heartbeat.event_id,
    ]
    assert all(event.replayed is True for event in replay_from_first)
