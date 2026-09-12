import asyncio
import time

import waterfallhunter.core.multi_exchange as multi_exchange_module
from waterfallhunter.core.derivatives import DerivativesAnalyzer
from waterfallhunter.core.multi_exchange import (
    MultiExchangeGateway,
    is_usdt_linear_perpetual,
)
from waterfallhunter.core.multi_exchange_validator import (
    MultiExchangeValidator,
)


class _Exchange:
    def __init__(self, price):
        self.markets = {
            "TEST/USDT:USDT": {
                "active": True,
                "linear": True,
                "settle": "USDT",
                "swap": True,
            },
        }
        self.price = price

    async def fetch_ticker(self, symbol):
        return {
            "last": self.price,
        }


def test_binance_configuration_loads_only_linear_markets():
    assert (
        MultiExchangeGateway._exchange_options(
            "binance"
        )
        == {
            "defaultType": "swap",
            "fetchMarkets": {
                "types": [
                    "linear",
                ]
            },
        }
    )


def test_reliable_secondary_perpetual_venues_follow_the_primary_waterfall():
    assert (
        MultiExchangeGateway().priority_chain
        == [
            "binance",
            "bybit",
            "kucoin",
            "okx",
            "mexc",
            "bingx",
            "gateio",
            "bitget",
            "htx",
        ]
    )


def test_gateio_internal_id_maps_only_at_ccxt_boundary():
    assert (
        MultiExchangeGateway._ccxt_exchange_id(
            "gateio"
        )
        == "gate"
    )

    assert (
        MultiExchangeGateway._ccxt_exchange_id(
            "binance"
        )
        == "binance"
    )


def test_gateio_internal_id_constructs_ccxt_gate_client(
    monkeypatch,
):
    created = []

    class FakeGate:
        def __init__(
            self,
            config,
        ):
            self.config = config
            self.markets = {}
            self.load_markets_calls = 0

            created.append(
                self
            )

        async def load_markets(
            self,
        ):
            self.load_markets_calls += 1
            return self.markets

        async def close(
            self,
        ):
            return None

    monkeypatch.setattr(
        multi_exchange_module.ccxt,
        "gate",
        FakeGate,
    )

    gateway = MultiExchangeGateway()

    exchange = asyncio.run(
        gateway._get_exchange(
            "gateio"
        )
    )

    assert len(created) == 1
    assert exchange is created[0]

    assert (
        gateway._exchanges[
            "gateio"
        ]
        is exchange
    )

    assert (
        gateway._markets_loaded[
            "gateio"
        ]
        is True
    )

    assert (
        exchange.load_markets_calls
        == 1
    )

    assert (
        exchange.config[
            "options"
        ]
        == {
            "defaultType": "swap",
        }
    )

    assert (
        "gate"
        not in gateway._exchanges
    )


def test_bybit_configuration_loads_only_linear_markets():
    assert (
        MultiExchangeGateway._exchange_options(
            "bybit"
        )
        == {
            "defaultType": "swap",
            "fetchMarkets": {
                "types": [
                    "linear",
                ]
            },
        }
    )


def test_unrestricted_exchange_configuration_remains_swap_only():
    assert (
        MultiExchangeGateway._exchange_options(
            "kucoin"
        )
        == {
            "defaultType": "swap",
        }
    )


def test_market_mapping_requires_an_active_usdt_linear_perpetual():
    eligible = {
        "active": True,
        "linear": True,
        "settle": "USDT",
        "swap": True,
    }

    assert is_usdt_linear_perpetual(
        eligible
    )

    assert not is_usdt_linear_perpetual(
        {
            **eligible,
            "swap": False,
        }
    )

    assert not is_usdt_linear_perpetual(
        {
            **eligible,
            "settle": "USDC",
        }
    )


def test_exact_source_ohlcv_never_falls_back_to_another_exchange():
    gateway = MultiExchangeGateway()
    gateway.priority_chain = [
        "binance",
        "bybit",
    ]
    gateway._markets_loaded = {
        "binance": True,
        "bybit": True,
    }
    calls = []

    class ExactExchange:
        markets = {
            "TEST/USDT:USDT": {
                "active": True,
                "linear": True,
                "settle": "USDT",
                "swap": True,
            }
        }

        async def fetch_ohlcv(
            self,
            symbol,
            timeframe,
            since,
            limit,
        ):
            calls.append(
                (symbol, timeframe, since, limit)
            )
            return [[since, 1, 1, 1, 1, 1]]

    async def get_exchange(name):
        assert name == "bybit"
        return ExactExchange()

    gateway._get_exchange = get_exchange

    result = asyncio.run(
        gateway.fetch_ohlcv_from_source(
            "bybit",
            "TEST/USDT:USDT",
            timeframe="1m",
            since=1_700_000_000_000,
            limit=500,
        )
    )

    assert result["exchange"] == "bybit"
    assert calls == [
        (
            "TEST/USDT:USDT",
            "1m",
            1_700_000_000_000,
            500,
        )
    ]
    assert asyncio.run(
        gateway.fetch_ohlcv_from_source(
            "unknown",
            "TEST/USDT:USDT",
        )
    ) == {}

def test_compatible_market_sources_uses_next_exchange_after_price_incompatibility():
    gateway = MultiExchangeGateway()

    gateway.priority_chain = [
        "binance",
        "bybit",
    ]

    gateway._markets_loaded = {
        "binance": True,
        "bybit": True,
    }

    exchanges = {
        "binance": _Exchange(
            1.20
        ),
        "bybit": _Exchange(
            1.01
        ),
    }

    async def get_exchange(
        name,
    ):
        return exchanges[
            name
        ]

    gateway._get_exchange = (
        get_exchange
    )

    async def collect():
        return [
            source
            async for source
            in gateway
            .compatible_market_sources(
                "TEST/USDT:USDT",
                1.0,
                5.0,
            )
        ]

    sources = asyncio.run(
        collect()
    )

    assert [
        source["exchange"]
        for source in sources
    ] == [
        "bybit",
    ]


class _DerivativesExchange:
    id = "binance"

    def __init__(
        self,
        price=1.0,
    ):
        self.markets = {
            "1000PEPE/USDT:USDT": {
                "id": "1000PEPEUSDT",
                "active": True,
                "linear": True,
                "settle": "USDT",
                "swap": True,
            },
        }

        self.price = price
        self.raw_requests = []

    async def fetch_ticker(
        self,
        symbol,
    ):
        assert (
            symbol
            == "1000PEPE/USDT:USDT"
        )

        return {
            "last": self.price,
        }

    async def fapiPublicGetFundingRate(
        self,
        params,
    ):
        self.raw_requests.append(
            (
                "funding",
                params,
            )
        )

        assert (
            params
            == {
                "symbol": "1000PEPEUSDT",
                "limit": 90,
            }
        )

        now = int(
            time.time()
            * 1000
        )

        return [
            {
                "symbol": "1000PEPEUSDT",
                "fundingRate": "0.00005",
                "fundingTime": (
                    now
                    - 16
                    * 60
                    * 60
                    * 1000
                ),
            },
            {
                "symbol": "1000PEPEUSDT",
                "fundingRate": "0.00010",
                "fundingTime": (
                    now
                    - 8
                    * 60
                    * 60
                    * 1000
                ),
            },
            {
                "symbol": "1000PEPEUSDT",
                "fundingRate": "0.00020",
                "fundingTime": now,
            },
        ]

    async def fapiDataGetTakerlongshortRatio(
        self,
        params,
    ):
        self.raw_requests.append(
            (
                "taker",
                params,
            )
        )

        assert (
            params
            == {
                "symbol": "1000PEPEUSDT",
                "period": "5m",
                "limit": 13,
            }
        )

        now = int(
            time.time()
            * 1000
        )

        return [
            {
                "symbol": "1000PEPEUSDT",
                "buySellRatio": str(
                    round(
                        1.2
                        - offset
                        / 30,
                        4,
                    )
                ),
                "buyVol": "80",
                "sellVol": "100",
                "timestamp": (
                    now
                    - (
                        12
                        - offset
                    )
                    * 300_000
                ),
            }
            for offset in range(
                13
            )
        ]

    async def fapiDataGetTopLongShortAccountRatio(
        self,
        params,
    ):
        self.raw_requests.append(
            (
                "top",
                params,
            )
        )

        assert (
            params
            == {
                "symbol": "1000PEPEUSDT",
                "period": "5m",
                "limit": 1,
            }
        )

        return [
            {
                "longShortRatio": "1.3",
                "longAccount": "0.5652",
                "shortAccount": "0.4348",
                "timestamp": int(
                    time.time()
                    * 1000
                ),
            }
        ]

    async def fapiDataGetOpenInterestHist(
        self,
        params,
    ):
        self.raw_requests.append(
            (
                "oi",
                params,
            )
        )

        assert (
            params
            == {
                "symbol": "1000PEPEUSDT",
                "period": "5m",
                "limit": 13,
            }
        )

        now = int(
            time.time()
            * 1000
        )

        return [
            {
                "symbol": "1000PEPEUSDT",
                "sumOpenInterest": str(
                    1_020_000
                    - offset
                    * 1_666
                ),
                "sumOpenInterestValue": str(
                    1_020_000
                    - offset
                    * 1_666
                ),
                "timestamp": (
                    now
                    - (
                        12
                        - offset
                    )
                    * 300_000
                ),
            }
            for offset in range(
                13
            )
        ]


def test_derivatives_gateway_keeps_canonical_mapped_contract_and_live_fields():
    gateway = (
        MultiExchangeGateway()
    )

    exchange = (
        _DerivativesExchange()
    )

    result = asyncio.run(
        gateway
        .fetch_derivatives_context(
            exchange,
            "1000PEPE/USDT:USDT",
            DerivativesAnalyzer(),
        )
    )

    assert (
        result["available"]
        is True
    )

    assert (
        result[
            "mapped_symbol"
        ]
        == "1000PEPE/USDT:USDT"
    )

    assert (
        result[
            "market_id"
        ]
        == "1000PEPEUSDT"
    )

    assert (
        result[
            "source_exchange"
        ]
        == "binance"
    )

    assert (
        result[
            "funding_rate"
        ]
        == 0.0002
    )

    assert (
        result[
            "funding_percentile"
        ]
        == 1.0
    )

    assert (
        result[
            "taker_buy_sell_ratio"
        ]
        == 0.8
    )

    assert (
        result[
            "top_trader_long_short_ratio"
        ]
        == 1.3
    )

    assert result["source_capture"]["provider"] == "binance"
    assert len(result["source_capture"]["funding_rows"]) == 3
    assert len(result["source_capture"]["taker_rows"]) == 13
    assert len(result["source_capture"]["open_interest_rows"]) == 13

    assert [
        name
        for name, _
        in exchange.raw_requests
    ] == [
        "funding",
        "taker",
        "top",
        "oi",
    ]


class _StaleTakerExchange(
    _DerivativesExchange
):
    async def fapiDataGetTakerlongshortRatio(
        self,
        params,
    ):
        rows = (
            await super()
            .fapiDataGetTakerlongshortRatio(
                params
            )
        )

        for row in rows:
            row[
                "timestamp"
            ] -= (
                60
                * 60
                * 1000
            )

        return rows


def test_derivatives_gateway_rejects_stale_raw_rows_with_provenance():
    result = asyncio.run(
        MultiExchangeGateway()
        .fetch_derivatives_context(
            _StaleTakerExchange(),
            "1000PEPE/USDT:USDT",
            DerivativesAnalyzer(),
        )
    )

    assert (
        result["available"]
        is False
    )

    assert (
        result["reason"]
        == (
            "missing valid taker "
            "buy/sell ratio"
        )
    )

    assert (
        result[
            "source_exchange"
        ]
        == "binance"
    )

    assert (
        result[
            "mapped_symbol"
        ]
        == "1000PEPE/USDT:USDT"
    )

    assert (
        result[
            "market_id"
        ]
        == "1000PEPEUSDT"
    )

    assert (
        result[
            "fallback_attempts"
        ]
        == []
    )

    assert result["source_capture"]["provider"] == "binance"
    assert len(result["source_capture"]["taker_rows"]) == 13


class _UnsupportedDerivativesExchange(
    _DerivativesExchange
):
    id = "bybit"

    async def fapiPublicGetFundingRate(
        self,
        params,
    ):
        raise AssertionError(
            "unsupported venue must "
            "not be queried as Binance"
        )


def test_derivatives_gateway_does_not_claim_an_unsupported_venue_is_complete():
    result = asyncio.run(
        MultiExchangeGateway()
        .fetch_derivatives_context(
            _UnsupportedDerivativesExchange(),
            "1000PEPE/USDT:USDT",
            DerivativesAnalyzer(),
        )
    )

    assert (
        result["available"]
        is False
    )

    assert (
        result["reason"]
        == (
            "unsupported derivatives "
            "source: bybit"
        )
    )

    assert (
        result[
            "source_exchange"
        ]
        == "bybit"
    )

    assert (
        result[
            "mapped_symbol"
        ]
        == "1000PEPE/USDT:USDT"
    )

    assert (
        result[
            "market_id"
        ]
        == "1000PEPEUSDT"
    )

    assert (
        result[
            "fallback_attempts"
        ]
        == []
    )


def _validator_with_derivatives_exchanges(
    exchanges,
):
    gateway = (
        MultiExchangeGateway()
    )

    gateway.priority_chain = list(
        exchanges
    )

    gateway._markets_loaded = {
        name: True
        for name in exchanges
    }

    async def get_exchange(
        name,
    ):
        return exchanges[
            name
        ]

    gateway._get_exchange = (
        get_exchange
    )

    instance = object.__new__(
        MultiExchangeValidator
    )

    instance.gateway = gateway
    instance.derivatives = (
        DerivativesAnalyzer()
    )

    instance.max_cross_exchange_deviation_pct = (
        5.0
    )

    return instance


def test_derivatives_context_prefers_compatible_binance_over_selected_bybit():
    binance = (
        _DerivativesExchange()
    )

    bybit = (
        _UnsupportedDerivativesExchange()
    )

    validator = (
        _validator_with_derivatives_exchanges(
            {
                "binance": binance,
                "bybit": bybit,
            }
        )
    )

    result = asyncio.run(
        validator._derivatives_context(
            "PEPE/USDT:USDT",
            1.0,
            "bybit",
            "1000PEPE/USDT:USDT",
            bybit,
        )
    )

    assert (
        result["available"]
        is True
    )

    assert (
        result[
            "source_exchange"
        ]
        == "binance"
    )

    assert (
        result[
            "market_id"
        ]
        == "1000PEPEUSDT"
    )

    assert (
        result[
            "fallback_attempts"
        ]
        == []
    )


def test_incomplete_derivatives_waterfall_preserves_attempt_provenance_in_priority_order():
    binance = (
        _StaleTakerExchange()
    )

    bybit = (
        _UnsupportedDerivativesExchange()
    )

    validator = (
        _validator_with_derivatives_exchanges(
            {
                "binance": binance,
                "bybit": bybit,
            }
        )
    )

    result = asyncio.run(
        validator._derivatives_context(
            "PEPE/USDT:USDT",
            1.0,
            "bybit",
            "1000PEPE/USDT:USDT",
            bybit,
        )
    )

    assert (
        result["available"]
        is False
    )

    assert (
        result["reason"]
        == (
            "no complete live derivatives "
            "data source in exchange waterfall"
        )
    )

    assert (
        result[
            "source_exchange"
        ]
        is None
    )

    assert (
        result[
            "mapped_symbol"
        ]
        is None
    )

    assert (
        result[
            "market_id"
        ]
        is None
    )

    assert [
        attempt[
            "exchange"
        ]
        for attempt
        in result[
            "fallback_attempts"
        ]
    ] == [
        "binance",
        "bybit",
    ]

    assert (
        result[
            "fallback_attempts"
        ][0][
            "reason"
        ]
        == (
            "missing valid taker "
            "buy/sell ratio"
        )
    )

    assert (
        result[
            "fallback_attempts"
        ][0][
            "market_id"
        ]
        == "1000PEPEUSDT"
    )

    assert (
        result[
            "fallback_attempts"
        ][1][
            "reason"
        ]
        == (
            "unsupported derivatives "
            "source: bybit"
        )
    )


def test_derivatives_context_never_queries_price_incompatible_binance_packet():
    binance = (
        _DerivativesExchange(
            price=1.2
        )
    )

    bybit = (
        _UnsupportedDerivativesExchange()
    )

    validator = (
        _validator_with_derivatives_exchanges(
            {
                "binance": binance,
                "bybit": bybit,
            }
        )
    )

    result = asyncio.run(
        validator._derivatives_context(
            "PEPE/USDT:USDT",
            1.0,
            "bybit",
            "1000PEPE/USDT:USDT",
            bybit,
        )
    )

    assert (
        result["available"]
        is False
    )

    assert (
        result[
            "fallback_attempts"
        ][0]
        == {
            "exchange": "binance",
            "mapped_symbol": (
                "1000PEPE/USDT:USDT"
            ),
            "market_id": (
                "1000PEPEUSDT"
            ),
            "retrieved_at": None,
            "reason": (
                "price incompatible "
                "with reference"
            ),
        }
    )

    assert (
        result[
            "fallback_attempts"
        ][1][
            "exchange"
        ]
        == "bybit"
    )

    assert (
        binance.raw_requests
        == []
    )


class _CrossCheckExchange:
    id = "bybit"

    markets = {
        "TEST/USDT:USDT": {
            "id": "TESTUSDT",
            "active": True,
            "linear": True,
            "settle": "USDT",
            "swap": True,
        },
    }


class _CrossCheckGateway:
    def __init__(
        self,
    ):
        self.exchange = (
            _CrossCheckExchange()
        )

    async def compatible_market_sources(
        self,
        symbol,
        reference_price,
        max_deviation_pct,
        **kwargs,
    ):
        yield {
            "exchange": "bybit",
            "mapped_symbol": (
                "TEST/USDT:USDT"
            ),
            "data": {
                "last": 1.0,
                "vwap": 1.0,
                "quoteVolume": (
                    12_000_000.0
                ),
            },
            "exchange_instance": (
                self.exchange
            ),
        }

    async def get_confirmation_exchange(
        self,
        symbol,
        exclude_name,
        reference_price,
        max_deviation_pct,
    ):
        return (
            type(
                "ConfirmationExchange",
                (),
                {
                    "id": "okx",
                },
            )(),
            "TEST/USDT:USDT",
        )


class _CrossCheckWebSockets:
    @staticmethod
    def get_realtime_orderbook(
        exchange_name,
        mapped_symbol,
    ):
        return {
            "bids": [
                [
                    0.99,
                    100,
                ]
            ],
            "asks": [
                [
                    1.01,
                    100,
                ]
            ],
        }


class _CrossCheckCandles:
    timeframes = (
        "5m",
        "15m",
        "1h",
        "4h",
    )

    async def analyze_candles(
        self,
        exchange,
        mapped_symbol,
        confirmation_exchange,
        confirmation_symbol,
    ):
        return {
            "details": {
                timeframe: {
                    "valid": True,
                    "is_bearish": True,
                }
                for timeframe
                in self.timeframes
            },
            "cross_exchange_confirmed": True,
            "is_breakdown_confirmed": True,
        }

    @staticmethod
    def channel_stages(
        details,
    ):
        return {
            "passed": True,
        }


class _CrossCheckMicrostructure:
    async def analyze(
        self,
        exchange,
        mapped_symbol,
        orderbook,
        market_info,
        **kwargs,
    ):
        return {
            "approved": True,
            "sell_flow_usdt": 60.0,
            "buy_flow_usdt": 40.0,
            "spread_pct": 0.05,
            "slippage_pct": 0.05,
            "spoofing_detected": False,
            "footprint": {
                "available": True,
                "aggressive_selling": True,
            },
        }


def test_validator_does_not_score_or_transition_an_incomplete_derivatives_packet():
    instance = object.__new__(
        MultiExchangeValidator
    )

    instance.gateway = (
        _CrossCheckGateway()
    )

    instance.ws_manager = (
        _CrossCheckWebSockets()
    )

    instance.candle_analyzer = (
        _CrossCheckCandles()
    )

    instance.microstructure = (
        _CrossCheckMicrostructure()
    )

    instance.max_cross_exchange_deviation_pct = (
        5.0
    )

    async def incomplete_derivatives(
        *args,
    ):
        return {
            "available": False,
            "reason": (
                "missing valid taker "
                "buy/sell ratio"
            ),
            "source_exchange": None,
            "mapped_symbol": None,
            "market_id": None,
            "retrieved_at": None,
            "fallback_attempts": [
                {
                    "exchange": (
                        "binance"
                    ),
                    "mapped_symbol": (
                        "1000TEST/USDT:USDT"
                    ),
                    "market_id": (
                        "1000TESTUSDT"
                    ),
                    "retrieved_at": (
                        1_700_000_000.0
                    ),
                    "reason": (
                        "missing valid taker "
                        "buy/sell ratio"
                    ),
                },
            ],
        }

    instance._derivatives_context = (
        incomplete_derivatives
    )

    result = asyncio.run(
        instance.cross_check_symbol(
            "TEST/USDT:USDT",
            1.0,
        )
    )

    assert (
        result["is_valid"]
        is False
    )

    assert (
        result["score"]
        is None
    )

    assert (
        result[
            "suggested_status"
        ]
        == "REJECTED"
    )

    assert (
        result[
            "metrics"
        ][
            "total_score"
        ]
        is None
    )

    assert (
        result[
            "metrics"
        ][
            "score_components"
        ]
        == {}
    )

    assert (
        result[
            "metrics"
        ][
            "error"
        ]
        == (
            "missing valid taker "
            "buy/sell ratio"
        )
    )

    assert (
        result[
            "metrics"
        ][
            "selected_quote_volume_usdt"
        ]
        == 12_000_000.0
    )

    assert (
        result[
            "metrics"
        ][
            "derivatives"
        ][
            "fallback_attempts"
        ][0][
            "market_id"
        ]
        == "1000TESTUSDT"
    )



def _collect_sources_with_realtime_ticker(realtime_ticker):
    gateway = MultiExchangeGateway()
    gateway.priority_chain = ["binance"]
    gateway._markets_loaded = {"binance": True}

    class CountingExchange(_Exchange):
        def __init__(self) -> None:
            super().__init__(1.01)
            self.ticker_calls = 0

        async def fetch_ticker(self, symbol):
            self.ticker_calls += 1
            return await super().fetch_ticker(symbol)

    exchange = CountingExchange()

    async def get_exchange(name):
        assert name == "binance"
        return exchange

    gateway._get_exchange = get_exchange

    async def collect():
        return [
            source
            async for source in gateway.compatible_market_sources(
                "TEST/USDT:USDT",
                1.0,
                5.0,
                realtime_ticker_getter=lambda ex, symbol: realtime_ticker,
            )
        ]

    return asyncio.run(collect()), exchange.ticker_calls


def test_compatible_market_sources_prefers_fresh_realtime_ticker_over_rest() -> None:
    sources, ticker_calls = _collect_sources_with_realtime_ticker({"last": 1.01})
    assert [source["exchange"] for source in sources] == ["binance"]
    assert ticker_calls == 0


def test_compatible_market_sources_falls_back_to_rest_when_realtime_ticker_missing() -> None:
    sources, ticker_calls = _collect_sources_with_realtime_ticker(None)
    assert [source["exchange"] for source in sources] == ["binance"]
    assert ticker_calls == 1
