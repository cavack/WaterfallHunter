from __future__ import annotations

import asyncio

import waterfallhunter.main as main


def _stub_shutdown_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(main, "_background_tasks", set())
    monkeypatch.setattr(main, "_lbank_execution_shadow_worker", None)
    monkeypatch.setattr(main, "_signal_settlement_worker", None)
    monkeypatch.setattr(main, "_hunter_task", None, raising=False)

    monkeypatch.setattr(main.feature_replay_worker, "stop", lambda: None)
    monkeypatch.setattr(main.scanner, "stop", lambda: None)

    async def noop_close() -> None:
        return None

    monkeypatch.setattr(main.scanner, "close", noop_close)
    monkeypatch.setattr(main.validator, "close_all", noop_close)

    async def refresh_live_references() -> None:
        return None

    monkeypatch.setattr(
        main.scanner,
        "refresh_live_references",
        refresh_live_references,
    )
    monkeypatch.setattr(
        main.execution_decision_logger,
        "flush_evaluations",
        lambda: None,
    )
    monkeypatch.setattr(
        main.execution_decision_logger,
        "record_universe_snapshot",
        lambda: None,
    )
    monkeypatch.setattr(
        main.validator.ws_manager,
        "prune_stale_cache",
        lambda: None,
    )
    monkeypatch.setattr(main, "_HUNTER_STARTUP_DELAY_SECONDS", 0.0)


def test_shutdown_allows_inflight_hunter_evaluation_to_finish(monkeypatch) -> None:
    _stub_shutdown_dependencies(monkeypatch)
    monkeypatch.setattr(
        main.db,
        "get_all_active_candidates",
        lambda: {"DRAIN": {}},
    )

    started: asyncio.Event
    release: asyncio.Event
    completed: asyncio.Event
    cancelled = False

    async def scenario() -> None:
        nonlocal started, release, completed, cancelled
        started = asyncio.Event()
        release = asyncio.Event()
        completed = asyncio.Event()
        main._hunter_stop_event.clear()

        async def evaluate_candidate(symbol: str, data: dict) -> None:
            nonlocal cancelled
            del symbol, data
            started.set()
            try:
                await release.wait()
                completed.set()
            except asyncio.CancelledError:
                cancelled = True
                raise

        monkeypatch.setattr(main, "evaluate_candidate", evaluate_candidate)

        hunter_task = main._start_background_task(
            main.hunter_loop(interval_seconds=60)
        )
        main._hunter_task = hunter_task
        await started.wait()

        shutdown_task = asyncio.create_task(main.shutdown_event())
        await asyncio.sleep(0)
        release.set()

        await shutdown_task
        await asyncio.gather(hunter_task, return_exceptions=True)

        assert completed.is_set()
        assert cancelled is False

    asyncio.run(scenario())


def test_shutdown_wakes_idle_hunter_without_waiting_for_interval(monkeypatch) -> None:
    _stub_shutdown_dependencies(monkeypatch)
    monkeypatch.setattr(
        main.db,
        "get_all_active_candidates",
        lambda: {},
    )

    async def scenario() -> None:
        class ObservedEvent(asyncio.Event):
            def __init__(self) -> None:
                super().__init__()
                self.wait_started = asyncio.Event()

            async def wait(self) -> bool:
                self.wait_started.set()
                return await super().wait()

        stop_event = ObservedEvent()
        monkeypatch.setattr(main, "_hunter_stop_event", stop_event)

        hunter_task = main._start_background_task(
            main.hunter_loop(interval_seconds=60)
        )
        main._hunter_task = hunter_task

        await asyncio.wait_for(stop_event.wait_started.wait(), timeout=1.0)
        await asyncio.wait_for(main.shutdown_event(), timeout=1.0)
        await asyncio.gather(hunter_task, return_exceptions=True)

        assert stop_event.is_set()
        assert hunter_task.done()
        assert hunter_task.cancelled() is False

    asyncio.run(scenario())
