import asyncio

from waterfallhunter.core.lbank_execution import LBankExecutionObserver
from waterfallhunter.core.multi_exchange_validator import MultiExchangeValidator


def _swap_market():
    return {
        "id": "TESTUSDT",
        "symbol": "TEST/USDT:USDT",
        "active": True,
        "linear": True,
        "swap": True,
        "settle": "USDT",
        "contractSize": 1.0,
        "precision": {"amount": 1.0, "price": 0.01},
        "limits": {
            "amount": {"min": 1.0},
            "cost": {"min": 5.0},
        },
        "info": {},
    }


def _orderbook():
    return {
        "bids": [[100.0, 10.0]],
        "asks": [[100.1, 10.0]],
    }


def test_validator_extracts_current_market_maximum_leverage():
    market = _swap_market()
    market["info"]["maxLeverage"] = "7"

    assert MultiExchangeValidator._market_maximum_leverage(market) == 7.0


def test_observe_accepts_ccxt_lbank_marked_price_field():
    class FakeExchange:
        def __init__(self):
            self.markets = {"TEST/USDT:USDT": _swap_market()}

        async def load_markets(self):
            return self.markets

        async def fetch_order_book(self, symbol, limit=50):
            return _orderbook()

        async def fetch_ticker(self, symbol):
            # CCXT LBank swap ticker preserves the exchange field in ticker.info.
            return {"info": {"markedPrice": "100.05"}}

        async def close(self):
            return None

    async def scenario():
        observer = LBankExecutionObserver()
        fake = FakeExchange()
        observer._create_exchange = lambda: fake

        result = await observer.observe("TEST/USDT:USDT")
        await observer.close()

        assert result["available"] is True
        assert result["mark_price"] == 100.05

    asyncio.run(scenario())


def test_exchange_bootstrap_uses_swap_only_market_loader_when_available():
    class FakeExchange:
        def __init__(self):
            self.markets = {}
            self.swap_fetch_count = 0
            self.load_markets_count = 0

        async def fetch_swap_markets(self):
            self.swap_fetch_count += 1
            return [_swap_market()]

        def set_markets(self, markets):
            self.markets = {market["symbol"]: market for market in markets}
            return self.markets

        async def load_markets(self):
            self.load_markets_count += 1
            raise AssertionError("swap-only observer must not load spot/currency markets")

        async def close(self):
            return None

    async def scenario():
        observer = LBankExecutionObserver()
        fake = FakeExchange()
        observer._create_exchange = lambda: fake

        exchange = await observer._ensure_exchange()
        await observer.close()

        assert exchange.markets["TEST/USDT:USDT"]["swap"] is True
        assert fake.swap_fetch_count == 1
        assert fake.load_markets_count == 0

    asyncio.run(scenario())
