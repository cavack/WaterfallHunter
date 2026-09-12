from __future__ import annotations

import asyncio
import threading

import waterfallhunter.main as main


def _stub_metrics_dependencies(monkeypatch) -> None:
    monkeypatch.setattr(main.db, "get_all_active_candidates", lambda: {})
    monkeypatch.setattr(main, "_update_candidate_state_metrics", lambda _: None)
    monkeypatch.setattr(main, "_update_lbank_shadow_metrics", lambda: None)
    monkeypatch.setattr(main, "_update_signal_settlement_worker_metrics", lambda: None)

    async def notification_noop(*args, **kwargs) -> None:
        del args, kwargs

    monkeypatch.setattr(
        main,
        "_notification_delivery_health_snapshot",
        notification_noop,
    )
    monkeypatch.setattr(main, "generate_latest", lambda: b"metrics-ok")


def test_metrics_reads_active_candidates_off_event_loop(monkeypatch) -> None:
    caller_thread = threading.get_ident()
    observed_thread: int | None = None

    def get_all_active_candidates() -> dict:
        nonlocal observed_thread
        observed_thread = threading.get_ident()
        return {}

    async def noop_async(*args, **kwargs) -> None:
        del args, kwargs

    monkeypatch.setattr(main.db, "get_all_active_candidates", get_all_active_candidates)
    monkeypatch.setattr(main, "_update_candidate_state_metrics", lambda _: None)
    monkeypatch.setattr(main, "_update_lbank_shadow_metrics", lambda: None)
    monkeypatch.setattr(main, "_update_signal_settlement_worker_metrics", lambda: None)
    monkeypatch.setattr(main, "_update_signal_evidence_metrics", noop_async)
    monkeypatch.setattr(main, "_notification_delivery_health_snapshot", noop_async)
    monkeypatch.setattr(main, "generate_latest", lambda: b"")

    asyncio.run(main.metrics())

    assert observed_thread is not None
    assert observed_thread != caller_thread


def test_metrics_survives_signal_evidence_refresh_failure(monkeypatch) -> None:
    _stub_metrics_dependencies(monkeypatch)

    async def evidence_failure(*args, **kwargs) -> None:
        del args, kwargs
        raise RuntimeError("evidence report unavailable")

    monkeypatch.setattr(main, "_update_signal_evidence_metrics", evidence_failure)

    response = asyncio.run(main.metrics())

    assert response.status_code == 200
    assert bytes(response.body) == b"metrics-ok"


def test_signal_evidence_refresh_failure_is_throttled(monkeypatch) -> None:
    calls = 0

    def failing_report() -> dict:
        nonlocal calls
        calls += 1
        raise RuntimeError("evidence report unavailable")

    monkeypatch.setattr(main.execution_outcome_report, "build_report", failing_report)
    monkeypatch.setattr(main, "_signal_evidence_metrics_last_refresh", 0.0)

    class FreshProcessClock:
        @staticmethod
        def monotonic() -> float:
            return 10.0

    monkeypatch.setattr(main, "time", FreshProcessClock)

    for _ in range(2):
        try:
            asyncio.run(main._update_signal_evidence_metrics())
        except RuntimeError:
            pass

    assert calls == 1


def test_adaptive_pipeline_metrics_render_without_symbol_labels(monkeypatch) -> None:
    record = getattr(main, "_record_adaptive_pipeline_observation", None)
    update = getattr(main, "_update_adaptive_pipeline_metrics", None)
    assert record is not None
    assert update is not None

    monkeypatch.setattr(
        main.validator.candle_analyzer,
        "cache_diagnostics",
        lambda: {"hits": 7, "misses": 3, "evictions": 1, "entries": 9},
        raising=False,
    )
    monkeypatch.setattr(main, "_hunter_in_flight_count", 4, raising=False)
    monkeypatch.setattr(main, "_hunter_due_backlog", 6, raising=False)
    record(
        "PRE-TRIGGER",
        1.25,
        {
            "source_attempts": 2,
            "ws_evidence_hits": 1,
            "rest_evidence_fallbacks": 1,
            "outcome": "complete",
            "stage_durations_seconds": {"microstructure": 0.2, "candles": 0.4},
        },
    )
    update()

    payload = main.generate_latest().decode("utf-8")
    names = (
        "waterfall_hunter_evaluation_duration_seconds",
        "waterfall_market_evidence_stage_duration_seconds",
        "waterfall_primary_source_attempts",
        "waterfall_hunter_in_flight_evaluations",
        "waterfall_hunter_due_backlog",
        "waterfall_market_evidence_path_total",
        "waterfall_candle_cache_events",
    )
    for name in names:
        assert name in payload

    adaptive_lines = [
        line for line in payload.splitlines()
        if any(line.startswith(name) for name in names)
    ]
    assert adaptive_lines
    assert all("symbol=" not in line for line in adaptive_lines)
