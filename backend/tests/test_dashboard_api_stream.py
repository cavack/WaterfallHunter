from __future__ import annotations

import asyncio
import json
import threading

from fastapi import Response

import waterfallhunter.main as main
from waterfallhunter.core.dashboard_stream import DashboardEventBuffer


DECISION_COUNTS = {
    "ENTRY_READY": 0,
    "FORMING": 0,
    "ACTIVE": 0,
    "LATE": 0,
    "INVALIDATED": 0,
    "EXPIRED": 0,
    "NO_TRADE": 1,
    "UNAVAILABLE": 0,
}

DECISION_TERMINAL = {
    "contract_version": "decision_terminal_v1",
    "counts": DECISION_COUNTS,
    "entry_ready": [],
    "forming": [],
    "active": [],
    "late": [],
    "zero_entry_ready_diagnostics": {
        "entry_ready_zero": True,
        "evaluated_candidates": 1,
        "top_reasons": [],
    },
    "recent_changes": [],
}

PAYLOAD = {
    "total": 1,
    "candidates": {"TEST": {"status": "WATCH"}},
    "decision_terminal": DECISION_TERMINAL,
    "final_ranking": {"version": "test"},
    "signal_funnel": {"version": "test"},
}


def test_dashboard_snapshot_rejects_partial_decision_terminal() -> None:
    from pydantic import ValidationError
    from waterfallhunter.core.dashboard_stream import DashboardSnapshot

    partial = {**PAYLOAD, "decision_terminal": {"contract_version": "decision_terminal_v1", "counts": {"ENTRY_READY": 1}}}
    try:
        DashboardSnapshot(
            contract_version="dashboard_snapshot_v2",
            schema_version="2.0",
            snapshot_version=1,
            generated_at=1.0,
            state="READY",
            **partial,
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("partial decision terminal must be rejected")


def test_dashboard_snapshot_rejects_terminal_counts_that_do_not_match_candidates() -> None:
    from pydantic import ValidationError
    from waterfallhunter.core.dashboard_stream import DashboardSnapshot

    bad_terminal = {
        **DECISION_TERMINAL,
        "counts": {**DECISION_COUNTS, "FORMING": 1},
        "forming": ["TEST"],
    }
    try:
        DashboardSnapshot(
            contract_version="dashboard_snapshot_v2",
            schema_version="2.0",
            snapshot_version=1,
            generated_at=1.0,
            state="READY",
            **{**PAYLOAD, "decision_terminal": bad_terminal},
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("terminal counts must reconcile to candidate total")


def test_dashboard_snapshot_rejects_terminal_diagnostic_candidate_count_mismatch() -> None:
    from pydantic import ValidationError
    from waterfallhunter.core.dashboard_stream import DashboardSnapshot

    bad_terminal = {
        **DECISION_TERMINAL,
        "zero_entry_ready_diagnostics": {
            **DECISION_TERMINAL["zero_entry_ready_diagnostics"],
            "evaluated_candidates": 2,
        },
    }
    try:
        DashboardSnapshot(
            contract_version="dashboard_snapshot_v2",
            schema_version="2.0",
            snapshot_version=1,
            generated_at=1.0,
            state="READY",
            **{**PAYLOAD, "decision_terminal": bad_terminal},
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("terminal diagnostics must reconcile to candidate total")


def test_polling_endpoint_returns_a_valid_versioned_no_store_snapshot(monkeypatch) -> None:
    buffer = DashboardEventBuffer()
    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "get_formatted_candidates", lambda **_: PAYLOAD)
    response = Response()

    snapshot = asyncio.run(main.get_candidates(response))

    assert snapshot is not None
    assert snapshot.contract_version == "dashboard_snapshot_v2"
    assert snapshot.schema_version == "2.0"
    assert snapshot.state == "READY"
    assert snapshot.total == 1
    assert response.headers["Cache-Control"] == "no-store"
    assert buffer.snapshot_version == 0
    assert buffer.replay_after("1") is None



def test_polling_refreshes_stale_snapshot_even_while_sse_client_is_registered(monkeypatch) -> None:
    buffer = DashboardEventBuffer()
    buffer.publish_snapshot(PAYLOAD, generated_at=10.0, full_snapshot=True)
    builds: list[float] = []

    def fresh_payload(*, evaluation_time: float):
        builds.append(evaluation_time)
        return PAYLOAD

    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "_dashboard_preview_cache", None)
    monkeypatch.setattr(main, "_sse_clients", {object()})
    monkeypatch.setattr(main, "_get_live_dashboard_payload", fresh_payload)
    monkeypatch.setattr(main.time, "time", lambda: 100.0)

    snapshot = asyncio.run(main._get_dashboard_poll_snapshot())

    assert builds == [100.0]
    assert snapshot.generated_at == 100.0

def test_raw_candidates_endpoint_returns_full_versioned_snapshot(monkeypatch) -> None:
    buffer = DashboardEventBuffer()
    raw_payload = {
        **PAYLOAD,
        "candidates": {
            "TEST": {
                "status": "WATCH",
                "metrics": {"raw_heavy_field": {"kept": True}},
            }
        },
    }
    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "get_formatted_candidates", lambda **_: raw_payload)
    response = Response()

    snapshot = asyncio.run(main.get_raw_candidates(response))

    assert snapshot.contract_version == "dashboard_snapshot_v2"
    assert snapshot.candidates["TEST"]["metrics"]["raw_heavy_field"] == {"kept": True}
    assert response.headers["Cache-Control"] == "no-store"



def test_raw_candidates_aggregation_runs_off_event_loop_thread(monkeypatch) -> None:
    buffer = DashboardEventBuffer()
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []

    def build_raw_payload(**_):
        worker_threads.append(threading.get_ident())
        return PAYLOAD

    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "get_formatted_candidates", build_raw_payload)

    snapshot = asyncio.run(main.get_raw_candidates(Response()))

    assert snapshot.state == "READY"
    assert worker_threads
    assert worker_threads[0] != caller_thread

def test_stream_replays_after_last_event_id_and_sets_proxy_safe_headers(monkeypatch) -> None:
    buffer = DashboardEventBuffer()
    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "get_formatted_candidates", lambda **_: PAYLOAD)
    first = buffer.publish_snapshot(PAYLOAD, generated_at=10.0, full_snapshot=True)
    second = buffer.publish_snapshot(PAYLOAD, generated_at=11.0, full_snapshot=False)

    async def first_chunk() -> tuple[object, str]:
        response = await main.stream_candidates(last_event_id=first.event_id)
        iterator = response.body_iterator
        try:
            chunk = await anext(iterator)
        finally:
            await iterator.aclose()
        return response, chunk

    response, chunk = asyncio.run(first_chunk())
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert response.headers["x-accel-buffering"] == "no"
    assert chunk.startswith(f"id: {second.event_id}\nevent: snapshot\n")
    event = json.loads(chunk.split("data: ", 1)[1])
    assert event["replayed"] is True
    assert event["full_snapshot"] is False


def test_stream_falls_back_to_full_snapshot_when_replay_is_unavailable(monkeypatch) -> None:
    buffer = DashboardEventBuffer(replay_limit=1)
    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "get_formatted_candidates", lambda **_: PAYLOAD)
    old = buffer.publish_snapshot(PAYLOAD, generated_at=10.0, full_snapshot=True)
    buffer.publish_heartbeat(generated_at=11.0)

    async def first_chunk() -> str:
        response = await main.stream_candidates(last_event_id=old.event_id)
        iterator = response.body_iterator
        try:
            return await anext(iterator)
        finally:
            await iterator.aclose()

    chunk = asyncio.run(first_chunk())
    event = json.loads(chunk.split("data: ", 1)[1])
    assert event["event_type"] == "snapshot"
    assert event["full_snapshot"] is True
    assert event["payload"]["state"] == "READY"


def test_broadcast_tolerates_client_set_mutation_during_delivery(monkeypatch) -> None:
    buffer = DashboardEventBuffer()
    event = buffer.publish_heartbeat(generated_at=10.0)
    clients: set[object] = set()

    class SelfRemovingQueue:
        def put_nowait(self, delivered_event) -> None:
            assert delivered_event is event
            clients.discard(self)

    queue = SelfRemovingQueue()
    clients.add(queue)
    monkeypatch.setattr(main, "_sse_clients", clients)

    main._broadcast_dashboard_event(event)

    assert clients == set()


def test_dashboard_snapshot_normalizes_non_finite_numbers_to_unavailable(monkeypatch) -> None:
    buffer = DashboardEventBuffer()
    unsafe_payload = {
        "total": 1,
        "candidates": {
            "TEST": {
                "status": "WATCH",
                "metrics": {
                    "entry_decision": {
                        "decision": "NO_TRADE",
                        "entry_readiness": 0.0,
                        "evidence_coverage_pct": 0.0,
                        "evidence_summary": {
                            "execution": {"spread_pct": float("nan")},
                        },
                    },
                },
            }
        },
        "decision_terminal": DECISION_TERMINAL,
        "final_ranking": {"version": "test"},
        "signal_funnel": {"version": "test"},
    }
    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "get_formatted_candidates", lambda **_: unsafe_payload)

    event = main._publish_dashboard_snapshot(
        full_snapshot=False,
        only_if_changed=True,
    )

    assert event is not None
    assert event.payload is not None
    assert (
        event.payload.candidates["TEST"]["metrics"]["entry_decision"]
        ["evidence_summary"]["execution"]["spread_pct"]
        is None
    )


def test_polling_snapshot_normalizes_non_finite_numbers_to_unavailable(monkeypatch) -> None:
    buffer = DashboardEventBuffer()
    unsafe_payload = {
        "total": 1,
        "candidates": {
            "TEST": {
                "status": "WATCH",
                "metrics": {
                    "entry_decision": {
                        "decision": "NO_TRADE",
                        "entry_readiness": 0.0,
                        "evidence_coverage_pct": 0.0,
                        "evidence_summary": {
                            "execution": {"spread_pct": float("inf")},
                        },
                    },
                },
            }
        },
        "decision_terminal": DECISION_TERMINAL,
        "final_ranking": {"version": "test"},
        "signal_funnel": {"version": "test"},
    }
    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "get_formatted_candidates", lambda **_: unsafe_payload)
    response = Response()

    snapshot = asyncio.run(main.get_candidates(response))

    assert (
        snapshot.candidates["TEST"]["metrics"]["entry_decision"]
        ["evidence_summary"]["execution"]["spread_pct"]
        is None
    )


def test_sse_snapshot_build_failure_is_contained(monkeypatch) -> None:
    def fail(**_):
        raise RuntimeError("synthetic dashboard aggregation failure")

    monkeypatch.setattr(main, "_publish_dashboard_snapshot", fail)

    assert main._publish_dashboard_snapshot_safely(
        full_snapshot=False, only_if_changed=True
    ) is None


def test_initial_sse_snapshot_aggregation_runs_off_event_loop_thread(monkeypatch) -> None:
    buffer = DashboardEventBuffer()
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []

    def aggregate(**_):
        worker_threads.append(threading.get_ident())
        return buffer.publish_snapshot(PAYLOAD, generated_at=20.0, full_snapshot=True)

    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "_publish_dashboard_snapshot_safely", aggregate)
    monkeypatch.setattr(main, "_sse_clients", set())

    async def first_chunk() -> str:
        response = await main.stream_candidates(last_event_id="missing")
        iterator = response.body_iterator
        try:
            return await asyncio.wait_for(anext(iterator), timeout=1.0)
        finally:
            await iterator.aclose()

    chunk = asyncio.run(first_chunk())
    assert "event: snapshot" in chunk
    assert worker_threads
    assert worker_threads[0] != caller_thread


def test_initial_sse_snapshot_failure_waits_for_next_queued_event(monkeypatch) -> None:
    buffer = DashboardEventBuffer()

    def fail(**_):
        raise RuntimeError("synthetic initial aggregation failure")

    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "_publish_dashboard_snapshot", fail)
    monkeypatch.setattr(main, "_sse_clients", set())

    async def first_chunk() -> str:
        response = await main.stream_candidates(last_event_id="missing")
        iterator = response.body_iterator
        pending = asyncio.create_task(anext(iterator))
        try:
            await asyncio.sleep(0)
            assert not pending.done()
            queue = next(iter(main._sse_clients))
            event = buffer.publish_snapshot(PAYLOAD, generated_at=20.0, full_snapshot=True)
            queue.put_nowait(event)
            return await asyncio.wait_for(pending, timeout=1.0)
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await iterator.aclose()

    chunk = asyncio.run(first_chunk())
    event = json.loads(chunk.split("data: ", 1)[1])
    assert event["event_type"] == "snapshot"
    assert event["payload"]["state"] == "READY"


def test_initial_sse_snapshot_preserves_queued_event_order(monkeypatch) -> None:
    buffer = DashboardEventBuffer()
    started = threading.Event()
    release = threading.Event()

    def aggregate(**_):
        started.set()
        assert release.wait(timeout=1.0)
        return buffer.publish_snapshot(PAYLOAD, generated_at=20.0, full_snapshot=True)

    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "_publish_dashboard_snapshot_safely", aggregate)
    monkeypatch.setattr(main, "_sse_clients", set())

    async def first_two_event_ids() -> list[int]:
        response = await main.stream_candidates(last_event_id="missing")
        iterator = response.body_iterator
        first = asyncio.create_task(anext(iterator))
        try:
            assert await asyncio.to_thread(started.wait, 1.0)
            queue = next(iter(main._sse_clients))
            earlier = buffer.publish_heartbeat(generated_at=19.0)
            queue.put_nowait(earlier)
            release.set()
            first_chunk = await asyncio.wait_for(first, timeout=1.0)
            second_chunk = await asyncio.wait_for(anext(iterator), timeout=1.0)
            return [
                int(json.loads(chunk.split("data: ", 1)[1])["event_id"])
                for chunk in (first_chunk, second_chunk)
            ]
        finally:
            release.set()
            if not first.done():
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
            await iterator.aclose()

    assert asyncio.run(first_two_event_ids()) == [1, 2]


def test_stale_poll_aggregation_runs_off_event_loop_thread(monkeypatch) -> None:
    buffer = DashboardEventBuffer()
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []

    def aggregate(*, evaluation_time: float):
        worker_threads.append(threading.get_ident())
        return PAYLOAD

    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "_dashboard_preview_cache", None)
    monkeypatch.setattr(main, "_get_live_dashboard_payload", aggregate)

    snapshot = asyncio.run(main.get_candidates(Response()))

    assert snapshot.state == "READY"
    assert worker_threads
    assert worker_threads[0] != caller_thread


def test_sse_snapshot_rebuild_is_coalesced_to_five_second_budget() -> None:
    assert main._DASHBOARD_SNAPSHOT_BROADCAST_INTERVAL_SECONDS == 5.0
    assert main._dashboard_snapshot_broadcast_due(10.0, 14.999) is False
    assert main._dashboard_snapshot_broadcast_due(10.0, 15.0) is True
    assert main._dashboard_snapshot_broadcast_due(0.0, 1.0) is True


def test_production_dashboard_replay_window_is_memory_bounded() -> None:
    assert getattr(main, "_DASHBOARD_REPLAY_EVENT_LIMIT", None) == 8
    assert main._dashboard_event_buffer._events.maxlen == 8


def test_periodic_dashboard_broadcast_ignores_derived_age_only_changes(monkeypatch) -> None:
    """Derived display ages must not make an unchanged business snapshot look new."""
    buffer = DashboardEventBuffer()
    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    payloads = [
        {
            "total": 1,
            "candidates": {
                "TEST": {
                    "status": "WATCH",
                    "age_seconds": 1.0,
                    "analysis_age_seconds": 1.0,
                    "reference_age_seconds": 2.0,
                }
            },
            "decision_terminal": DECISION_TERMINAL,
            "final_ranking": {"version": "test"},
            "signal_funnel": {"version": "test"},
        },
        {
            "total": 1,
            "candidates": {
                "TEST": {
                    "status": "WATCH",
                    "age_seconds": 2.0,
                    "analysis_age_seconds": 2.0,
                    "reference_age_seconds": 3.0,
                }
            },
            "decision_terminal": DECISION_TERMINAL,
            "final_ranking": {"version": "test"},
            "signal_funnel": {"version": "test"},
        },
    ]
    monkeypatch.setattr(main, "get_formatted_candidates", lambda **_: payloads.pop(0))

    first = main._publish_dashboard_snapshot(full_snapshot=False, only_if_changed=True)
    second = main._publish_dashboard_snapshot(full_snapshot=False, only_if_changed=True)

    assert first is not None
    assert second is None
    assert buffer.snapshot_version == 1


def test_sse_client_queue_is_bounded_and_latest_event_wins(monkeypatch) -> None:
    """A slow SSE client must retain only a tiny rolling set of full snapshots."""
    queue = main._new_dashboard_client_queue()
    assert queue.maxsize == 2

    buffer = DashboardEventBuffer()
    first = buffer.publish_heartbeat(generated_at=10.0)
    second = buffer.publish_heartbeat(generated_at=11.0)
    latest = buffer.publish_heartbeat(generated_at=12.0)
    queue.put_nowait(first)
    queue.put_nowait(second)
    monkeypatch.setattr(main, "_sse_clients", {queue})

    main._broadcast_dashboard_event(latest)

    assert queue.get_nowait() is second
    assert queue.get_nowait() is latest
    assert queue.empty()


def test_sse_broadcaster_aggregates_off_event_loop_thread(monkeypatch) -> None:
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []
    queue = asyncio.Queue(maxsize=1)

    def aggregate(**_):
        worker_threads.append(threading.get_ident())
        main._hunter_running = False
        return None

    monkeypatch.setattr(main, "_hunter_running", True)
    monkeypatch.setattr(main, "_sse_clients", {queue})
    monkeypatch.setattr(main, "_publish_dashboard_snapshot_safely", aggregate)

    asyncio.run(asyncio.wait_for(main.sse_broadcaster(), timeout=5.0))

    assert worker_threads
    assert worker_threads[0] != caller_thread


def test_sse_heartbeat_continues_while_snapshot_aggregation_is_blocked(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    heartbeats: list[object] = []
    queue = asyncio.Queue(maxsize=2)

    def aggregate(**_):
        started.set()
        release.wait(3.0)
        return None

    def broadcast(event) -> None:
        if getattr(event, "event_type", None) == "heartbeat":
            heartbeats.append(event)
            if len(heartbeats) >= 2:
                release.set()
                main._hunter_running = False

    monkeypatch.setattr(main, "_hunter_running", True)
    monkeypatch.setattr(main, "_sse_clients", {queue})
    monkeypatch.setattr(main, "_DASHBOARD_HEARTBEAT_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(main, "_publish_dashboard_snapshot_safely", aggregate)
    monkeypatch.setattr(main, "_broadcast_dashboard_event", broadcast)

    asyncio.run(asyncio.wait_for(main.sse_broadcaster(), timeout=5.0))

    assert started.is_set()
    assert len(heartbeats) >= 2
    assert release.is_set()
