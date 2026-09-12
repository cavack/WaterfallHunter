import asyncio
import time

import pytest

from waterfallhunter.core.microstructure import MicrostructureAnalyzer


class FreshExchange:
    async def fetch_order_book(self, symbol, limit):
        now = int(time.time() * 1000)
        return {"timestamp": now, "bids": [[10.0, 100.0]], "asks": [[10.1, 100.0]]}

    async def fetch_trades(self, symbol, limit):
        now = int(time.time() * 1000)
        return [{"timestamp": now, "side": "sell", "price": 10.0, "amount": 1.0} for _ in range(20)]


def test_microstructure_rejects_a_stale_initial_orderbook_snapshot():
    result = asyncio.run(MicrostructureAnalyzer().analyze(
        FreshExchange(), "TEST/USDT:USDT",
        {"timestamp": int(time.time() * 1000) - 6_000, "bids": [[10.0, 100.0]], "asks": [[10.1, 100.0]]},
        {"limits": {}, "contractSize": 1},
    ))

    assert result == {"approved": False, "reason": "stale orderbook snapshot"}


def test_contract_orderbook_amounts_are_converted_to_usdt_notional():
    levels = [[0.000005, 10.0]]

    assert MicrostructureAnalyzer._depth(levels, contract_size=1_000_000) == 50.0
    assert MicrostructureAnalyzer._vwap(levels, notional=50.0, contract_size=1_000_000) == 0.000005


def test_microstructure_reports_real_entry_and_exit_vwap_slippage():
    now = int(time.time() * 1000)
    book = {
        "timestamp": now,
        "bids": [[10.0, 2.0], [9.9, 10.0]],
        "asks": [[10.1, 2.0], [10.2, 10.0]],
    }
    class DepthExchange:
        async def fetch_order_book(self, symbol, limit):
            return {**book, "timestamp": int(time.time() * 1000)}

        async def fetch_trades(self, symbol, limit):
            timestamp = int(time.time() * 1000)
            return [{"timestamp": timestamp, "side": "sell", "price": 10.0, "amount": 1.0} for _ in range(20)]

    result = asyncio.run(MicrostructureAnalyzer(executable_notional=50.0).analyze(
        DepthExchange(), "TEST/USDT:USDT", book,
        {"limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}}, "contractSize": 1},
    ))

    expected_sell_vwap = 50.0 / (2.0 + 30.0 / 9.9)
    expected_buy_vwap = 50.0 / (2.0 + 29.8 / 10.2)
    assert result["sell_vwap"] == pytest.approx(expected_sell_vwap)
    assert result["buy_vwap"] == pytest.approx(expected_buy_vwap)
    assert result["entry_slippage_pct"] == round((10.0 - expected_sell_vwap) / 10.0 * 100.0, 4)
    assert result["exit_slippage_pct"] == round((expected_buy_vwap - 10.1) / 10.1 * 100.0, 4)
    assert result["executable"] is True
    assert isinstance(result["observed_at"], float)
    assert 0 <= time.time() - result["observed_at"] < 2.0
    assert result["source_capture"]["raw_trades_captured"] is True
    assert len(result["source_capture"]["fresh_trades"]) == 20
    assert set(result["source_capture"]["fresh_trades"][0]) == {
        "timestamp", "side", "price", "amount"
    }
    assert result["source_capture"]["orderbook_snapshots_captured"] is True
    assert len(result["source_capture"]["orderbook_snapshots"]) == 3
    assert result["source_capture"]["market_filters_captured"] is True
    assert result["source_capture"]["market"]["contractSize"] == 1


def test_source_capture_keeps_every_orderbook_level_consumed_by_production():
    now = int(time.time() * 1000)
    bids = [[10.0 - index * 0.01, 2.0 + index] for index in range(30)]
    asks = [[10.1 + index * 0.01, 2.0 + index] for index in range(30)]
    first = {"timestamp": now, "bids": bids, "asks": asks}

    class LimitIgnoringExchange:
        async def fetch_order_book(self, symbol, limit):
            assert limit == 20
            return {
                "timestamp": int(time.time() * 1000),
                "bids": bids,
                "asks": asks,
            }

        async def fetch_trades(self, symbol, limit):
            timestamp = int(time.time() * 1000)
            return [
                {"timestamp": timestamp, "side": "sell", "price": 10.0, "amount": 1.0}
                for _ in range(20)
            ]

    result = asyncio.run(
        MicrostructureAnalyzer(snapshot_delay_seconds=0.0).analyze(
            LimitIgnoringExchange(),
            "TEST/USDT:USDT",
            first,
            {
                "limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}},
                "precision": {},
                "contractSize": 1.0,
            },
        )
    )

    captured = result["source_capture"]["orderbook_snapshots"]
    assert [len(snapshot["bids"]) for snapshot in captured] == [30, 30, 30]
    assert [len(snapshot["asks"]) for snapshot in captured] == [30, 30, 30]
    assert result["bid_depth_usdt"] == pytest.approx(
        MicrostructureAnalyzer._depth(captured[-1]["bids"])
    )
    assert result["ask_depth_usdt"] == pytest.approx(
        MicrostructureAnalyzer._depth(captured[-1]["asks"])
    )


def test_trade_fetch_starts_before_delayed_orderbook_sampling():
    events = []

    class ConcurrentExchange:
        def __init__(self):
            self.book_calls = 0

        async def fetch_order_book(self, symbol, limit):
            self.book_calls += 1
            events.append(f"book_{self.book_calls}")
            now = int(time.time() * 1000)
            return {"timestamp": now, "bids": [[10.0, 100.0]], "asks": [[10.1, 100.0]]}

        async def fetch_trades(self, symbol, limit):
            events.append("trades_started")
            now = int(time.time() * 1000)
            return [
                {"timestamp": now, "side": "sell", "price": 10.0, "amount": 1.0}
                for _ in range(20)
            ]

    now = int(time.time() * 1000)
    first = {"timestamp": now, "bids": [[10.0, 100.0]], "asks": [[10.1, 100.0]]}
    result = asyncio.run(
        MicrostructureAnalyzer(snapshot_delay_seconds=0.0).analyze(
            ConcurrentExchange(),
            "TEST/USDT:USDT",
            first,
            {
                "limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}},
                "contractSize": 1.0,
            },
        )
    )

    assert result["source_capture"]["raw_trades_captured"] is True
    assert events.index("trades_started") < events.index("book_1")


def test_microstructure_cancels_parallel_trade_fetch_when_analysis_is_cancelled():
    async def scenario():
        trade_started = asyncio.Event()
        trade_cancelled = asyncio.Event()

        class SlowExchange:
            async def fetch_order_book(self, symbol, limit):
                await asyncio.sleep(10)

            async def fetch_trades(self, symbol, limit):
                trade_started.set()
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    trade_cancelled.set()
                    raise

        now = int(time.time() * 1000)
        first = {"timestamp": now, "bids": [[10.0, 100.0]], "asks": [[10.1, 100.0]]}
        task = asyncio.create_task(
            MicrostructureAnalyzer(snapshot_delay_seconds=0.0).analyze(
                SlowExchange(),
                "TEST/USDT:USDT",
                first,
                {"limits": {}, "contractSize": 1.0},
            )
        )
        await trade_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert trade_cancelled.is_set()

    asyncio.run(scenario())


def test_complete_preloaded_microstructure_evidence_avoids_rest_calls() -> None:
    now = time.time()
    snapshots = []
    for offset in (0.6, 0.3, 0.0):
        observed = now - offset
        snapshots.append({
            "timestamp": int(observed * 1000),
            "_received_at": observed,
            "bids": [[10.0, 100.0]],
            "asks": [[10.1, 100.0]],
        })
    trades = [
        {
            "id": f"pre-{index}",
            "timestamp": int(now * 1000),
            "side": "sell",
            "price": 10.0,
            "amount": 1.0,
        }
        for index in range(20)
    ]

    class NoRestExchange:
        async def fetch_order_book(self, symbol, limit):
            raise AssertionError("REST orderbook must not be called")

        async def fetch_trades(self, symbol, limit):
            raise AssertionError("REST trades must not be called")

    result = asyncio.run(
        MicrostructureAnalyzer(snapshot_delay_seconds=0.25).analyze(
            NoRestExchange(),
            "TEST/USDT:USDT",
            snapshots[-1],
            {
                "limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}},
                "contractSize": 1.0,
            },
            preloaded_snapshots=snapshots,
            preloaded_trades=trades,
        )
    )

    assert result["source_capture"]["raw_trades_captured"] is True
    assert result["source_capture"]["orderbook_snapshots_captured"] is True
    assert len(result["source_capture"]["orderbook_snapshots"]) == 3


def _fresh_preloaded_snapshots(now: float) -> list[dict]:
    return [
        {
            "timestamp": int((now - offset) * 1000),
            "_received_at": now - offset,
            "bids": [[10.0, 100.0]],
            "asks": [[10.1, 100.0]],
        }
        for offset in (0.6, 0.3, 0.0)
    ]


def _fresh_preloaded_trades(now: float) -> list[dict]:
    return [
        {
            "id": f"partial-{index}",
            "timestamp": int(now * 1000),
            "side": "sell",
            "price": 10.0,
            "amount": 1.0,
        }
        for index in range(20)
    ]


def test_fresh_preloaded_snapshots_only_skip_rest_orderbooks() -> None:
    now = time.time()
    snapshots = _fresh_preloaded_snapshots(now)

    class CountingExchange:
        def __init__(self) -> None:
            self.book_calls = 0
            self.trade_calls = 0

        async def fetch_order_book(self, symbol, limit):
            self.book_calls += 1
            raise AssertionError("fresh preloaded snapshots must be reused")

        async def fetch_trades(self, symbol, limit):
            self.trade_calls += 1
            observed = int(time.time() * 1000)
            return [
                {"timestamp": observed, "side": "sell", "price": 10.0, "amount": 1.0}
                for _ in range(20)
            ]

    exchange = CountingExchange()
    result = asyncio.run(
        MicrostructureAnalyzer(snapshot_delay_seconds=0.25).analyze(
            exchange,
            "TEST/USDT:USDT",
            snapshots[-1],
            {"limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}}, "contractSize": 1.0},
            preloaded_snapshots=snapshots,
            preloaded_trades=[],
        )
    )

    assert exchange.book_calls == 0
    assert exchange.trade_calls == 1
    assert result.get("reason") != "missing live orderbook snapshots or trades"


def test_fresh_preloaded_trades_only_skip_rest_trades() -> None:
    now = time.time()
    trades = _fresh_preloaded_trades(now)

    class CountingExchange:
        def __init__(self) -> None:
            self.book_calls = 0
            self.trade_calls = 0

        async def fetch_order_book(self, symbol, limit):
            self.book_calls += 1
            return {
                "timestamp": int(time.time() * 1000),
                "bids": [[10.0, 100.0]],
                "asks": [[10.1, 100.0]],
            }

        async def fetch_trades(self, symbol, limit):
            self.trade_calls += 1
            raise AssertionError("fresh preloaded trades must be reused")

    exchange = CountingExchange()
    first = {
        "timestamp": int(now * 1000),
        "_received_at": now,
        "bids": [[10.0, 100.0]],
        "asks": [[10.1, 100.0]],
    }
    result = asyncio.run(
        MicrostructureAnalyzer(snapshot_delay_seconds=0.0).analyze(
            exchange,
            "TEST/USDT:USDT",
            first,
            {"limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}}, "contractSize": 1.0},
            preloaded_snapshots=[first],
            preloaded_trades=trades,
        )
    )

    assert exchange.book_calls == 2
    assert exchange.trade_calls == 0
    assert result.get("reason") != "missing live orderbook snapshots or trades"


def test_incomplete_preloaded_microstructure_evidence_uses_existing_rest_path() -> None:
    now = int(time.time() * 1000)

    class CountingExchange:
        def __init__(self) -> None:
            self.book_calls = 0
            self.trade_calls = 0

        async def fetch_order_book(self, symbol, limit):
            self.book_calls += 1
            return {
                "timestamp": int(time.time() * 1000),
                "bids": [[10.0, 100.0]],
                "asks": [[10.1, 100.0]],
            }
        async def fetch_trades(self, symbol, limit):
            self.trade_calls += 1
            observed = int(time.time() * 1000)
            return [
                {
                    "timestamp": observed,
                    "side": "sell",
                    "price": 10.0,
                    "amount": 1.0,
                }
                for _ in range(20)
            ]

    exchange = CountingExchange()
    first = {
        "timestamp": now,
        "bids": [[10.0, 100.0]],
        "asks": [[10.1, 100.0]],
    }
    result = asyncio.run(
        MicrostructureAnalyzer(snapshot_delay_seconds=0.0).analyze(
            exchange,
            "TEST/USDT:USDT",
            first,
            {
                "limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}},
                "contractSize": 1.0,
            },
            preloaded_snapshots=[first],
            preloaded_trades=[],
        )
    )

    assert exchange.book_calls == 2
    assert exchange.trade_calls == 1
    assert result["source_capture"]["orderbook_snapshots_captured"] is True


def test_future_dated_preloaded_orderbook_is_rejected_before_reuse() -> None:
    analyzer = MicrostructureAnalyzer(snapshot_delay_seconds=0.25)
    now = time.time()
    snapshots = [
        {
            "timestamp": int((now - 0.6 + index * 0.3) * 1000),
            "_received_at": now - 0.6 + index * 0.3,
            "bids": [[10.0, 100.0]],
            "asks": [[10.1, 100.0]],
        }
        for index in range(3)
    ]
    snapshots[-1]["timestamp"] = int((now + 2.0) * 1000)
    trades = [
        {"timestamp": int(now * 1000), "side": "sell", "price": 10.0, "amount": 1.0}
        for _ in range(20)
    ]

    assert analyzer._preloaded_evidence_is_usable(snapshots, trades, now=now) is False


def test_preloaded_orderbooks_expiring_during_trade_fetch_are_refreshed() -> None:
    analyzer = MicrostructureAnalyzer(snapshot_delay_seconds=0.0)
    analyzer.snapshot_ttl_seconds = 0.20
    now = time.time()
    snapshots = [
        {
            "timestamp": int((now - 0.02) * 1000),
            "_received_at": now - 0.02,
            "bids": [[10.0, 100.0]],
            "asks": [[10.1, 100.0]],
        }
        for _ in range(3)
    ]

    class SlowTradesExchange:
        def __init__(self) -> None:
            self.book_calls = 0

        async def fetch_order_book(self, symbol, limit):
            self.book_calls += 1
            observed = time.time()
            return {
                "timestamp": int(observed * 1000),
                "bids": [[10.0, 100.0]],
                "asks": [[10.1, 100.0]],
            }

        async def fetch_trades(self, symbol, limit):
            await asyncio.sleep(0.25)
            observed = int(time.time() * 1000)
            return [
                {"timestamp": observed, "side": "sell", "price": 10.0, "amount": 1.0}
                for _ in range(20)
            ]

    exchange = SlowTradesExchange()
    result = asyncio.run(
        analyzer.analyze(
            exchange,
            "TEST/USDT:USDT",
            snapshots[-1],
            {"limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}}, "contractSize": 1.0},
            preloaded_snapshots=snapshots,
            preloaded_trades=[],
        )
    )

    assert exchange.book_calls >= 3
    assert result.get("reason") != "stale orderbook snapshot"


def test_underfilled_rest_trade_batch_is_not_refetched_immediately() -> None:
    analyzer = MicrostructureAnalyzer(snapshot_delay_seconds=0.0)
    now = time.time()
    snapshots = [
        {
            "timestamp": int((now - 0.6 + index * 0.3) * 1000),
            "_received_at": now - 0.6 + index * 0.3,
            "bids": [[10.0, 100.0]],
            "asks": [[10.1, 100.0]],
        }
        for index in range(3)
    ]

    class Exchange:
        def __init__(self) -> None:
            self.trade_calls = 0

        async def fetch_trades(self, symbol, limit):
            self.trade_calls += 1
            observed = int(time.time() * 1000)
            return [
                {"timestamp": observed, "side": "sell", "price": 10.0, "amount": 1.0}
                for _ in range(5)
            ]

    exchange = Exchange()
    _, trades, reason = asyncio.run(
        analyzer._collect_evidence(
            exchange, "TEST/USDT:USDT", snapshots[-1], snapshots, []
        )
    )

    assert reason is None
    assert exchange.trade_calls == 1
    assert not analyzer._preloaded_trades_are_usable(trades, now=time.time())


def test_rest_trades_expiring_during_snapshot_collection_are_refreshed() -> None:
    analyzer = MicrostructureAnalyzer(snapshot_delay_seconds=0.12)
    analyzer.trade_ttl_seconds = 0.15
    analyzer.snapshot_ttl_seconds = 1.0
    now = time.time()

    class Exchange:
        def __init__(self) -> None:
            self.trade_calls = 0
            self.book_calls = 0

        async def fetch_order_book(self, symbol, limit):
            self.book_calls += 1
            observed = time.time()
            return {
                "timestamp": int(observed * 1000),
                "bids": [[10.0, 100.0]],
                "asks": [[10.1, 100.0]],
            }

        async def fetch_trades(self, symbol, limit):
            self.trade_calls += 1
            observed = int(time.time() * 1000)
            return [
                {"timestamp": observed, "side": "sell", "price": 10.0, "amount": 1.0}
                for _ in range(20)
            ]

    exchange = Exchange()
    first = {
        "timestamp": int(now * 1000),
        "_received_at": now,
        "bids": [[10.0, 100.0]],
        "asks": [[10.1, 100.0]],
    }
    _, trades, reason = asyncio.run(
        analyzer._collect_evidence(exchange, "TEST/USDT:USDT", first, [], [])
    )

    assert reason is None
    assert exchange.trade_calls == 2
    assert analyzer._preloaded_trades_are_usable(trades, now=time.time())


def test_rest_snapshots_expiring_during_slow_trade_fetch_are_refreshed() -> None:
    analyzer = MicrostructureAnalyzer(snapshot_delay_seconds=0.0)
    analyzer.snapshot_ttl_seconds = 0.15
    analyzer.trade_ttl_seconds = 1.0
    now = time.time()

    class Exchange:
        def __init__(self) -> None:
            self.trade_calls = 0
            self.book_calls = 0

        async def fetch_order_book(self, symbol, limit):
            self.book_calls += 1
            observed = time.time()
            return {
                "timestamp": int(observed * 1000),
                "bids": [[10.0, 100.0]],
                "asks": [[10.1, 100.0]],
            }

        async def fetch_trades(self, symbol, limit):
            self.trade_calls += 1
            await asyncio.sleep(0.20)
            observed = int(time.time() * 1000)
            return [
                {"timestamp": observed, "side": "sell", "price": 10.0, "amount": 1.0}
                for _ in range(20)
            ]

    exchange = Exchange()
    first = {
        "timestamp": int(now * 1000),
        "_received_at": now,
        "bids": [[10.0, 100.0]],
        "asks": [[10.1, 100.0]],
    }
    snapshots, _, reason = asyncio.run(
        analyzer._collect_evidence(exchange, "TEST/USDT:USDT", first, [], [])
    )

    assert reason is None
    assert exchange.book_calls == 5
    assert analyzer._preloaded_snapshots_are_usable(snapshots, now=time.time())


def test_preloaded_trades_expiring_during_snapshot_collection_are_refreshed() -> None:
    analyzer = MicrostructureAnalyzer(snapshot_delay_seconds=0.12)
    analyzer.trade_ttl_seconds = 0.15
    now = time.time()
    trades = [
        {"timestamp": int(now * 1000), "side": "sell", "price": 10.0, "amount": 1.0}
        for _ in range(20)
    ]

    class RefreshTradesExchange:
        def __init__(self) -> None:
            self.trade_calls = 0

        async def fetch_order_book(self, symbol, limit):
            observed = time.time()
            return {
                "timestamp": int(observed * 1000),
                "bids": [[10.0, 100.0]],
                "asks": [[10.1, 100.0]],
            }

        async def fetch_trades(self, symbol, limit):
            self.trade_calls += 1
            observed = int(time.time() * 1000)
            return [
                {"timestamp": observed, "side": "sell", "price": 10.0, "amount": 1.0}
                for _ in range(20)
            ]

    exchange = RefreshTradesExchange()
    first = {
        "timestamp": int(now * 1000),
        "_received_at": now,
        "bids": [[10.0, 100.0]],
        "asks": [[10.1, 100.0]],
    }
    result = asyncio.run(
        analyzer.analyze(
            exchange,
            "TEST/USDT:USDT",
            first,
            {"limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}}, "contractSize": 1.0},
            preloaded_snapshots=[],
            preloaded_trades=trades,
        )
    )

    assert exchange.trade_calls == 1
    assert result.get("reason") != "insufficient fresh trades"
