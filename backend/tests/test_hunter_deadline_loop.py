from __future__ import annotations

import asyncio

import waterfallhunter.main as main


def _stub_runtime_maintenance(monkeypatch) -> None:
    async def refresh_live_references() -> None:
        return None

    monkeypatch.setattr(main.scanner, "refresh_live_references", refresh_live_references)
    monkeypatch.setattr(main, "_reconcile_explicit_entry_expirations", lambda **_: 0)
    monkeypatch.setattr(main, "_reconcile_inactive_actionable_decisions", lambda **_: 0)
    monkeypatch.setattr(main.execution_decision_logger, "flush_evaluations", lambda: None)
    monkeypatch.setattr(main.execution_decision_logger, "record_universe_snapshot", lambda: None)
    monkeypatch.setattr(main.validator.ws_manager, "prune_stale_cache", lambda: None)
    monkeypatch.setattr(
        main,
        "trim_process_heap",
        lambda: {"gc_collected": 0, "malloc_trim_released": False},
    )
    monkeypatch.setattr(main, "_HUNTER_STARTUP_DELAY_SECONDS", 0.0)


def test_hunter_discovers_due_pretrigger_without_waiting_for_slow_watch_batch(
    monkeypatch,
) -> None:
    _stub_runtime_maintenance(monkeypatch)
    initial = {
        f"W{index:02d}": {"status": "WATCH"}
        for index in range(main.DEFAULT_EVALUATION_CONCURRENCY)
    }
    promoted = {
        **initial,
        "PRE": {"status": "PRE-TRIGGER"},
    }
    db_calls = 0

    def get_candidates() -> dict:
        nonlocal db_calls
        db_calls += 1
        return initial if db_calls == 1 else promoted

    monkeypatch.setattr(main.db, "get_all_active_candidates", get_candidates)

    async def scenario() -> None:
        started_watch: set[str] = set()
        all_watch_started = asyncio.Event()
        release_one_watch = asyncio.Event()
        release_remaining_watch = asyncio.Event()
        pre_started = asyncio.Event()
        main._hunter_stop_event.clear()

        async def evaluate_candidate(symbol: str, data: dict) -> None:
            del data
            if symbol == "PRE":
                pre_started.set()
                main._hunter_running = False
                main._hunter_stop_event.set()
                return

            started_watch.add(symbol)
            if len(started_watch) == len(initial):
                all_watch_started.set()
            if symbol == "W00":
                await release_one_watch.wait()
            else:
                await release_remaining_watch.wait()

        monkeypatch.setattr(main, "evaluate_candidate", evaluate_candidate)

        hunter = asyncio.create_task(main.hunter_loop(interval_seconds=0.01))
        try:
            await asyncio.wait_for(all_watch_started.wait(), timeout=1.0)
            release_one_watch.set()
            await asyncio.wait_for(pre_started.wait(), timeout=0.4)
        finally:
            main._hunter_running = False
            main._hunter_stop_event.set()
            release_one_watch.set()
            release_remaining_watch.set()
            await asyncio.wait_for(hunter, timeout=1.0)

        assert db_calls >= 2
        assert pre_started.is_set()

    asyncio.run(scenario())


def test_hunter_offloads_candidate_db_read_from_event_loop(monkeypatch) -> None:
    import threading

    _stub_runtime_maintenance(monkeypatch)
    caller_thread = threading.get_ident()
    observed_threads: list[int] = []

    def get_candidates() -> dict:
        observed_threads.append(threading.get_ident())
        return {}

    monkeypatch.setattr(main.db, "get_all_active_candidates", get_candidates)

    async def scenario() -> None:
        main._hunter_stop_event.clear()
        hunter = asyncio.create_task(main.hunter_loop(interval_seconds=0.01))
        try:
            for _ in range(100):
                if observed_threads:
                    break
                await asyncio.sleep(0.01)
        finally:
            main._hunter_running = False
            main._hunter_stop_event.set()
            await asyncio.wait_for(hunter, timeout=1.0)

    asyncio.run(scenario())
    assert observed_threads
    assert observed_threads[0] != caller_thread
