from __future__ import annotations

import asyncio
import gc

import waterfallhunter.core.ws_streamer as ws_streamer_module
from waterfallhunter.core.ws_streamer import WebSocketManager


def _book(price: float, *, timestamp_ms: int) -> dict:
    return {
        "symbol": "TEST/USDT:USDT",
        "timestamp": timestamp_ms,
        "bids": [[price, 10.0]],
        "asks": [[price + 0.01, 10.0]],
    }


def _trade(index: int, *, timestamp_ms: int) -> dict:
    return {
        "id": f"t-{index}",
        "symbol": "TEST/USDT:USDT",
        "timestamp": timestamp_ms,
        "side": "sell" if index % 2 else "buy",
        "price": 1.0,
        "amount": 1.0,
    }


def test_orderbook_history_is_deep_copied_bounded_and_temporally_valid() -> None:
    manager = WebSocketManager()
    symbol = "TEST/USDT:USDT"
    first = _book(1.0, timestamp_ms=10_000)
    manager._ingest_orderbook("binance", symbol, first, received_at=10.0)
    first["bids"][0][0] = 99.0
    manager._ingest_orderbook(
        "binance", symbol, _book(1.1, timestamp_ms=10_300), received_at=10.3
    )
    manager._ingest_orderbook(
        "binance", symbol, _book(1.2, timestamp_ms=10_600), received_at=10.6
    )

    samples = manager.get_realtime_orderbook_samples(
        "binance", symbol, count=3, min_span_seconds=0.5, now=10.7
    )

    assert [row["bids"][0][0] for row in samples] == [1.0, 1.1, 1.2]
    assert [row["_received_at"] for row in samples] == [10.0, 10.3, 10.6]

    for index in range(12):
        received_at = 11.0 + index * 0.1
        manager._ingest_orderbook(
            "binance",
            symbol,
            _book(2.0 + index, timestamp_ms=int(received_at * 1000)),
            received_at=received_at,
        )

    assert len(manager.live_orderbook_history[f"binance:{symbol}"]) <= 8


def test_orderbook_samples_select_time_spanning_rows_from_dense_history() -> None:
    manager = WebSocketManager()
    symbol = "TEST/USDT:USDT"
    for index in range(8):
        received_at = 10.0 + index * 0.1
        manager._ingest_orderbook(
            "binance",
            symbol,
            _book(1.0 + index, timestamp_ms=int(received_at * 1000)),
            received_at=received_at,
        )

    samples = manager.get_realtime_orderbook_samples(
        "binance", symbol, count=3, min_span_seconds=0.5, now=10.8
    )

    assert len(samples) == 3
    observed = [row["_received_at"] for row in samples]
    assert observed == sorted(observed)
    assert observed[-1] == 10.7
    assert observed[-1] - observed[0] >= 0.5


def test_orderbook_samples_fail_closed_when_history_is_too_old_or_too_tight() -> None:
    manager = WebSocketManager()
    symbol = "TEST/USDT:USDT"
    for received_at in (10.0, 10.1, 10.2):
        manager._ingest_orderbook(
            "binance",
            symbol,
            _book(1.0, timestamp_ms=int(received_at * 1000)),
            received_at=received_at,
        )

    assert manager.get_realtime_orderbook_samples(
        "binance", symbol, count=3, min_span_seconds=0.5, now=10.3
    ) == []
    assert manager.get_realtime_orderbook_samples(
        "binance", symbol, count=3, min_span_seconds=0.1, now=16.0
    ) == []


def test_trade_window_deduplicates_prunes_and_stays_bounded() -> None:
    manager = WebSocketManager()
    symbol = "TEST/USDT:USDT"
    now = 100.0
    rows = [_trade(index, timestamp_ms=99_000 + index) for index in range(600)]
    rows.append(dict(rows[-1]))
    rows.append(_trade(9999, timestamp_ms=1_000))

    manager._ingest_trades("binance", symbol, rows, received_at=now)
    trades = manager.get_realtime_trades("binance", symbol, now=now)

    assert len(trades) == 500
    assert len({trade["id"] for trade in trades}) == 500
    assert all(now * 1000 - trade["timestamp"] <= 60_000 for trade in trades)


def test_trade_ingest_does_not_deepcopy_existing_history_on_each_update(monkeypatch) -> None:
    manager = WebSocketManager()
    symbol = "TEST/USDT:USDT"
    now = 100.0
    initial = [_trade(index, timestamp_ms=99_000 + index) for index in range(500)]
    manager._ingest_trades("binance", symbol, initial, received_at=now)

    calls = 0
    original_deepcopy = ws_streamer_module.copy.deepcopy

    def counted(value):
        nonlocal calls
        calls += 1
        return original_deepcopy(value)

    monkeypatch.setattr(ws_streamer_module.copy, "deepcopy", counted)
    incoming = _trade(501, timestamp_ms=99_900)
    incoming["info"] = {"nested": {"large": [1, 2, 3]}}
    manager._ingest_trades("binance", symbol, [incoming], received_at=now)

    assert calls <= 1


def test_trade_cache_detaches_and_keeps_only_evidence_fields() -> None:
    manager = WebSocketManager()
    symbol = "TEST/USDT:USDT"
    row = _trade(1, timestamp_ms=99_900)
    row.update({
        "datetime": "1970-01-01T00:01:39.900Z",
        "cost": 1.0,
        "takerOrMaker": "taker",
        "info": {"mutable": {"value": 1}},
    })
    manager._ingest_trades("binance", symbol, [row], received_at=100.0)
    row["price"] = 999.0
    row["info"]["mutable"]["value"] = 2

    cached = manager.live_trades[f"binance:{symbol}"]["data"][0]
    assert cached["price"] == 1.0
    assert "info" not in cached
    projected = manager.get_realtime_trades("binance", symbol, now=100.0)
    projected[0]["price"] = 77.0
    assert manager.live_trades[f"binance:{symbol}"]["data"][0]["price"] == 1.0
    assert set(cached) <= {
        "id", "timestamp", "datetime", "symbol", "side",
        "price", "amount", "cost", "takerOrMaker",
    }


def test_unsubscribe_removes_hot_evidence_history() -> None:
    manager = WebSocketManager()
    symbol = "TEST/USDT:USDT"
    manager._ingest_orderbook(
        "binance", symbol, _book(1.0, timestamp_ms=10_000), received_at=10.0
    )
    manager._ingest_trades(
        "binance", symbol, [_trade(1, timestamp_ms=10_000)], received_at=10.0
    )

    manager.unsubscribe("binance", symbol)

    assert manager.get_realtime_orderbook_samples(
        "binance", symbol, count=1, min_span_seconds=0.0, now=10.1
    ) == []
    assert manager.get_realtime_trades("binance", symbol, now=10.1) == []
    assert f"binance:{symbol}" not in manager.live_orderbook_history


class _FakeCcxtClient:
    def __init__(self, subscriptions: int = 1, futures: int = 0) -> None:
        self.subscriptions = {f"sub-{index}": object() for index in range(subscriptions)}
        self.futures = {f"future-{index}": object() for index in range(futures)}
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


class _ClosableExchange:
    def __init__(self, clients: int = 1, subscriptions_each: int = 1) -> None:
        self.clients = {
            f"ws-{index}": _FakeCcxtClient(subscriptions_each)
            for index in range(clients)
        }
        self.close_calls = 0
        self.unwatch_calls: list[tuple[str, str]] = []

    def _retire_one_subscription(self, method: str, symbol: str) -> None:
        self.unwatch_calls.append((method, symbol))
        for client in self.clients.values():
            if client.subscriptions:
                client.subscriptions.pop(next(iter(client.subscriptions)))
                return

    async def un_watch_order_book(self, symbol: str) -> None:
        self._retire_one_subscription("orderbook", symbol)

    async def un_watch_ticker(self, symbol: str) -> None:
        self._retire_one_subscription("ticker", symbol)

    async def un_watch_trades(self, symbol: str) -> None:
        self._retire_one_subscription("trades", symbol)

    async def close(self) -> None:
        self.close_calls += 1
        self.clients.clear()


def test_direct_exchange_is_shared_per_venue(monkeypatch) -> None:
    manager = WebSocketManager()
    created: list[_ClosableExchange] = []

    def new_exchange(_ex_name: str) -> _ClosableExchange:
        exchange = _ClosableExchange()
        created.append(exchange)
        return exchange

    monkeypatch.setattr(manager, "_new_exchange", new_exchange)

    async def scenario():
        first = await manager._get_exchange("binance")
        same = await manager._get_exchange("binance")
        return first, same

    first, same = asyncio.run(scenario())

    assert first is same
    assert len(created) == 1
    assert manager.exchanges == {"binance": first}


def test_unsubscribe_unwatches_symbol_and_closes_idle_ccxt_clients() -> None:
    manager = WebSocketManager()
    symbol = "AAA/USDT:USDT"
    exchange = _ClosableExchange(clients=3, subscriptions_each=1)
    clients = list(exchange.clients.values())

    async def scenario() -> None:
        manager.exchanges["bybit"] = exchange
        manager.unsubscribe("bybit", symbol)
        task = manager._direct_symbol_retire_tasks[f"bybit:{symbol}"]
        await task
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert exchange.unwatch_calls == [
        ("orderbook", symbol),
        ("ticker", symbol),
        ("trades", symbol),
    ]
    assert exchange.clients == {}
    assert [client.close_calls for client in clients] == [1, 1, 1]
    assert exchange.close_calls == 0
    assert manager._direct_symbol_retire_tasks == {}


def test_direct_retirement_closes_idle_clients_while_other_symbol_remains_active() -> None:
    manager = WebSocketManager()
    retired_symbol = "OLD/USDT:USDT"
    survivor_symbol = "KEEP/USDT:USDT"
    exchange = _ClosableExchange(clients=6, subscriptions_each=1)
    clients = list(exchange.clients.values())

    async def survivor() -> None:
        await asyncio.Future()

    async def scenario() -> None:
        manager.exchanges["binance"] = exchange
        survivor_task = asyncio.create_task(survivor())
        manager.active_tasks[f"binance:{survivor_symbol}"] = survivor_task
        try:
            await manager._retire_direct_symbol("binance", retired_symbol, ())
        finally:
            manager.active_tasks.pop(f"binance:{survivor_symbol}", None)
            survivor_task.cancel()
            await asyncio.gather(survivor_task, return_exceptions=True)

    asyncio.run(scenario())

    assert list(exchange.clients) == ["ws-3", "ws-4", "ws-5"]
    assert [client.close_calls for client in clients] == [1, 1, 1, 0, 0, 0]


def test_direct_idle_cleanup_preserves_client_with_pending_future() -> None:
    manager = WebSocketManager()
    idle = _FakeCcxtClient(subscriptions=0, futures=0)
    pending = _FakeCcxtClient(subscriptions=0, futures=1)
    active = _FakeCcxtClient(subscriptions=1, futures=0)
    exchange = _ClosableExchange(clients=0)
    exchange.clients = {"idle": idle, "pending": pending, "active": active}

    asyncio.run(manager._close_idle_ccxt_clients("binance", exchange))

    assert list(exchange.clients) == ["pending", "active"]
    assert idle.close_calls == 1
    assert pending.close_calls == 0
    assert active.close_calls == 0


def test_retain_liquidations_retires_direct_clients_without_restarting_liquidation(monkeypatch) -> None:
    manager = WebSocketManager()
    symbol = "AAA/USDT:USDT"
    stream_id = f"bybit:{symbol}"
    liquidation_id = f"{stream_id}:liquidations"
    direct_exchange = _ClosableExchange(clients=3, subscriptions_each=1)
    liquidation_exchange = _ClosableExchange(clients=1)
    restarted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        manager,
        "subscribe_liquidations",
        lambda ex_name, mapped_symbol: restarted.append((ex_name, mapped_symbol)),
    )

    async def idle() -> None:
        await asyncio.Future()

    async def scenario() -> None:
        manager.exchanges["bybit"] = direct_exchange
        manager.liquidation_exchanges[stream_id] = liquidation_exchange
        liquidation_task = asyncio.create_task(idle())
        manager.active_tasks[liquidation_id] = liquidation_task
        manager.retain_liquidations_only("bybit", symbol)
        await manager._direct_symbol_retire_tasks[stream_id]
        assert manager.active_tasks[liquidation_id] is liquidation_task
        liquidation_task.cancel()
        await asyncio.gather(liquidation_task, return_exceptions=True)

    asyncio.run(scenario())

    assert direct_exchange.clients == {}
    assert direct_exchange.close_calls == 0
    assert liquidation_exchange.close_calls == 0
    assert manager.liquidation_exchanges[stream_id] is liquidation_exchange
    assert restarted == [("bybit", symbol)]


def test_unsubscribe_closes_non_binance_liquidation_exchange() -> None:
    manager = WebSocketManager()
    symbol = "AAA/USDT:USDT"
    stream_id = f"bybit:{symbol}"
    liquidation_exchange = _ClosableExchange(clients=2)

    async def scenario() -> None:
        manager.liquidation_exchanges[stream_id] = liquidation_exchange
        manager.unsubscribe("bybit", symbol)
        tasks = list(manager._liquidation_exchange_retire_tasks.values())
        if tasks:
            await asyncio.gather(*tasks)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert liquidation_exchange.close_calls == 1
    assert stream_id not in manager.liquidation_exchanges


def test_last_binance_liquidation_subscriber_closes_shared_exchange() -> None:
    manager = WebSocketManager()
    symbol = "AAA/USDT:USDT"
    exchange = _ClosableExchange(clients=1)

    async def idle() -> None:
        await asyncio.Future()

    async def scenario() -> None:
        manager.liquidation_subscribers["binance"] = {symbol}
        manager.shared_liquidation_exchanges["binance"] = exchange
        task = asyncio.create_task(idle())
        manager.active_tasks["binance:liquidations"] = task
        manager.unsubscribe("binance", symbol)
        retire = manager._shared_liquidation_retire_tasks["binance"]
        await retire
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert exchange.close_calls == 1
    assert "binance" not in manager.shared_liquidation_exchanges
    assert "binance" not in manager.liquidation_subscribers


def test_cancelled_task_settlement_consumes_completed_exceptions() -> None:
    manager = WebSocketManager()
    unhandled: list[dict] = []

    async def fail() -> None:
        raise RuntimeError("synthetic settled-task failure")

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        task = asyncio.create_task(fail())
        await asyncio.sleep(0)
        assert task.done()
        await manager._settle_cancelled_tasks((task,), context="test")
        del task
        gc.collect()
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert unhandled == []


def test_direct_retirement_bounds_cancelled_task_settlement() -> None:
    manager = WebSocketManager()
    manager.retirement_timeout_seconds = 0.02

    async def slow_cancel() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)

    async def scenario() -> None:
        task = asyncio.create_task(slow_cancel())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.wait_for(
            manager._retire_direct_symbol("binance", "TEST/USDT:USDT", (task,)),
            timeout=0.1,
        )

    asyncio.run(scenario())


def test_exchange_retirement_bounds_cancelled_task_settlement() -> None:
    manager = WebSocketManager()
    manager.retirement_timeout_seconds = 0.02
    exchange = _ClosableExchange()

    async def slow_cancel() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)

    async def scenario() -> None:
        task = asyncio.create_task(slow_cancel())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.wait_for(
            manager._close_exchange_instance(
                "binance", "binance:test", exchange, (task,)
            ),
            timeout=0.1,
        )

    asyncio.run(scenario())
    assert exchange.close_calls == 1


def test_close_all_continues_after_exchange_close_failure() -> None:
    manager = WebSocketManager()

    class BrokenClose:
        async def close(self) -> None:
            raise RuntimeError("synthetic close failure")

    good = _ClosableExchange()
    manager.exchanges["broken"] = BrokenClose()
    manager.exchanges["good"] = good

    asyncio.run(manager.close_all())

    assert good.close_calls == 1
    assert manager.exchanges == {}


def test_close_all_bounds_slow_exchange_close() -> None:
    manager = WebSocketManager()
    manager.exchange_close_timeout_seconds = 0.02

    class SlowClose:
        async def close(self) -> None:
            await asyncio.sleep(0.2)

    good = _ClosableExchange()
    manager.exchanges["slow"] = SlowClose()
    manager.exchanges["good"] = good

    async def scenario() -> None:
        await asyncio.wait_for(manager.close_all(), timeout=0.1)

    asyncio.run(scenario())

    assert good.close_calls == 1
    assert manager.exchanges == {}


def test_runtime_diagnostics_exposes_ccxt_client_ownership() -> None:
    manager = WebSocketManager()
    direct_exchange = _ClosableExchange(clients=3, subscriptions_each=1)
    direct_exchange.clients["retired-idle"] = _FakeCcxtClient(subscriptions=0)
    manager.exchanges["bybit"] = direct_exchange
    manager.liquidation_exchanges["bybit:AAA/USDT:USDT"] = _ClosableExchange(
        clients=1, subscriptions_each=1
    )
    manager.shared_liquidation_exchanges["binance"] = _ClosableExchange(
        clients=1, subscriptions_each=1
    )
    manager.shared_evidence_exchanges["okx"] = _ClosableExchange(
        clients=2, subscriptions_each=2
    )

    snapshot = manager.runtime_diagnostics()

    assert snapshot["direct_exchange_instances"] == 1
    assert snapshot["liquidation_exchange_instances"] == 2
    assert snapshot["direct_ccxt_clients"] == 4
    assert snapshot["direct_ccxt_subscriptions"] == 3
    assert snapshot["direct_idle_ccxt_clients"] == 1
    assert snapshot["liquidation_ccxt_clients"] == 2
    assert snapshot["liquidation_ccxt_subscriptions"] == 2
    assert snapshot["shared_evidence_ccxt_clients"] == 2
    assert snapshot["shared_evidence_ccxt_subscriptions"] == 4
    assert snapshot["ccxt_clients"] == 8
    assert snapshot["ccxt_subscriptions"] == 9
    assert snapshot["direct_exchange_retire_tasks"] == 0
    assert snapshot["liquidation_exchange_retire_tasks"] == 0


class _SlowUnwatchExchange(_ClosableExchange):
    def __init__(self) -> None:
        super().__init__(clients=3, subscriptions_each=1)
        self.release = asyncio.Event()

    async def un_watch_order_book(self, symbol: str) -> None:
        await self.release.wait()
        await super().un_watch_order_book(symbol)


def test_resubscribe_waits_for_prior_direct_symbol_retirement() -> None:
    manager = WebSocketManager()
    symbol = "CHURN/USDT:USDT"
    stream_id = f"bybit:{symbol}"
    exchange = _SlowUnwatchExchange()

    async def scenario() -> None:
        manager.exchanges["bybit"] = exchange
        manager._schedule_direct_symbol_retire("bybit", symbol)
        waiter = asyncio.create_task(
            manager._await_direct_symbol_retirement("bybit", symbol)
        )
        await asyncio.sleep(0)
        assert waiter.done() is False
        exchange.release.set()
        await waiter
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert stream_id not in manager._direct_symbol_retire_tasks
    assert exchange.clients == {}


def test_cross_symbol_direct_start_waits_for_shared_client_retirement() -> None:
    manager = WebSocketManager()
    old_symbol = "OLD/USDT:USDT"
    new_symbol = "NEW/USDT:USDT"
    new_stream_id = f"bybit:{new_symbol}"

    class RaceClient:
        def __init__(self, initial_subscription: str | None = None) -> None:
            self.subscriptions = (
                {initial_subscription: object()} if initial_subscription else {}
            )
            self.futures = {}
            self.close_started = asyncio.Event()
            self.close_release = asyncio.Event()
            self.closed = False

        async def close(self) -> None:
            self.close_started.set()
            await self.close_release.wait()
            self.closed = True
            self.subscriptions.clear()

    class RaceExchange:
        def __init__(self) -> None:
            self.initial_client = RaceClient("old-orderbook")
            self.clients = {"ws": self.initial_client}
            self.new_subscribed = asyncio.Event()
            self.new_release = asyncio.Event()

        async def un_watch_order_book(self, _symbol: str) -> None:
            client = self.clients.get("ws")
            if client and client.subscriptions:
                client.subscriptions.pop(next(iter(client.subscriptions)))

        async def un_watch_ticker(self, _symbol: str) -> None:
            return None

        async def un_watch_trades(self, _symbol: str) -> None:
            return None

        async def watch_order_book(self, symbol: str, *, limit: int) -> dict:
            client = self.clients.get("ws")
            if client is None:
                client = RaceClient()
                client.close_release.set()
                self.clients["ws"] = client
            client.subscriptions[f"book:{symbol}"] = object()
            self.new_subscribed.set()
            await self.new_release.wait()
            return {"symbol": symbol, "bids": [[1.0, 1.0]], "asks": [[1.1, 1.0]]}

    exchange = RaceExchange()

    async def scenario() -> None:
        manager.exchanges["bybit"] = exchange
        retire = asyncio.create_task(
            manager._retire_direct_symbol("bybit", old_symbol, ())
        )
        await asyncio.wait_for(exchange.initial_client.close_started.wait(), timeout=1.0)

        new_task = asyncio.create_task(
            manager.watch_orderbook_stream("bybit", new_symbol)
        )
        manager.active_tasks[new_stream_id] = new_task
        try:
            await asyncio.sleep(0.02)
            assert exchange.new_subscribed.is_set() is False

            exchange.initial_client.close_release.set()
            await asyncio.wait_for(retire, timeout=1.0)
            await asyncio.wait_for(exchange.new_subscribed.wait(), timeout=1.0)

            active_client = exchange.clients.get("ws")
            assert active_client is not None
            assert active_client.closed is False
            assert f"book:{new_symbol}" in active_client.subscriptions
        finally:
            exchange.initial_client.close_release.set()
            exchange.new_release.set()
            manager.active_tasks.pop(new_stream_id, None)
            new_task.cancel()
            await asyncio.gather(new_task, retire, return_exceptions=True)

    asyncio.run(scenario())
