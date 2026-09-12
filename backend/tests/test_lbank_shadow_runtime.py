import asyncio
import time


def _complete_short_outcome_candles(triggered_at: int) -> list[list[float]]:
    start_ms = triggered_at * 1000
    candles = []
    for index in range(1_440):
        low = 94.0 if index == 1 else 99.0
        candles.append([start_ms + index * 60_000, 100.0, 101.0, low, 99.5, 1.0])
    return candles


def test_entry_outcome_provenance_keeps_cost_components_distinct(monkeypatch):
    import waterfallhunter.main as main

    monkeypatch.setattr(main.settings, "source_revision", "a" * 40)
    provenance = main._entry_outcome_research_provenance(
        {
            "microstructure": {
                "entry_slippage_pct": 0.05,
                "exit_slippage_pct": 0.08,
            },
        },
        decision_contract_hash="b" * 64,
        decision_at=100,
        reference_source="lbank",
        reference_mapped_symbol="SXT/USDT:USDT",
    )

    assert provenance["source_revision"] == "a" * 40
    assert provenance["contract"]["market_type"] == "linear_usdt_perpetual"
    assert provenance["costs"]["entry_slippage"]["value"] == 0.05
    assert provenance["costs"]["exit_slippage"]["value"] == 0.08
    assert provenance["costs"]["fees"]["classification"] == "UNAVAILABLE"
    assert provenance["costs"]["funding"]["value"] is None


def test_entry_outcome_provenance_binds_primary_lbank_reference_not_cross_check(
    monkeypatch,
):
    import waterfallhunter.main as main

    monkeypatch.setattr(main.settings, "source_revision", "a" * 40)
    provenance = main._entry_outcome_research_provenance(
        {
            "exchange": "bybit",
            "mapped_symbol": "SXT/USDT:USDT",
            "microstructure": {},
        },
        decision_contract_hash="b" * 64,
        decision_at=100,
        reference_source="lbank",
        reference_mapped_symbol="SXT/USDT:USDT",
    )

    assert provenance["contract"] == {
        "available": True,
        "exchange": "lbank",
        "mapped_symbol": "SXT/USDT:USDT",
        "market_type": "linear_usdt_perpetual",
        "settlement_asset": "USDT",
        "spot_substitution_allowed": False,
        "cross_venue_substitution_allowed": False,
    }


def test_entry_outcome_provenance_marks_fallback_reference_unavailable(monkeypatch):
    import waterfallhunter.main as main

    monkeypatch.setattr(main.settings, "source_revision", "a" * 40)
    provenance = main._entry_outcome_research_provenance(
        {"exchange": "bybit", "mapped_symbol": "SXT/USDT:USDT"},
        decision_contract_hash="b" * 64,
        decision_at=100,
        reference_source="binance",
        reference_mapped_symbol="SXT/USDT:USDT",
    )

    assert provenance["contract"]["available"] is False
    assert provenance["contract"]["exchange"] == "binance"
    assert provenance["contract"]["mapped_symbol"] == "SXT/USDT:USDT"


def test_entry_outcome_worker_builder_uses_bounded_two_lane_cadence():
    import waterfallhunter.main as main

    worker = main._build_entry_outcome_resolution_worker()

    assert worker.batch_size == 36
    assert worker.local_batch_size == 300
    assert worker.interval_seconds == 900.0


def test_entry_outcome_resolver_uses_exact_complete_lbank_window(monkeypatch):
    import waterfallhunter.main as main

    triggered_at = 60
    observed_fetch = {}

    async def fetch(signal, start_ms, end_ms):
        observed_fetch.update(signal=signal, start_ms=start_ms, end_ms=end_ms)
        return _complete_short_outcome_candles(triggered_at)

    monkeypatch.setattr(main, "_fetch_signal_outcome_candles", fetch)
    capture = {
        "decision_event_id": 7,
        "decision_event_at": triggered_at,
        "decision_packet_sha256": "c" * 64,
        "decision_contract_sha256": "b" * 64,
        "source_revision": "a" * 40,
        "symbol": "SXT/USDT:USDT",
        "contract": {
            "available": True,
            "exchange": "lbank",
            "mapped_symbol": "SXT/USDT:USDT",
            "market_type": "linear_usdt_perpetual",
        },
        "trade_plan": {
            "entry_price": 100.0,
            "stop_loss": 105.0,
            "take_profit_1": 95.0,
            "take_profit_2": 90.0,
        },
        "outcome_contract": {
            "horizon_seconds": 86_400,
            "closed_candles_only": True,
            "complete_window_required": True,
        },
        "costs": {
            name: {"available": False, "classification": "UNAVAILABLE", "value": None}
            for name in ("fees", "entry_slippage", "exit_slippage", "funding")
        },
    }

    result = asyncio.run(main._resolve_entry_outcome(capture))

    assert observed_fetch["signal"]["trigger_metrics_json"] == (
        '{"exchange": "lbank", "mapped_symbol": "SXT/USDT:USDT"}'
    )
    assert result is not None
    assert result["outcome_status"] == "OBSERVED"
    assert result["classification"] == "WIN"
    assert result["raw_outcome_status"] == "TP1_ONLY_24H"
    assert result["gross_r"] == 1.0
    assert result["net_r"] is None
    assert result["scientific_tier"] == "UNAVAILABLE"


def test_tp1_then_stop_gross_r_is_unavailable_without_exit_allocation_policy():
    import waterfallhunter.main as main

    assert main._decision_outcome_gross_r(
        "WIN",
        "TP1_THEN_STOP",
        {
            "entry_price": 100.0,
            "stop_loss": 105.0,
            "take_profit_1": 95.0,
            "take_profit_2": 90.0,
        },
        [],
    ) is None


def test_entry_outcome_resolver_retries_incomplete_window(monkeypatch):
    import waterfallhunter.main as main

    async def fetch(*_args):
        return [[60_000, 100.0, 101.0, 99.0, 100.0, 1.0]]

    monkeypatch.setattr(main, "_fetch_signal_outcome_candles", fetch)
    capture = {
        "decision_event_id": 7,
        "decision_event_at": 60,
        "symbol": "SXT/USDT:USDT",
        "contract": {
            "available": True,
            "exchange": "lbank",
            "mapped_symbol": "SXT/USDT:USDT",
            "market_type": "linear_usdt_perpetual",
        },
        "trade_plan": {
            "entry_price": 100.0,
            "stop_loss": 105.0,
            "take_profit_1": 95.0,
            "take_profit_2": 90.0,
        },
        "outcome_contract": {
            "horizon_seconds": 86_400,
            "closed_candles_only": True,
            "complete_window_required": True,
        },
    }
    assert asyncio.run(main._resolve_entry_outcome(capture)) is None


class _FakeSettlementWorker:
    def run_forever(
        self,
        *,
        interval_seconds,
    ):
        return (
            "settlement",
            interval_seconds,
        )


def test_shadow_builder_does_nothing_when_feature_is_disabled(
    monkeypatch,
):
    import waterfallhunter.main as main

    monkeypatch.setattr(
        main.settings,
        "lbank_execution_shadow_enabled",
        False,
    )

    class ForbiddenStore:
        def __init__(
            self,
            *args,
            **kwargs,
        ):
            raise AssertionError(
                "Store must not be constructed "
                "when shadow is disabled"
            )

    monkeypatch.setattr(
        main,
        "LBankExecutionStore",
        ForbiddenStore,
    )

    worker = (
        main
        ._build_lbank_execution_shadow_worker()
    )

    assert worker is None


def test_shadow_builder_uses_configured_bounded_runtime_values(
    monkeypatch,
):
    import waterfallhunter.main as main

    captured = {}

    class FakeStore:
        def __init__(
            self,
            db_path,
        ):
            captured[
                "db_path"
            ] = db_path

    class FakeWorker:
        def __init__(
            self,
            store,
            *,
            batch_size,
            success_recheck_seconds,
            failure_recheck_seconds,
        ):
            captured[
                "store"
            ] = store

            captured[
                "batch_size"
            ] = batch_size

            captured[
                "success_recheck_seconds"
            ] = (
                success_recheck_seconds
            )

            captured[
                "failure_recheck_seconds"
            ] = (
                failure_recheck_seconds
            )

    monkeypatch.setattr(
        main.settings,
        "lbank_execution_shadow_enabled",
        True,
    )

    monkeypatch.setattr(
        main.settings,
        "lbank_execution_shadow_batch_size",
        4,
    )

    monkeypatch.setattr(
        main.settings,
        (
            "lbank_execution_shadow_"
            "success_recheck_seconds"
        ),
        900.0,
    )

    monkeypatch.setattr(
        main.settings,
        (
            "lbank_execution_shadow_"
            "failure_recheck_seconds"
        ),
        300.0,
    )

    monkeypatch.setattr(
        main.db,
        "db_path",
        "/tmp/runtime-test.db",
        raising=False,
    )

    monkeypatch.setattr(
        main,
        "LBankExecutionStore",
        FakeStore,
    )

    monkeypatch.setattr(
        main,
        "LBankExecutionShadowWorker",
        FakeWorker,
    )

    worker = (
        main
        ._build_lbank_execution_shadow_worker()
    )

    assert isinstance(
        worker,
        FakeWorker,
    )

    assert (
        captured["db_path"]
        == "/tmp/runtime-test.db"
    )

    assert (
        captured["batch_size"]
        == 4
    )

    assert (
        captured[
            "success_recheck_seconds"
        ]
        == 900.0
    )

    assert (
        captured[
            "failure_recheck_seconds"
        ]
        == 300.0
    )


def test_startup_with_shadow_disabled_schedules_no_shadow_task(
    monkeypatch,
):
    import waterfallhunter.main as main

    scheduled = []

    monkeypatch.setattr(
        main.settings,
        "live_trading_enabled",
        False,
    )

    monkeypatch.setattr(
        main.settings,
        "lbank_execution_shadow_enabled",
        False,
    )

    monkeypatch.setattr(
        main,
        "_lbank_execution_shadow_worker",
        None,
    )

    fake_settlement = _FakeSettlementWorker()
    monkeypatch.setattr(
        main,
        "_build_signal_settlement_worker",
        lambda: fake_settlement,
    )
    class FakeOutcomeWorker:
        def run_forever(self):
            return ("entry_outcome_resolution", 900.0)

        def stop(self):
            pass

    fake_outcome_worker = FakeOutcomeWorker()
    monkeypatch.setattr(
        main,
        "_build_entry_outcome_resolution_worker",
        lambda: fake_outcome_worker,
    )
    monkeypatch.setattr(
        main.feature_replay_worker,
        "run_forever",
        lambda interval_seconds: (
            "feature_replay",
            interval_seconds,
        ),
    )

    monkeypatch.setattr(
        main.scanner,
        "start_background_scanner",
        lambda interval: (
            "catalog",
            interval,
        ),
    )

    monkeypatch.setattr(
        main,
        "live_reference_loop",
        lambda: "live_reference",
    )

    monkeypatch.setattr(
        main,
        "hunter_loop",
        lambda interval_seconds=60: (
            "hunter",
            interval_seconds,
        ),
    )

    monkeypatch.setattr(
        main,
        "sse_broadcaster",
        lambda: "sse",
    )

    monkeypatch.setattr(
        main.notifier,
        "start_interactive_bot",
        lambda: "telegram_bot",
    )

    monkeypatch.setattr(
        main,
        "_start_background_task",
        lambda task: (
            scheduled.append(task)
            or task
        ),
    )

    asyncio.run(
        main.startup_event()
    )

    assert (
        main
        ._lbank_execution_shadow_worker
        is None
    )

    assert len(scheduled) == 8
    assert main._entry_outcome_resolution_worker is fake_outcome_worker

    assert (
        "feature_replay",
        60.0,
    ) in scheduled

    assert (
        "settlement",
        900.0,
    ) in scheduled
    assert ("entry_outcome_resolution", 900.0) in scheduled

    assert not any(
        (
            isinstance(
                item,
                tuple,
            )
            and item
            and item[0]
            == "shadow"
        )
        for item in scheduled
    )


def test_startup_with_shadow_enabled_schedules_shadow_worker(
    monkeypatch,
):
    import waterfallhunter.main as main

    scheduled = []

    class FakeShadowWorker:
        batch_size = 3

        def run_forever(
            self,
            *,
            interval_seconds,
        ):
            return (
                "shadow",
                interval_seconds,
            )

    fake_worker = (
        FakeShadowWorker()
    )

    monkeypatch.setattr(
        main.settings,
        "live_trading_enabled",
        False,
    )

    monkeypatch.setattr(
        main.settings,
        "lbank_execution_shadow_enabled",
        True,
    )

    monkeypatch.setattr(
        main.settings,
        (
            "lbank_execution_shadow_"
            "interval_seconds"
        ),
        120.0,
    )

    monkeypatch.setattr(
        main,
        "_lbank_execution_shadow_worker",
        None,
    )

    fake_settlement = _FakeSettlementWorker()
    monkeypatch.setattr(
        main,
        "_build_signal_settlement_worker",
        lambda: fake_settlement,
    )
    class FakeOutcomeWorker:
        def run_forever(self):
            return ("entry_outcome_resolution", 900.0)

        def stop(self):
            pass

    fake_outcome_worker = FakeOutcomeWorker()
    monkeypatch.setattr(
        main,
        "_build_entry_outcome_resolution_worker",
        lambda: fake_outcome_worker,
    )
    monkeypatch.setattr(
        main.feature_replay_worker,
        "run_forever",
        lambda interval_seconds: (
            "feature_replay",
            interval_seconds,
        ),
    )

    monkeypatch.setattr(
        main.scanner,
        "start_background_scanner",
        lambda interval: (
            "catalog",
            interval,
        ),
    )

    monkeypatch.setattr(
        main,
        "live_reference_loop",
        lambda: "live_reference",
    )

    monkeypatch.setattr(
        main,
        "hunter_loop",
        lambda interval_seconds=60: (
            "hunter",
            interval_seconds,
        ),
    )

    monkeypatch.setattr(
        main,
        "sse_broadcaster",
        lambda: "sse",
    )

    monkeypatch.setattr(
        main.notifier,
        "start_interactive_bot",
        lambda: "telegram_bot",
    )

    monkeypatch.setattr(
        main,
        (
            "_build_lbank_execution_"
            "shadow_worker"
        ),
        lambda: fake_worker,
    )

    monkeypatch.setattr(
        main,
        "_start_background_task",
        lambda task: (
            scheduled.append(task)
            or task
        ),
    )

    asyncio.run(
        main.startup_event()
    )

    assert (
        main
        ._lbank_execution_shadow_worker
        is fake_worker
    )

    assert (
        (
            "shadow",
            120.0,
        )
        in scheduled
    )

    assert len(scheduled) == 9
    assert main._entry_outcome_resolution_worker is fake_outcome_worker

    assert (
        "feature_replay",
        60.0,
    ) in scheduled

    assert (
        "settlement",
        900.0,
    ) in scheduled
    assert ("entry_outcome_resolution", 900.0) in scheduled


def test_shadow_health_is_observational_and_does_not_gate_main_health(
    monkeypatch,
):
    import waterfallhunter.main as main

    now = time.time()

    class FakeShadowWorker:
        def health_snapshot(
            self,
        ):
            return {
                "running": False,
                "batch_size": 8,
                "last_started_at": (
                    now - 100
                ),
                "last_progress_at": (
                    now - 100
                ),
                "last_completed_at": (
                    now - 100
                ),
                "total_attempted": 8,
                "total_observed": 0,
                "total_unavailable": 8,
            }

    monkeypatch.setattr(
        main.scanner,
        "last_successful_refresh_at",
        now,
    )

    monkeypatch.setattr(
        main,
        "_hunter_last_progress_at",
        now,
    )

    monkeypatch.setattr(
        main,
        "_lbank_execution_shadow_worker",
        FakeShadowWorker(),
    )

    result = asyncio.run(
        main.health_check()
    )

    assert (
        result["status"]
        == "healthy"
    )

    shadow = result[
        "lbank_execution_shadow"
    ]

    assert (
        shadow["enabled"]
        is True
    )

    assert (
        shadow["running"]
        is False
    )

    assert (
        shadow[
            "total_unavailable"
        ]
        == 8
    )


def test_live_reference_loop_propagates_task_cancellation(monkeypatch):
    import waterfallhunter.main as main

    async def cancelled_refresh():
        raise asyncio.CancelledError

    monkeypatch.setattr(
        main.scanner,
        "refresh_live_references",
        cancelled_refresh,
    )

    async def scenario():
        task = asyncio.create_task(main.live_reference_loop(interval_seconds=0))
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(scenario()) is True


def test_entry_outcome_resolver_terminalizes_no_candles_after_bounded_retry_age(monkeypatch):
    import waterfallhunter.main as main

    async def fetch(*_args):
        return []

    monkeypatch.setattr(main, "_fetch_signal_outcome_candles", fetch)
    captured_at = 1_000
    monkeypatch.setattr(main.time, "time", lambda: captured_at + 72 * 3600 + 1)
    capture = {
        "decision_event_id": 7,
        "decision_event_at": 900,
        "captured_at": captured_at,
        "symbol": "SXT/USDT:USDT",
        "contract": {
            "available": True,
            "exchange": "lbank",
            "mapped_symbol": "SXT/USDT:USDT",
            "market_type": "linear_usdt_perpetual",
        },
        "trade_plan": {
            "entry_price": 100.0,
            "stop_loss": 105.0,
            "take_profit_1": 95.0,
            "take_profit_2": 90.0,
        },
        "outcome_contract": {
            "horizon_seconds": 86_400,
            "closed_candles_only": True,
            "complete_window_required": True,
        },
    }

    result = asyncio.run(main._resolve_entry_outcome(capture))
    assert result is not None
    assert result["outcome_status"] == "UNAVAILABLE"
    assert result["classification"] == "UNAVAILABLE"
    assert "retry window exhausted" in result["reason"]
