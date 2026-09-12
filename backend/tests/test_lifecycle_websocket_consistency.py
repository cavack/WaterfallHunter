from __future__ import annotations

import asyncio

from waterfallhunter import main
from waterfallhunter.core.lifecycle_v2_shadow import LifecycleV2State


def _prepare_invalid_evaluation(monkeypatch, *, symbol: str, result: dict, persist_state: bool):
    monkeypatch.setattr(main.scanner, "active_candidates", {symbol: {}})
    monkeypatch.setattr(
        main.scanner,
        "get_live_reference",
        lambda requested_symbol: (0.01, 1_700_000_000.0),
    )
    monkeypatch.setattr(
        main.execution_decision_logger,
        "observe_evaluation",
        lambda *args, **kwargs: None,
    )

    async def cross_check_symbol(*args, **kwargs):
        return result

    monkeypatch.setattr(main.validator, "cross_check_symbol", cross_check_symbol)
    monkeypatch.setattr(
        main.production_evidence_recorder,
        "record",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(main, "_store_live_metrics", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        main.lifecycle_v2_shadow_store,
        "latest_state",
        lambda **kwargs: LifecycleV2State.WATCH,
    )
    monkeypatch.setattr(
        main.lifecycle_v2_shadow_store,
        "append_comparison",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        main.db,
        "update_candidate_state",
        lambda *args, **kwargs: persist_state,
    )
    # Synthetic symbols in this unit test do not exist in lbank_catalog.
    # Bypass the independent canonical lifecycle CAS so this fixture continues
    # to isolate websocket unsubscribe behavior.
    monkeypatch.setattr(
        main.entry_decision_store,
        "latest_for_symbol",
        lambda _symbol: None,
    )
    monkeypatch.setattr(
        main.entry_decision_store,
        "append_if_changed",
        lambda *_args, **_kwargs: None,
    )

    unsubscribed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main.validator.ws_manager,
        "unsubscribe",
        lambda exchange, mapped_symbol: unsubscribed.append((exchange, mapped_symbol)),
    )
    return unsubscribed


def test_failed_observation_downgrade_keeps_armed_websocket_subscription(monkeypatch) -> None:
    symbol = "DRIFTFAIL/USDT:USDT"
    mapped_symbol = "DRIFTFAIL/USDT:USDT"
    result = {
        "is_valid": False,
        "score": None,
        "suggested_status": "REJECTED",
        "observation_status": "WATCH",
        "observation_score": 35.0,
        "metrics": {
            "exchange": "binance",
            "mapped_symbol": mapped_symbol,
            "analysis_reason": "observational downgrade",
        },
    }
    unsubscribed = _prepare_invalid_evaluation(
        monkeypatch,
        symbol=symbol,
        result=result,
        persist_state=False,
    )

    asyncio.run(
        main.evaluate_candidate(
            symbol,
            {
                "status": "ARMED",
                "lifecycle_id": 1,
                "scan_eligible": True,
                "quote_volume": 3_000_000.0,
                "last_price": 0.01,
            },
        )
    )

    assert unsubscribed == []


def test_successful_unavailable_downgrade_unsubscribes_armed_websocket(monkeypatch) -> None:
    symbol = "DRIFTPASS/USDT:USDT"
    mapped_symbol = "DRIFTPASS/USDT:USDT"
    result = {
        "is_valid": False,
        "score": None,
        "suggested_status": "REJECTED",
        "metrics": {
            "exchange": "binance",
            "mapped_symbol": mapped_symbol,
            "error": "live validation unavailable",
        },
    }
    unsubscribed = _prepare_invalid_evaluation(
        monkeypatch,
        symbol=symbol,
        result=result,
        persist_state=True,
    )

    asyncio.run(
        main.evaluate_candidate(
            symbol,
            {
                "status": "ARMED",
                "lifecycle_id": 1,
                "scan_eligible": True,
                "quote_volume": 3_000_000.0,
                "last_price": 0.01,
            },
        )
    )

    assert unsubscribed == [("binance", mapped_symbol)]


def test_armed_source_failover_retires_previous_websocket_subscription(monkeypatch) -> None:
    symbol = "FAILOVER/USDT:USDT"
    old_symbol = "OLD/USDT:USDT"
    new_symbol = "NEW/USDT:USDT"
    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {symbol: {"metrics": {"exchange": "binance", "mapped_symbol": old_symbol}}},
    )
    monkeypatch.setattr(
        main.scanner,
        "get_live_reference",
        lambda requested_symbol: (0.01, __import__("time").time()),
    )
    monkeypatch.setattr(main.execution_decision_logger, "observe_evaluation", lambda *args, **kwargs: None)

    async def cross_check_symbol(*args, **kwargs):
        return {
            "is_valid": True,
            "score": 80.0,
            "suggested_status": "ARMED",
            "metrics": {"exchange": "bybit", "mapped_symbol": new_symbol},
        }

    monkeypatch.setattr(main.validator, "cross_check_symbol", cross_check_symbol)
    monkeypatch.setattr(main, "_apply_deterministic_entry_gate", lambda _s, state, _m: (state, False))
    leverage_decisions: list[str | None] = []

    def leverage_advisory(metrics, execution_suitability=None, **kwargs):
        leverage_decisions.append(kwargs.get("decision_status"))
        return {
            "policy_version": "adaptive_signal_leverage_v1",
            "minimum": 4, "maximum": 18, "symbol_agnostic": True,
            "signal_only": True, "advisory_only": True,
            "status": "UNAVAILABLE", "leverage": None, "reason": "test unavailable",
        }

    monkeypatch.setattr(main, "build_signal_leverage_advisory", leverage_advisory)
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _symbol: None)
    monkeypatch.setattr(main.entry_decision_store, "append_if_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.production_evidence_recorder, "record", lambda *args, **kwargs: True)
    monkeypatch.setattr(main.lifecycle_v2_shadow_store, "latest_state", lambda **kwargs: LifecycleV2State.WATCH)
    monkeypatch.setattr(main.lifecycle_v2_shadow_store, "append_comparison", lambda **kwargs: True)

    subscribed: list[tuple[str, str]] = []
    unsubscribed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main.validator.ws_manager,
        "subscribe",
        lambda exchange, mapped_symbol: subscribed.append((exchange, mapped_symbol)),
    )
    monkeypatch.setattr(
        main.validator.ws_manager,
        "unsubscribe",
        lambda exchange, mapped_symbol: unsubscribed.append((exchange, mapped_symbol)),
    )

    asyncio.run(
        main.evaluate_candidate(
            symbol,
            {
                "status": "ARMED",
                "lifecycle_id": 1,
                "scan_eligible": True,
                "quote_volume": 3_000_000.0,
                "last_price": 0.01,
            },
        )
    )

    assert unsubscribed == [("binance", old_symbol)]
    assert subscribed == [("bybit", new_symbol)]
    assert leverage_decisions == ["NO_TRADE"]


def test_pretrigger_observation_starts_websocket_evidence_subscription(monkeypatch) -> None:
    symbol = "PREWS/USDT:USDT"
    mapped_symbol = "PREWS/USDT:USDT"
    result = {
        "is_valid": False,
        "score": None,
        "suggested_status": "REJECTED",
        "observation_status": "PRE-TRIGGER",
        "observation_score": 58.0,
        "metrics": {
            "exchange": "binance",
            "mapped_symbol": mapped_symbol,
            "analysis_reason": "strict gates incomplete",
        },
    }
    unsubscribed = _prepare_invalid_evaluation(
        monkeypatch, symbol=symbol, result=result, persist_state=True
    )
    full_subscribed: list[tuple[str, str]] = []
    liquidation_only: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main.validator.ws_manager,
        "subscribe",
        lambda exchange, mapped: full_subscribed.append((exchange, mapped)),
    )
    monkeypatch.setattr(
        main.validator.ws_manager,
        "retain_liquidations_only",
        lambda exchange, mapped: liquidation_only.append((exchange, mapped)),
        raising=False,
    )

    asyncio.run(
        main.evaluate_candidate(
            symbol,
            {
                "status": "WATCH", "lifecycle_id": 1, "scan_eligible": True,
                "quote_volume": 3_000_000.0, "last_price": 0.01,
            },
        )
    )

    assert full_subscribed == [("binance", mapped_symbol)]
    assert liquidation_only == []
    assert unsubscribed == []


def test_pretrigger_source_failover_retires_previous_websocket_subscription(monkeypatch) -> None:
    symbol = "PREFLOW/USDT:USDT"
    old_symbol = "OLDPREFLOW/USDT:USDT"
    new_symbol = "NEWPREFLOW/USDT:USDT"
    result = {
        "is_valid": False,
        "score": None,
        "suggested_status": "REJECTED",
        "observation_status": "PRE-TRIGGER",
        "observation_score": 59.0,
        "metrics": {
            "exchange": "bybit",
            "mapped_symbol": new_symbol,
            "analysis_reason": "strict gates incomplete",
        },
    }
    unsubscribed = _prepare_invalid_evaluation(
        monkeypatch, symbol=symbol, result=result, persist_state=True
    )
    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {symbol: {"metrics": {"exchange": "binance", "mapped_symbol": old_symbol}}},
    )
    full_subscribed: list[tuple[str, str]] = []
    liquidation_only: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main.validator.ws_manager,
        "subscribe",
        lambda exchange, mapped: full_subscribed.append((exchange, mapped)),
    )
    monkeypatch.setattr(
        main.validator.ws_manager,
        "retain_liquidations_only",
        lambda exchange, mapped: liquidation_only.append((exchange, mapped)),
    )

    asyncio.run(
        main.evaluate_candidate(
            symbol,
            {
                "status": "PRE-TRIGGER", "lifecycle_id": 1, "scan_eligible": True,
                "quote_volume": 3_000_000.0, "last_price": 0.01,
            },
        )
    )

    assert unsubscribed == [("binance", old_symbol)]
    assert full_subscribed == [("bybit", new_symbol)]
    assert liquidation_only == []


def test_pretrigger_downgrade_to_watch_retires_websocket_subscription(monkeypatch) -> None:
    symbol = "PREWATCH/USDT:USDT"
    mapped_symbol = "PREWATCH/USDT:USDT"
    result = {
        "is_valid": False,
        "score": None,
        "suggested_status": "REJECTED",
        "observation_status": "WATCH",
        "observation_score": 35.0,
        "metrics": {
            "exchange": "binance",
            "mapped_symbol": mapped_symbol,
            "analysis_reason": "observational downgrade",
        },
    }
    unsubscribed = _prepare_invalid_evaluation(
        monkeypatch, symbol=symbol, result=result, persist_state=True
    )
    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {symbol: {"metrics": {"exchange": "binance", "mapped_symbol": mapped_symbol}}},
    )
    subscribed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main.validator.ws_manager,
        "subscribe",
        lambda exchange, mapped: subscribed.append((exchange, mapped)),
    )

    asyncio.run(
        main.evaluate_candidate(
            symbol,
            {
                "status": "PRE-TRIGGER", "lifecycle_id": 1, "scan_eligible": True,
                "quote_volume": 3_000_000.0, "last_price": 0.01,
            },
        )
    )

    assert subscribed == []
    assert unsubscribed == [("binance", mapped_symbol)]


def test_reference_failure_retires_previous_pretrigger_subscription(monkeypatch) -> None:
    symbol = "REFLOSS/USDT:USDT"
    mapped_symbol = "REFLOSS/USDT:USDT"
    monkeypatch.setattr(main.scanner, "active_candidates", {symbol: {"metrics": {"exchange": "binance", "mapped_symbol": mapped_symbol}}})
    monkeypatch.setattr(main.scanner, "get_live_reference", lambda _symbol: (None, None))
    monkeypatch.setattr(main.execution_decision_logger, "observe_evaluation", lambda *a, **k: None)

    async def no_reference(_symbol):
        return None

    monkeypatch.setattr(main.validator, "resolve_live_reference", no_reference)
    monkeypatch.setattr(main.production_evidence_recorder, "record", lambda *a, **k: True)
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _symbol: None)
    monkeypatch.setattr(main, "_store_live_metrics", lambda *a, **k: None)
    monkeypatch.setattr(main.db, "update_candidate_state", lambda *a, **k: True)
    unsubscribed = []
    monkeypatch.setattr(main.validator.ws_manager, "unsubscribe", lambda exchange, mapped: unsubscribed.append((exchange, mapped)))

    asyncio.run(main.evaluate_candidate(symbol, {"status": "PRE-TRIGGER", "lifecycle_id": 1, "scan_eligible": True, "quote_volume": 3_000_000.0, "last_price": 0.01}))
    assert unsubscribed == [("binance", mapped_symbol)]


def test_source_less_validation_failure_retires_previous_pretrigger_subscription(monkeypatch) -> None:
    symbol = "SOURCELESS/USDT:USDT"
    mapped_symbol = "SOURCELESS/USDT:USDT"
    result = {"is_valid": False, "score": None, "suggested_status": "REJECTED", "metrics": {"error": "no exchange source selected"}}
    unsubscribed = _prepare_invalid_evaluation(monkeypatch, symbol=symbol, result=result, persist_state=True)
    monkeypatch.setattr(main.scanner, "active_candidates", {symbol: {"metrics": {"exchange": "binance", "mapped_symbol": mapped_symbol}}})

    asyncio.run(main.evaluate_candidate(symbol, {"status": "PRE-TRIGGER", "lifecycle_id": 1, "scan_eligible": True, "quote_volume": 3_000_000.0, "last_price": 0.01}))
    assert unsubscribed == [("binance", mapped_symbol)]


def test_removed_candidate_cleanup_retires_its_previous_websocket_source(monkeypatch) -> None:
    symbol = "REMOVED/USDT:USDT"
    mapped_symbol = "REMOVED/USDT:USDT"
    unsubscribed = []
    monkeypatch.setattr(main.validator.ws_manager, "unsubscribe", lambda exchange, mapped: unsubscribed.append((exchange, mapped)))
    main._retire_removed_candidate_websocket_sources({symbol: {"metrics": {"exchange": "binance", "mapped_symbol": mapped_symbol}}})
    assert unsubscribed == [("binance", mapped_symbol)]
    assert main.scanner.on_candidates_removed is main._retire_removed_candidate_websocket_sources


def test_catalog_removal_fences_inflight_reference_failure_from_resurrecting_candidate(monkeypatch) -> None:
    symbol = "CATRACE/USDT:USDT"
    mapped_symbol = symbol
    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {symbol: {"metrics": {"exchange": "binance", "mapped_symbol": mapped_symbol}}},
    )
    monkeypatch.setattr(main.scanner, "get_live_reference", lambda _symbol: (None, None))
    monkeypatch.setattr(main.execution_decision_logger, "observe_evaluation", lambda *a, **k: None)
    monkeypatch.setattr(main.production_evidence_recorder, "record", lambda *a, **k: True)
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _symbol: None)
    monkeypatch.setattr(main.db, "update_candidate_state", lambda *a, **k: True)
    unsubscribed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main.validator.ws_manager,
        "unsubscribe",
        lambda exchange, mapped: unsubscribed.append((exchange, mapped)),
    )

    async def scenario() -> None:
        resolver_started = asyncio.Event()
        resume = asyncio.Event()

        async def no_reference(_symbol):
            resolver_started.set()
            await resume.wait()
            return None

        monkeypatch.setattr(main.validator, "resolve_live_reference", no_reference)
        task = asyncio.create_task(
            main.evaluate_candidate(
                symbol,
                {
                    "status": "PRE-TRIGGER", "lifecycle_id": 1, "scan_eligible": True,
                    "quote_volume": 3_000_000.0, "last_price": 0.01,
                },
            )
        )
        await resolver_started.wait()
        removed = main.scanner.active_candidates.pop(symbol)
        main._retire_removed_candidate_websocket_sources({symbol: removed})
        resume.set()
        await task

    asyncio.run(scenario())
    assert symbol not in main.scanner.active_candidates
    assert unsubscribed == [("binance", mapped_symbol)]


def test_catalog_removal_during_cross_check_cannot_restart_pretrigger_stream(monkeypatch) -> None:
    symbol = "CATSTREAMRACE/USDT:USDT"
    mapped_symbol = symbol
    result = {
        "is_valid": False,
        "score": None,
        "suggested_status": "REJECTED",
        "observation_status": "PRE-TRIGGER",
        "observation_score": 59.0,
        "metrics": {
            "exchange": "binance",
            "mapped_symbol": mapped_symbol,
            "analysis_reason": "strict gates incomplete",
        },
    }
    unsubscribed = _prepare_invalid_evaluation(
        monkeypatch, symbol=symbol, result=result, persist_state=True
    )
    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {symbol: {"metrics": {"exchange": "binance", "mapped_symbol": mapped_symbol}}},
    )
    liquidation_only: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main.validator.ws_manager,
        "retain_liquidations_only",
        lambda exchange, mapped: liquidation_only.append((exchange, mapped)),
    )

    async def scenario() -> None:
        cross_check_started = asyncio.Event()
        resume = asyncio.Event()

        async def gated_cross_check(*args, **kwargs):
            cross_check_started.set()
            await resume.wait()
            return result

        monkeypatch.setattr(main.validator, "cross_check_symbol", gated_cross_check)
        task = asyncio.create_task(
            main.evaluate_candidate(
                symbol,
                {
                    "status": "PRE-TRIGGER", "lifecycle_id": 1, "scan_eligible": True,
                    "quote_volume": 3_000_000.0, "last_price": 0.01,
                },
            )
        )
        await cross_check_started.wait()
        removed = main.scanner.active_candidates.pop(symbol)
        main._retire_removed_candidate_websocket_sources({symbol: removed})
        resume.set()
        await task

    asyncio.run(scenario())
    assert liquidation_only == []
    assert unsubscribed == [("binance", mapped_symbol)]
    assert symbol not in main.scanner.active_candidates


def test_established_catalog_rejects_queued_candidate_missing_from_active_universe(monkeypatch) -> None:
    symbol = "QUEUEDSTALE/USDT:USDT"
    monkeypatch.setattr(main.scanner, "active_candidates", {})
    monkeypatch.setattr(main.scanner, "last_successful_refresh_at", 1_700_000_000.0)

    def should_not_read_reference(_symbol):
        raise AssertionError("stale queued candidate reached live evaluation")

    monkeypatch.setattr(main.scanner, "get_live_reference", should_not_read_reference)

    asyncio.run(
        main.evaluate_candidate(
            symbol,
            {
                "status": "PRE-TRIGGER", "lifecycle_id": 1, "scan_eligible": True,
                "quote_volume": 3_000_000.0, "last_price": 0.01,
            },
        )
    )
    assert symbol not in main.scanner.active_candidates


def test_readded_candidate_rejects_queued_stale_lifecycle_before_mutation(monkeypatch) -> None:
    symbol = "REGEN/USDT:USDT"
    current = {
        "lifecycle_id": 2,
        "status": "WATCH",
        "analysis_status": "fresh_generation",
    }
    monkeypatch.setattr(main.scanner, "active_candidates", {symbol: current})
    monkeypatch.setattr(main.scanner, "last_successful_refresh_at", 1_700_000_000.0)

    def should_not_read_reference(_symbol):
        raise AssertionError("stale lifecycle reached live evaluation")

    monkeypatch.setattr(main.scanner, "get_live_reference", should_not_read_reference)

    asyncio.run(
        main.evaluate_candidate(
            symbol,
            {
                "status": "PRE-TRIGGER", "lifecycle_id": 1, "scan_eligible": True,
                "quote_volume": 3_000_000.0, "last_price": 0.01,
            },
        )
    )
    assert main.scanner.active_candidates[symbol] is current
    assert current["analysis_status"] == "fresh_generation"


def test_fuel_rich_uses_shared_market_evidence_pool_without_direct_subscription(monkeypatch) -> None:
    source = ("binance", "FUEL/USDT:USDT")
    direct_subscribed: list[tuple[str, str]] = []
    direct_unsubscribed: list[tuple[str, str]] = []
    shared_subscribed: list[tuple[str, str]] = []
    shared_unsubscribed: list[tuple[str, str]] = []

    monkeypatch.setattr(
        main.validator.ws_manager,
        "subscribe",
        lambda exchange, mapped: direct_subscribed.append((exchange, mapped)),
    )
    monkeypatch.setattr(
        main.validator.ws_manager,
        "unsubscribe",
        lambda exchange, mapped: direct_unsubscribed.append((exchange, mapped)),
    )
    monkeypatch.setattr(
        main.validator.ws_manager,
        "subscribe_shared_evidence",
        lambda exchange, mapped: shared_subscribed.append((exchange, mapped)),
        raising=False,
    )
    monkeypatch.setattr(
        main.validator.ws_manager,
        "unsubscribe_shared_evidence",
        lambda exchange, mapped: shared_unsubscribed.append((exchange, mapped)),
        raising=False,
    )
    monkeypatch.setattr(
        main.validator.ws_manager,
        "has_direct_evidence_subscription",
        lambda exchange, mapped: False,
        raising=False,
    )

    main._sync_websocket_evidence_subscription(source, source, state="FUEL-RICH")

    assert direct_subscribed == []
    assert direct_unsubscribed == []
    assert shared_subscribed == [source]
    assert shared_unsubscribed == []


def test_shared_direct_shared_lifecycle_handoff_never_has_dual_logical_ownership(monkeypatch) -> None:
    source = ("binance", "HANDOFF/USDT:USDT")
    direct: set[tuple[str, str]] = set()
    shared: set[tuple[str, str]] = set()
    snapshots: list[tuple[str, frozenset[tuple[str, str]], frozenset[tuple[str, str]]]] = []

    def subscribe(exchange: str, mapped: str) -> None:
        direct.add((exchange, mapped))
        snapshots.append(("direct-subscribe", frozenset(direct), frozenset(shared)))

    def unsubscribe(exchange: str, mapped: str) -> None:
        direct.discard((exchange, mapped))
        snapshots.append(("direct-unsubscribe", frozenset(direct), frozenset(shared)))

    def subscribe_shared(exchange: str, mapped: str) -> bool:
        shared.add((exchange, mapped))
        snapshots.append(("shared-subscribe", frozenset(direct), frozenset(shared)))
        return True

    def unsubscribe_shared(exchange: str, mapped: str) -> None:
        shared.discard((exchange, mapped))
        snapshots.append(("shared-unsubscribe", frozenset(direct), frozenset(shared)))

    manager = main.validator.ws_manager
    monkeypatch.setattr(manager, "subscribe", subscribe)
    monkeypatch.setattr(manager, "unsubscribe", unsubscribe)
    monkeypatch.setattr(manager, "subscribe_shared_evidence", subscribe_shared, raising=False)
    monkeypatch.setattr(manager, "unsubscribe_shared_evidence", unsubscribe_shared, raising=False)
    monkeypatch.setattr(
        manager,
        "has_direct_evidence_subscription",
        lambda exchange, mapped: (exchange, mapped) in direct,
        raising=False,
    )

    main._sync_websocket_evidence_subscription(source, source, state="FUEL-RICH")
    assert direct == set()
    assert shared == {source}

    main._sync_websocket_evidence_subscription(source, source, state="PRE-TRIGGER")
    assert direct == {source}
    assert shared == set()

    main._sync_websocket_evidence_subscription(source, source, state="FUEL-RICH")
    assert direct == set()
    assert shared == {source}

    assert all(not (direct_state and shared_state) for _, direct_state, shared_state in snapshots)
    assert [event for event, _, _ in snapshots] == [
        "shared-subscribe",
        "shared-unsubscribe",
        "direct-subscribe",
        "direct-unsubscribe",
        "shared-subscribe",
    ]


def test_fuel_rich_direct_fallback_is_not_restarted_on_resync(monkeypatch) -> None:
    source = ("bingx", "FALLBACK/USDT:USDT")
    direct_unsubscribed: list[tuple[str, str]] = []
    shared_subscribed: list[tuple[str, str]] = []
    manager = main.validator.ws_manager

    monkeypatch.setattr(
        manager, "has_direct_evidence_subscription", lambda *args: True, raising=False
    )
    monkeypatch.setattr(
        manager, "has_shared_evidence_direct_fallback", lambda *args: True, raising=False
    )
    monkeypatch.setattr(
        manager, "unsubscribe", lambda exchange, mapped: direct_unsubscribed.append((exchange, mapped))
    )
    monkeypatch.setattr(
        manager,
        "subscribe_shared_evidence",
        lambda exchange, mapped: shared_subscribed.append((exchange, mapped)) or True,
        raising=False,
    )

    main._sync_websocket_evidence_subscription(source, source, state="FUEL-RICH")

    assert direct_unsubscribed == []
    assert shared_subscribed == [source]
