from __future__ import annotations

import asyncio

import pytest

from waterfallhunter.core.ws_streamer import WebSocketManager


class _Client:
    def __init__(self) -> None:
        self.futures: dict[str, asyncio.Future] = {}


class _MexcExchange:
    def __init__(self) -> None:
        self.markets = {"loaded": True}
        self.urls = {"api": {"ws": {"swap": "wss://swap", "spot": "wss://spot"}}}
        self.calls: list[tuple] = []
        self._client = _Client()

    async def load_markets(self) -> None:
        self.calls.append(("load_markets",))

    def market(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "id": "BULLA_USDT",
            "spot": False,
            "swap": True,
        }

    async def watch_swap_public(
        self,
        channel: str,
        message_hash: str,
        request_params: dict,
        params: dict,
    ) -> bool:
        self.calls.append(("watch_swap_public", channel, message_hash, request_params, params))
        future = asyncio.get_running_loop().create_future()
        self._client.futures[message_hash] = future
        return await future

    def client(self, url: str) -> _Client:
        self.calls.append(("client", url))
        return self._client

    def handle_unsubscriptions(self, client: _Client, hashes: list[str]) -> None:
        self.calls.append(("cleanup", tuple(hashes)))
        for message_hash in hashes:
            assert message_hash in client.futures
            future = client.futures[message_hash]
            if not future.done():
                future.set_result(True)

    async def un_watch_order_book(self, symbol: str) -> None:
        raise AssertionError("broken CCXT MEXC un_watch_order_book must not be used")

    async def un_watch_ticker(self, symbol: str) -> None:
        raise AssertionError("broken CCXT MEXC un_watch_ticker must not be used")

    async def un_watch_trades(self, symbol: str) -> None:
        raise AssertionError("broken CCXT MEXC un_watch_trades must not be used")


@pytest.mark.parametrize(
    ("method_name", "channel", "message_hash"),
    [
        ("un_watch_order_book", "unsub.depth", "unsubscribe:orderbook:BULLA/USDT:USDT"),
        ("un_watch_ticker", "unsub.ticker", "unsubscribe:ticker:BULLA/USDT:USDT"),
        ("un_watch_trades", "unsub.deal", "unsubscribe:trades:BULLA/USDT:USDT"),
    ],
)
def test_mexc_swap_unwatch_registers_future_before_cleanup(
    method_name: str,
    channel: str,
    message_hash: str,
) -> None:
    manager = WebSocketManager()
    exchange = _MexcExchange()

    asyncio.run(
        manager._unwatch_direct_stream(
            "mexc", exchange, "BULLA/USDT:USDT", method_name
        )
    )

    watch_call = next(call for call in exchange.calls if call[0] == "watch_swap_public")
    cleanup_index = next(i for i, call in enumerate(exchange.calls) if call[0] == "cleanup")
    watch_index = next(i for i, call in enumerate(exchange.calls) if call[0] == "watch_swap_public")

    assert watch_index < cleanup_index
    assert watch_call[1] == channel
    assert watch_call[2] == message_hash
    assert watch_call[3] == {"symbol": "BULLA_USDT"}
    assert watch_call[4] == {}
    assert exchange._client.futures[message_hash].done() is True
    assert exchange._client.futures[message_hash].result() is True


class _OtherExchange:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def un_watch_order_book(self, symbol: str) -> None:
        self.calls.append(symbol)


def test_non_mexc_unwatch_delegates_to_exchange_method() -> None:
    manager = WebSocketManager()
    exchange = _OtherExchange()

    asyncio.run(
        manager._unwatch_direct_stream(
            "bybit", exchange, "BULLA/USDT:USDT", "un_watch_order_book"
        )
    )
    assert exchange.calls == ["BULLA/USDT:USDT"]
