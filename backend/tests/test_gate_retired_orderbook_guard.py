from __future__ import annotations

import pytest

from waterfallhunter.core.ws_streamer import WebSocketManager


class _Client:
    def __init__(self, subscriptions: dict[str, object] | None = None) -> None:
        self.subscriptions = subscriptions or {}


class _Exchange:
    def __init__(self, missing_key: str) -> None:
        self.missing_key = missing_key
        self.calls = 0

    def handle_order_book(self, client: _Client, message: object) -> None:
        self.calls += 1
        raise KeyError(self.missing_key)


def _guard(exchange: _Exchange) -> _Exchange:
    guarded = WebSocketManager._guard_gate_retired_orderbook_callback(exchange)
    assert guarded is exchange
    return exchange


def test_gate_guard_drops_late_message_for_retired_orderbook_subscription() -> None:
    key = "orderbook:BULLA/USDT:USDT"
    exchange = _guard(_Exchange(key))

    assert exchange.handle_order_book(_Client(), {}) is None
    assert exchange.calls == 1


def test_gate_guard_does_not_mask_active_subscription_keyerror() -> None:
    key = "orderbook:BULLA/USDT:USDT"
    exchange = _guard(_Exchange(key))
    client = _Client({key: {"symbol": "BULLA/USDT:USDT"}})

    with pytest.raises(KeyError, match="orderbook:BULLA"):
        exchange.handle_order_book(client, {})


def test_gate_guard_does_not_mask_unrelated_keyerror() -> None:
    exchange = _guard(_Exchange("unexpected-field"))

    with pytest.raises(KeyError, match="unexpected-field"):
        exchange.handle_order_book(_Client(), {})


def test_new_gate_exchange_installs_retired_orderbook_guard() -> None:
    exchange = WebSocketManager._new_exchange("gateio")
    try:
        assert getattr(exchange, "_wfh_gate_retired_orderbook_guarded", False) is True
    finally:
        import asyncio
        asyncio.run(exchange.close())
