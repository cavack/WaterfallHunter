from __future__ import annotations

import asyncio
import threading

import waterfallhunter.main as main


def test_periodic_flush_does_not_block_remaining_deadline_work(monkeypatch) -> None:
    candidates = {
        f"T{index:02d}": {"status": "WATCH"}
        for index in range(42)
    }
    monkeypatch.setattr(main.db, "get_all_active_candidates", lambda: candidates)
    monkeypatch.setattr(main, "_reconcile_explicit_entry_expirations", lambda **_: 0)
    monkeypatch.setattr(main, "_reconcile_inactive_actionable_decisions", lambda **_: 0)
    monkeypatch.setattr(main.execution_decision_logger, "record_universe_snapshot", lambda: None)
    monkeypatch.setattr(main.validator.ws_manager, "prune_stale_cache", lambda: None)
    monkeypatch.setattr(
        main,
        "trim_process_heap",
        lambda: {"gc_collected": 0, "malloc_trim_released": False},
    )
    monkeypatch.setattr(main, "_HUNTER_STARTUP_DELAY_SECONDS", 0.0)

    flush_started = threading.Event()
    post_boundary_started = threading.Event()
    release_flush = threading.Event()
    flush_calls = 0

    def flush_evaluations() -> None:
        nonlocal flush_calls
        flush_calls += 1
        if flush_calls != 1:
            return
        flush_started.set()
        assert post_boundary_started.wait(timeout=1.0)
        release_flush.set()

    monkeypatch.setattr(
        main.execution_decision_logger,
        "flush_evaluations",
        flush_evaluations,
    )

    async def evaluate_candidate(symbol: str, data: dict) -> None:
        del data
        index = int(symbol[1:])
        if index < 36:
            return
        post_boundary_started.set()
        while not flush_started.is_set():
            await asyncio.sleep(0)
        while not release_flush.is_set():
            await asyncio.sleep(0)
        if symbol == "T36":
            main._hunter_running = False
            main._hunter_stop_event.set()

    monkeypatch.setattr(main, "evaluate_candidate", evaluate_candidate)

    async def scenario() -> None:
        main._hunter_stop_event.clear()
        hunter = asyncio.create_task(main.hunter_loop(interval_seconds=60.0))
        try:
            await asyncio.wait_for(
                asyncio.to_thread(flush_started.wait, 1.0),
                timeout=1.2,
            )
            await asyncio.wait_for(
                asyncio.to_thread(post_boundary_started.wait, 1.0),
                timeout=1.2,
            )
        finally:
            main._hunter_running = False
            main._hunter_stop_event.set()
            release_flush.set()
            await asyncio.wait_for(hunter, timeout=1.0)

    asyncio.run(scenario())

    assert flush_calls >= 1
    assert post_boundary_started.is_set()
