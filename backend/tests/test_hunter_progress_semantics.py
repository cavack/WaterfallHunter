from __future__ import annotations

import asyncio

import waterfallhunter.main as main


def _stub_hunter_cycle(monkeypatch, candidates: dict) -> None:
    async def refresh_live_references() -> None:
        return None

    monkeypatch.setattr(
        main.scanner,
        "refresh_live_references",
        refresh_live_references,
    )
    monkeypatch.setattr(
        main.db,
        "get_all_active_candidates",
        lambda: candidates,
    )
    monkeypatch.setattr(
        main.execution_decision_logger,
        "flush_evaluations",
        lambda: None,
    )
    def stop_after_cycle() -> None:
        main._hunter_running = False
        main._hunter_stop_event.set()

    monkeypatch.setattr(
        main.execution_decision_logger,
        "record_universe_snapshot",
        stop_after_cycle,
    )
    monkeypatch.setattr(
        main.validator.ws_manager,
        "prune_stale_cache",
        lambda: None,
    )
    monkeypatch.setattr(main, "_HUNTER_STARTUP_DELAY_SECONDS", 0.0)
    main._hunter_stop_event.clear()


def test_failed_candidate_evaluation_does_not_mark_hunter_progress(
    monkeypatch,
) -> None:
    _stub_hunter_cycle(monkeypatch, {"TEST": {}})
    monkeypatch.setattr(main, "_hunter_last_progress_at", None)
    monkeypatch.setattr(main, "_hunter_last_completed_at", None)

    async def failing_evaluation(symbol: str, data: dict) -> None:
        del symbol, data
        raise RuntimeError("candidate evaluation failed")

    monkeypatch.setattr(main, "evaluate_candidate", failing_evaluation)

    asyncio.run(main.hunter_loop(interval_seconds=0))

    assert main._hunter_last_progress_at is None


def test_successful_candidate_evaluation_marks_hunter_progress(
    monkeypatch,
) -> None:
    _stub_hunter_cycle(monkeypatch, {"TEST": {}})
    monkeypatch.setattr(main, "_hunter_last_progress_at", None)
    monkeypatch.setattr(main, "_hunter_last_completed_at", None)

    async def successful_evaluation(symbol: str, data: dict) -> None:
        del symbol, data

    monkeypatch.setattr(main, "evaluate_candidate", successful_evaluation)

    asyncio.run(main.hunter_loop(interval_seconds=0))

    assert main._hunter_last_progress_at is not None


def test_successful_empty_hunter_cycle_marks_progress(
    monkeypatch,
) -> None:
    _stub_hunter_cycle(monkeypatch, {})
    monkeypatch.setattr(main, "_hunter_last_progress_at", None)
    monkeypatch.setattr(main, "_hunter_last_completed_at", None)

    asyncio.run(main.hunter_loop(interval_seconds=0))

    assert main._hunter_last_progress_at is not None
