from __future__ import annotations

import asyncio

import waterfallhunter.core.ws_streamer as ws_module
from waterfallhunter.core.ws_streamer import WebSocketManager


def test_liquidation_cache_normalizes_long_short_notional_and_burst_ratio() -> None:
    manager = WebSocketManager()
    symbol = "TEST/USDT:USDT"
    manager._ingest_liquidations(
        "binance",
        symbol,
        [
            {"timestamp": 40_000, "side": "buy", "contracts": 2.0, "contractSize": 1.0, "price": 100.0},
            {"timestamp": 70_000, "side": "sell", "contracts": 3.0, "contractSize": 1.0, "price": 100.0},
            {"timestamp": 95_000, "side": "sell", "contracts": 4.0, "contractSize": 1.0, "price": 100.0},
            {"timestamp": 99_000, "side": "buy", "contracts": 1.0, "contractSize": 1.0, "price": 100.0},
        ],
        received_at=100.0,
    )

    flow = manager.get_realtime_liquidation_flow("binance", symbol, now=100.0)

    assert flow is not None
    assert flow["available"] is True
    assert flow["long_liquidation_notional_1m"] == 700.0
    assert flow["short_liquidation_notional_1m"] == 100.0
    assert flow["liquidation_velocity_usd_per_min"] == 800.0
    assert flow["burst_ratio"] == 4.0
    assert flow["observed_at"] == 99.0


def test_subscription_starts_and_unsubscribe_stops_liquidation_stream(monkeypatch) -> None:
    manager = WebSocketManager()
    symbol = "TEST/USDT:USDT"
    monkeypatch.setattr(ws_module, "ccxt_pro", object())

    async def idle(*args, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(manager, "watch_orderbook_stream", idle)
    monkeypatch.setattr(manager, "_watch_stream", idle)
    monkeypatch.setattr(manager, "_watch_liquidations_stream", idle)

    async def scenario() -> None:
        manager.subscribe("bybit", symbol)
        await asyncio.sleep(0)
        assert f"bybit:{symbol}:liquidations" in manager.active_tasks
        manager.unsubscribe("bybit", symbol)
        await asyncio.sleep(0)
        assert f"bybit:{symbol}:liquidations" not in manager.active_tasks

    asyncio.run(scenario())


def test_liquidation_only_subscription_upgrades_to_full_without_duplicate(monkeypatch) -> None:
    manager = WebSocketManager()
    symbol = "PRE/USDT:USDT"
    monkeypatch.setattr(ws_module, "ccxt_pro", object())

    async def idle(*args, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(manager, "watch_orderbook_stream", idle)
    monkeypatch.setattr(manager, "_watch_stream", idle)
    monkeypatch.setattr(manager, "_watch_liquidations_stream", idle)

    async def scenario() -> None:
        manager.subscribe_liquidations("bybit", symbol)
        await asyncio.sleep(0)
        liquidation_id = f"bybit:{symbol}:liquidations"
        assert set(manager.active_tasks) == {liquidation_id}
        liquidation_task = manager.active_tasks[liquidation_id]

        manager.subscribe("bybit", symbol)
        await asyncio.sleep(0)
        assert set(manager.active_tasks) == {
            f"bybit:{symbol}",
            f"bybit:{symbol}:ticker",
            f"bybit:{symbol}:trades",
            liquidation_id,
        }
        assert manager.active_tasks[liquidation_id] is liquidation_task

        manager.unsubscribe("bybit", symbol)
        await asyncio.sleep(0)
        assert not manager.active_tasks

    asyncio.run(scenario())


def test_full_subscription_can_downgrade_to_liquidation_only_without_restarting_liquidations(monkeypatch) -> None:
    manager = WebSocketManager()
    symbol = "DOWNGRADE/USDT:USDT"
    monkeypatch.setattr(ws_module, "ccxt_pro", object())

    async def idle(*args, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(manager, "watch_orderbook_stream", idle)
    monkeypatch.setattr(manager, "_watch_stream", idle)
    monkeypatch.setattr(manager, "_watch_liquidations_stream", idle)

    async def scenario() -> None:
        manager.subscribe("bybit", symbol)
        await asyncio.sleep(0)
        stream_id = f"bybit:{symbol}"
        liquidation_id = f"{stream_id}:liquidations"
        liquidation_task = manager.active_tasks[liquidation_id]
        manager.live_orderbooks[stream_id] = {"updated_at": 1.0, "data": {"bids": [], "asks": []}}
        manager.live_tickers[stream_id] = {"updated_at": 1.0, "data": {"last": 1.0}}
        manager.live_trades[stream_id] = {"updated_at": 1.0, "data": []}
        manager.live_liquidations[stream_id] = {"updated_at": 1.0, "events": []}
        manager.circuit_breakers[stream_id] = object()
        manager.circuit_breakers[liquidation_id] = object()
        manager.message_counters[stream_id] = 4
        manager.message_counters[liquidation_id] = 2

        manager.retain_liquidations_only("bybit", symbol)
        await asyncio.sleep(0)

        assert set(manager.active_tasks) == {liquidation_id}
        assert manager.active_tasks[liquidation_id] is liquidation_task
        assert stream_id not in manager.live_orderbooks
        assert stream_id not in manager.live_tickers
        assert stream_id not in manager.live_trades
        assert stream_id in manager.live_liquidations
        assert stream_id not in manager.circuit_breakers
        assert liquidation_id in manager.circuit_breakers
        assert stream_id not in manager.message_counters
        assert manager.message_counters[liquidation_id] == 2

        manager.unsubscribe("bybit", symbol)
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_liquidation_stream_skips_provider_without_declared_support(monkeypatch) -> None:
    manager = WebSocketManager()
    symbol = "NOSUPPORT/USDT:USDT"
    called = False

    class Exchange:
        has = {"watchLiquidations": None}

        async def watch_liquidations(self, *args, **kwargs):
            nonlocal called
            called = True
            await asyncio.sleep(1.0)
            return []

    async def get_exchange(_name):
        return Exchange()

    monkeypatch.setattr(manager, "_get_liquidation_exchange", lambda _name, _symbol: get_exchange(_name))

    async def scenario() -> None:
        task_id = f"bingx:{symbol}:liquidations"
        manager.active_tasks[task_id] = asyncio.current_task()
        await asyncio.wait_for(
            manager._watch_liquidations_stream("bingx", symbol), timeout=0.05
        )
        assert task_id not in manager.active_tasks

    asyncio.run(scenario())
    assert called is False


def test_bybit_pretrigger_scale_remains_one_liquidation_task_per_symbol(monkeypatch) -> None:
    manager = WebSocketManager()
    monkeypatch.setattr(ws_module, "ccxt_pro", object())

    async def idle(*args, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(manager, "_watch_liquidations_stream", idle)
    symbols = [f"PRE{i}/USDT:USDT" for i in range(44)]

    async def scenario() -> None:
        for symbol in symbols:
            manager.subscribe_liquidations("bybit", symbol)
        await asyncio.sleep(0)
        assert len(manager.active_tasks) == 44
        assert all(task_id.endswith(":liquidations") for task_id in manager.active_tasks)
        for symbol in symbols:
            manager.unsubscribe("bybit", symbol)
        await asyncio.sleep(0)
        assert manager.active_tasks == {}

    asyncio.run(scenario())


def test_unsupported_liquidation_capability_is_cached_between_subscriptions(monkeypatch) -> None:
    manager = WebSocketManager()
    symbol = "NOSUPPORTCACHE/USDT:USDT"
    monkeypatch.setattr(ws_module, "ccxt_pro", object())
    lookups = 0

    class Exchange:
        has = {"watchLiquidations": None}

    async def get_exchange(_name):
        nonlocal lookups
        lookups += 1
        return Exchange()

    monkeypatch.setattr(manager, "_get_liquidation_exchange", lambda _name, _symbol: get_exchange(_name))

    async def scenario() -> None:
        manager.subscribe_liquidations("bingx", symbol)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert f"bingx:{symbol}:liquidations" not in manager.active_tasks
        manager.subscribe_liquidations("bingx", symbol)
        await asyncio.sleep(0)
        assert f"bingx:{symbol}:liquidations" not in manager.active_tasks

    asyncio.run(scenario())
    assert lookups == 1


def test_gateio_exchange_id_is_mapped_at_websocket_boundary(monkeypatch) -> None:
    manager = WebSocketManager()
    created = []

    class Exchange:
        pass

    class FakeCCXTPro:
        @staticmethod
        def gate(config):
            created.append(config)
            return Exchange()

    monkeypatch.setattr(ws_module, "ccxt_pro", FakeCCXTPro())

    async def scenario() -> None:
        exchange = await manager._get_exchange("gateio")
        assert isinstance(exchange, Exchange)
        assert await manager._get_exchange("gateio") is exchange

    asyncio.run(scenario())
    assert len(created) == 1


def test_cancelled_liquidation_task_cannot_remove_replacement(monkeypatch) -> None:
    manager = WebSocketManager()
    symbol = "REPLACE/USDT:USDT"
    monkeypatch.setattr(ws_module, "ccxt_pro", object())
    started = 0

    class Exchange:
        has = {"watchLiquidations": True}

        async def watch_liquidations(self, *args, **kwargs):
            nonlocal started
            started += 1
            await asyncio.Future()

    async def get_exchange(_name):
        return Exchange()

    monkeypatch.setattr(
        manager,
        "_get_liquidation_exchange",
        lambda _name, _symbol: get_exchange(_name),
    )

    async def scenario() -> None:
        task_id = f"bybit:{symbol}:liquidations"
        manager.subscribe_liquidations("bybit", symbol)
        await asyncio.sleep(0)
        old_task = manager.active_tasks[task_id]
        manager.unsubscribe("bybit", symbol)
        manager.subscribe_liquidations("bybit", symbol)
        replacement = manager.active_tasks[task_id]
        assert replacement is not old_task
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert manager.active_tasks.get(task_id) is replacement
        assert replacement.done() is False
        manager.unsubscribe("bybit", symbol)
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert started >= 1


def test_binance_pretrigger_scale_uses_one_shared_liquidation_task(monkeypatch) -> None:
    manager = WebSocketManager()
    monkeypatch.setattr(ws_module, "ccxt_pro", object())

    async def idle(*args, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(manager, "_watch_liquidations_stream", idle)
    monkeypatch.setattr(
        manager,
        "_watch_shared_liquidations_stream",
        idle,
        raising=False,
    )
    symbols = [f"PRE{i}/USDT:USDT" for i in range(44)]

    async def scenario() -> None:
        for symbol in symbols:
            manager.subscribe_liquidations("binance", symbol)
        await asyncio.sleep(0)
        assert set(manager.active_tasks) == {"binance:liquidations"}
        assert manager.liquidation_subscribers["binance"] == set(symbols)
        for symbol in symbols:
            manager.unsubscribe("binance", symbol)
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_binance_shared_stream_routes_only_current_subscribers(monkeypatch) -> None:
    manager = WebSocketManager()
    monkeypatch.setattr(ws_module, "ccxt_pro", object())
    first = "AAA/USDT:USDT"
    second = "BBB/USDT:USDT"
    ignored = "ZZZ/USDT:USDT"
    calls = 0

    class Exchange:
        has = {"watchLiquidationsForSymbols": True}

        async def watch_liquidations_for_symbols(self, symbols, **kwargs):
            nonlocal calls
            calls += 1
            assert symbols == []
            if calls == 1:
                now_ms = int(ws_module.time.time() * 1000)
                return [
                    {"symbol": first, "timestamp": now_ms, "side": "sell", "quoteValue": 100.0},
                    {"symbol": second, "timestamp": now_ms, "side": "buy", "quoteValue": 50.0},
                    {"symbol": ignored, "timestamp": now_ms, "side": "sell", "quoteValue": 999.0},
                ]
            await asyncio.Future()

    async def get_exchange(_name):
        return Exchange()

    monkeypatch.setattr(manager, "_get_shared_liquidation_exchange", get_exchange)

    async def scenario() -> None:
        manager.subscribe_liquidations("binance", first)
        manager.subscribe_liquidations("binance", second)
        for _ in range(20):
            if f"binance:{first}" in manager.live_liquidations:
                break
            await asyncio.sleep(0)
        assert f"binance:{first}" in manager.live_liquidations
        assert f"binance:{second}" in manager.live_liquidations
        assert f"binance:{ignored}" not in manager.live_liquidations
        manager.unsubscribe("binance", first)
        manager.unsubscribe("binance", second)
        await manager.close_all()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_binance_shared_subscription_churn_does_not_duplicate_consumer(monkeypatch) -> None:
    manager = WebSocketManager()
    monkeypatch.setattr(ws_module, "ccxt_pro", object())

    async def idle(*args, **kwargs):
        await asyncio.Future()

    monkeypatch.setattr(manager, "_watch_shared_liquidations_stream", idle)

    async def scenario() -> None:
        manager.subscribe_liquidations("binance", "AAA/USDT:USDT")
        manager.subscribe_liquidations("binance", "BBB/USDT:USDT")
        await asyncio.sleep(0)
        task = manager.active_tasks["binance:liquidations"]
        manager.unsubscribe("binance", "AAA/USDT:USDT")
        manager.subscribe_liquidations("binance", "AAA/USDT:USDT")
        await asyncio.sleep(0)
        assert manager.active_tasks["binance:liquidations"] is task
        assert manager.liquidation_subscribers["binance"] == {
            "AAA/USDT:USDT", "BBB/USDT:USDT"
        }
        manager.unsubscribe("binance", "AAA/USDT:USDT")
        manager.unsubscribe("binance", "BBB/USDT:USDT")
        await asyncio.sleep(0)
        assert "binance:liquidations" not in manager.active_tasks
        assert "binance" not in manager.liquidation_subscribers
        await manager.close_all()
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_binance_shared_capability_failure_is_cached(monkeypatch) -> None:
    manager = WebSocketManager()
    monkeypatch.setattr(ws_module, "ccxt_pro", object())
    lookups = 0

    class Exchange:
        has = {"watchLiquidationsForSymbols": None}

    async def get_exchange(_name):
        nonlocal lookups
        lookups += 1
        return Exchange()

    monkeypatch.setattr(manager, "_get_shared_liquidation_exchange", get_exchange)

    async def scenario() -> None:
        manager.subscribe_liquidations("binance", "AAA/USDT:USDT")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "binance:liquidations" not in manager.active_tasks
        manager.subscribe_liquidations("binance", "BBB/USDT:USDT")
        await asyncio.sleep(0)
        assert "binance:liquidations" not in manager.active_tasks

    asyncio.run(scenario())
    assert lookups == 1


def test_runtime_diagnostics_exposes_liquidation_fanout_shape() -> None:
    manager = WebSocketManager()
    manager.active_tasks = {
        "binance:liquidations": object(),
        "bybit:AAA/USDT:USDT:liquidations": object(),
        "bybit:AAA/USDT:USDT": object(),
    }
    manager.liquidation_subscribers = {
        "binance": {"AAA/USDT:USDT", "BBB/USDT:USDT"},
    }

    assert manager.runtime_diagnostics() == {
        "active_tasks": 3,
        "liquidation_tasks": 2,
        "shared_liquidation_tasks": 1,
        "shared_liquidation_subscribers": 2,
        "shared_evidence_tasks": 0,
        "shared_evidence_subscribers": 0,
        "shared_evidence_active_subscribers": 0,
        "shared_evidence_exchange_instances": 0,
        "shared_evidence_reconcile_tasks": 0,
        "shared_evidence_retirement_failures": 0,
        "shared_evidence_blocked_exchanges": 0,
        "shared_evidence_direct_fallbacks": 0,
        "shared_evidence_generations": 0,
        "direct_exchange_instances": 0,
        "liquidation_exchange_instances": 0,
        "direct_ccxt_clients": 0,
        "direct_ccxt_subscriptions": 0,
        "direct_idle_ccxt_clients": 0,
        "liquidation_ccxt_clients": 0,
        "liquidation_ccxt_subscriptions": 0,
        "shared_evidence_ccxt_clients": 0,
        "shared_evidence_ccxt_subscriptions": 0,
        "direct_exchange_retire_tasks": 0,
        "liquidation_exchange_retire_tasks": 0,
        "exchange_close_finalizer_tasks": 0,
        "exchange_close_timeouts": 0,
        "ccxt_clients": 0,
        "ccxt_subscriptions": 0,
    }


def test_cancelled_shared_liquidation_task_cannot_remove_replacement(monkeypatch) -> None:
    manager = WebSocketManager()
    monkeypatch.setattr(ws_module, "ccxt_pro", object())
    started = 0

    class Exchange:
        has = {"watchLiquidationsForSymbols": True}

        async def watch_liquidations_for_symbols(self, symbols, **kwargs):
            nonlocal started
            started += 1
            await asyncio.Future()

    async def get_exchange(_name):
        return Exchange()

    monkeypatch.setattr(manager, "_get_shared_liquidation_exchange", get_exchange)

    async def scenario() -> None:
        task_id = "binance:liquidations"
        manager.subscribe_liquidations("binance", "AAA/USDT:USDT")
        await asyncio.sleep(0)
        old_task = manager.active_tasks[task_id]
        manager.unsubscribe("binance", "AAA/USDT:USDT")
        manager.subscribe_liquidations("binance", "BBB/USDT:USDT")
        replacement = manager.active_tasks[task_id]
        assert replacement is not old_task
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert manager.active_tasks.get(task_id) is replacement
        assert replacement.done() is False
        manager.unsubscribe("binance", "BBB/USDT:USDT")
        await asyncio.sleep(0)
        assert task_id not in manager.active_tasks

    asyncio.run(scenario())
    assert started >= 1


def test_liquidation_close_timeout_keeps_owned_finalizer_until_exchange_closes() -> None:
    manager = WebSocketManager()
    manager.exchange_close_timeout_seconds = 0.01
    symbol = "RETRY/USDT:USDT"
    stream_id = f"bybit:{symbol}"

    class Exchange:
        def __init__(self) -> None:
            self.close_calls = 0
            self.release_retry = asyncio.Event()
            self.closed = False

        async def close(self) -> None:
            self.close_calls += 1
            await self.release_retry.wait()
            self.closed = True

    exchange = Exchange()
    manager.liquidation_exchanges[stream_id] = exchange

    async def scenario() -> None:
        manager._schedule_liquidation_exchange_retire("bybit", symbol)
        await asyncio.sleep(0.03)
        assert exchange.close_calls == 1
        assert any(
            not task.done()
            for task in manager._liquidation_exchange_retire_tasks.values()
        )
        exchange.release_retry.set()
        await asyncio.sleep(0.03)
        assert exchange.closed is True
        assert not manager._liquidation_exchange_retire_tasks

    asyncio.run(scenario())


def test_liquidation_replacement_waits_for_prior_exchange_retirement(monkeypatch) -> None:
    manager = WebSocketManager()
    manager.exchange_close_timeout_seconds = 0.01
    symbol = "SERIAL/USDT:USDT"
    stream_id = f"bybit:{symbol}"

    class OldExchange:
        def __init__(self) -> None:
            self.calls = 0
            self.release = asyncio.Event()
        async def close(self) -> None:
            self.calls += 1
            await self.release.wait()

    old = OldExchange()
    replacement = object()
    manager.liquidation_exchanges[stream_id] = old
    monkeypatch.setattr(manager, "_new_exchange", lambda _name: replacement)

    async def scenario() -> None:
        manager._schedule_liquidation_exchange_retire("bybit", symbol)
        starter = asyncio.create_task(manager._get_liquidation_exchange("bybit", symbol))
        await asyncio.sleep(0.03)
        assert starter.done() is False
        old.release.set()
        assert await asyncio.wait_for(starter, timeout=0.1) is replacement

    asyncio.run(scenario())


def test_close_all_bounds_unfinished_exchange_close_finalizers() -> None:
    manager = WebSocketManager()
    manager.exchange_close_timeout_seconds = 0.01
    manager.retirement_timeout_seconds = 0.02
    symbol = "HANG/USDT:USDT"
    stream_id = f"bybit:{symbol}"

    class Exchange:
        async def close(self) -> None:
            await asyncio.Future()

    manager.liquidation_exchanges[stream_id] = Exchange()

    async def scenario() -> None:
        manager._schedule_liquidation_exchange_retire("bybit", symbol)
        await asyncio.sleep(0.03)
        assert manager._exchange_close_finalizer_tasks
        await asyncio.wait_for(manager.close_all(), timeout=0.2)
        assert not manager._exchange_close_finalizer_tasks
        assert not manager._liquidation_exchange_retire_tasks

    asyncio.run(scenario())


def test_close_all_bounds_hung_live_exchange_close() -> None:
    manager = WebSocketManager()
    manager.exchange_close_timeout_seconds = 0.01
    manager.retirement_timeout_seconds = 0.02

    class Exchange:
        async def close(self) -> None:
            await asyncio.Future()

    manager.exchanges["bybit"] = Exchange()

    async def scenario() -> None:
        await asyncio.wait_for(manager.close_all(), timeout=0.2)
        assert not manager._exchange_close_finalizer_tasks
        assert not manager.exchanges

    asyncio.run(scenario())


def test_new_liquidation_exchange_waits_only_for_same_venue_close_finalizer(monkeypatch) -> None:
    manager = WebSocketManager()
    manager.exchange_close_timeout_seconds = 0.01
    old_symbol = "OLD/USDT:USDT"
    new_symbol = "NEW/USDT:USDT"
    old_stream_id = f"bybit:{old_symbol}"

    class OldExchange:
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def close(self) -> None:
            await self.release.wait()

    old = OldExchange()
    created: list[tuple[str, object]] = []

    def new_exchange(ex_name: str) -> object:
        replacement = object()
        created.append((ex_name, replacement))
        return replacement

    manager.liquidation_exchanges[old_stream_id] = old
    monkeypatch.setattr(manager, "_new_exchange", new_exchange)

    async def scenario() -> None:
        manager._schedule_liquidation_exchange_retire("bybit", old_symbol)
        await asyncio.sleep(0.03)
        assert manager._exchange_close_finalizer_tasks

        same_venue = asyncio.create_task(
            manager._get_liquidation_exchange("bybit", new_symbol)
        )
        await asyncio.sleep(0.02)
        assert same_venue.done() is False

        unrelated = await asyncio.wait_for(
            manager._get_liquidation_exchange("okx", "OKX/USDT:USDT"),
            timeout=0.05,
        )
        assert unrelated is created[-1][1]
        assert created[-1][0] == "okx"

        old.release.set()
        bybit = await asyncio.wait_for(same_venue, timeout=0.1)
        assert bybit is created[-1][1]
        assert created[-1][0] == "bybit"

    asyncio.run(scenario())


def test_close_all_drains_retirement_scheduled_by_cancelled_active_stream() -> None:
    manager = WebSocketManager()
    manager.exchange_close_timeout_seconds = 0.01
    manager.retirement_timeout_seconds = 0.02
    symbol = "SHUTDOWN/USDT:USDT"
    stream_id = f"bybit:{symbol}"

    class Exchange:
        async def close(self) -> None:
            await asyncio.Future()

    manager.liquidation_exchanges[stream_id] = Exchange()

    async def active_stream() -> None:
        try:
            await asyncio.Future()
        finally:
            manager._schedule_liquidation_exchange_retire("bybit", symbol)

    async def scenario() -> None:
        task_id = f"{stream_id}:liquidations"
        task = asyncio.create_task(active_stream())
        manager.active_tasks[task_id] = task
        await asyncio.sleep(0)
        await asyncio.wait_for(manager.close_all(), timeout=0.2)
        await asyncio.sleep(0.03)
        assert not manager._liquidation_exchange_retire_tasks
        assert not manager._exchange_close_finalizer_tasks

    asyncio.run(scenario())


def test_new_liquidation_exchange_rechecks_same_venue_retirement_before_admission(monkeypatch) -> None:
    manager = WebSocketManager()
    manager.exchange_close_timeout_seconds = 1.0
    old_symbol = "RACING-OLD/USDT:USDT"
    new_symbol = "RACING-NEW/USDT:USDT"
    old_stream_id = f"bybit:{old_symbol}"

    class OldExchange:
        def __init__(self) -> None:
            self.release = asyncio.Event()

        async def close(self) -> None:
            await self.release.wait()

    old = OldExchange()
    created: list[tuple[str, object]] = []

    def new_exchange(ex_name: str) -> object:
        replacement = object()
        created.append((ex_name, replacement))
        return replacement

    manager.liquidation_exchanges[old_stream_id] = old
    monkeypatch.setattr(manager, "_new_exchange", new_exchange)

    async def scenario() -> None:
        await manager._lock.acquire()
        try:
            replacement_task = asyncio.create_task(
                manager._get_liquidation_exchange("bybit", new_symbol)
            )
            await asyncio.sleep(0)
            manager._schedule_liquidation_exchange_retire("bybit", old_symbol)
        finally:
            manager._lock.release()

        await asyncio.sleep(0.02)
        assert replacement_task.done() is False
        assert created == []

        unrelated = await asyncio.wait_for(
            manager._get_liquidation_exchange("okx", "RACING-OKX/USDT:USDT"),
            timeout=0.05,
        )
        assert unrelated is created[-1][1]
        assert created[-1][0] == "okx"

        old.release.set()
        replacement = await asyncio.wait_for(replacement_task, timeout=0.2)
        assert replacement is created[-1][1]
        assert created[-1][0] == "bybit"

    asyncio.run(scenario())
