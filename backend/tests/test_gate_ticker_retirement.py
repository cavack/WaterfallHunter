from __future__ import annotations

import asyncio

from waterfallhunter.core.ws_streamer import WebSocketManager


class _GateExchange:
    def __init__(self) -> None:
        self.markets = {"loaded": True}
        self.calls: list[tuple] = []

    async def load_markets(self) -> None:
        self.calls.append(("load_markets",))

    def market(self, symbol: str) -> dict:
        return {
            "symbol": symbol,
            "id": "BTC_USDT",
            "spot": False,
            "option": False,
            "swap": True,
        }

    def get_type_by_market(self, market: dict) -> str:
        return "futures"

    def get_url_by_market(self, market: dict) -> str:
        return "wss://gate/futures"

    async def un_watch_ticker(self, symbol: str) -> None:
        raise AssertionError("Gate has no usable direct un_watch_ticker")

    async def un_subscribe_public_multiple(
        self,
        url: str,
        topic: str,
        symbols: list[str],
        message_hashes: list[str],
        sub_message_hashes: list[str],
        payload: list[str],
        channel: str,
        params: dict,
    ) -> bool:
        self.calls.append(
            (
                "unsubscribe_multiple",
                url,
                topic,
                symbols,
                message_hashes,
                sub_message_hashes,
                payload,
                channel,
                params,
            )
        )
        return True


def test_gate_ticker_retirement_uses_generic_public_unsubscribe() -> None:
    manager = WebSocketManager()
    exchange = _GateExchange()

    asyncio.run(
        manager._unwatch_direct_stream(
            "gateio", exchange, "BTC/USDT:USDT", "un_watch_ticker"
        )
    )

    assert exchange.calls == [
        (
            "unsubscribe_multiple",
            "wss://gate/futures",
            "ticker",
            ["BTC/USDT:USDT"],
            ["unsubscribe:ticker:BTC/USDT:USDT"],
            ["ticker:BTC/USDT:USDT"],
            ["BTC_USDT"],
            "futures.tickers",
            {},
        )
    ]
