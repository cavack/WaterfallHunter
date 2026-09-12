from __future__ import annotations

import asyncio
import time

from fastapi import Response

from waterfallhunter import main
from waterfallhunter.core.dashboard_stream import DashboardEventBuffer


def test_bootstrap_candidate_poll_builds_preview_once_for_concurrent_clients(
    monkeypatch,
) -> None:
    """Concurrent bootstrap polls must share one expensive preview build."""

    buffer = DashboardEventBuffer(replay_limit=100)
    monkeypatch.setattr(
        main,
        "_dashboard_event_buffer",
        buffer,
    )

    build_calls = 0

    def build_candidates(*, evaluation_time=None):
        nonlocal build_calls
        build_calls += 1
        return {
            "total": 0,
            "candidates": {},
            "decision_terminal": {
                "contract_version": "decision_terminal_v1",
                "counts": {
                    "ENTRY_READY": 0, "FORMING": 0, "ACTIVE": 0, "LATE": 0,
                    "INVALIDATED": 0, "EXPIRED": 0, "NO_TRADE": 0, "UNAVAILABLE": 0,
                },
                "entry_ready": [], "forming": [], "active": [], "late": [],
                "zero_entry_ready_diagnostics": {
                    "entry_ready_zero": True,
                    "evaluated_candidates": 0,
                    "top_reasons": [],
                },
                "recent_changes": [],
            },
            "final_ranking": {},
            "signal_funnel": {},
        }

    monkeypatch.setattr(
        main,
        "get_formatted_candidates",
        build_candidates,
    )

    async def scenario() -> None:
        snapshots = await asyncio.gather(
            *(
                main.get_candidates(Response())
                for _ in range(8)
            )
        )

        assert len(snapshots) == 8
        assert all(snapshot.snapshot_version == 1 for snapshot in snapshots)
        assert all(snapshot.state == "READY" for snapshot in snapshots)
        assert len({snapshot.generated_at for snapshot in snapshots}) == 1

    asyncio.run(scenario())

    assert build_calls == 1
    # Poll bootstrap stays a read-only preview. It must not advance the SSE
    # event/snapshot sequence or become a retained replay snapshot.
    assert buffer.snapshot_version == 0
    assert buffer.latest_snapshot() is None


def test_poll_refreshes_retained_snapshot_after_it_becomes_stale(monkeypatch) -> None:
    buffer = DashboardEventBuffer(replay_limit=100)
    buffer.publish_snapshot(
        {
            "total": 1,
            "candidates": {"OLD": {"status": "WATCH"}},
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
            "final_ranking": {},
            "signal_funnel": {},
        },
        generated_at=time.time() - main._DASHBOARD_PREVIEW_CACHE_SECONDS - 1,
        full_snapshot=True,
    )
    monkeypatch.setattr(main, "_dashboard_event_buffer", buffer)
    monkeypatch.setattr(main, "_sse_clients", set())
    monkeypatch.setattr(main, "_dashboard_preview_cache", None)
    monkeypatch.setattr(
        main,
        "get_formatted_candidates",
        lambda *, evaluation_time=None: {
            "total": 1,
            "candidates": {"NEW": {"status": "ARMED"}},
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
            "final_ranking": {},
            "signal_funnel": {},
        },
    )

    snapshot = asyncio.run(main._get_dashboard_poll_snapshot())

    assert "NEW" in snapshot.candidates
    assert "OLD" not in snapshot.candidates
