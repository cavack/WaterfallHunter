import asyncio

from waterfallhunter.core.lbank_execution import (
    LBankExecutionObserver,
)


def market_packet():
    return {
        "active": True,
        "linear": True,
        "swap": True,
        "settle": "USDT",
        "contractSize": 1.0,
        "precision": {
            "amount": 1.0,
            "price": 0.01,
        },
        "limits": {
            "amount": {
                "min": 1.0,
            },
            "cost": {
                "min": 5.0,
            },
        },
        "info": {
            "markPrice": 100.5,
            "priceTick": 0.01,
            "volumeTick": 1.0,
            "maxLeverage": 20,
            "priceLimitLowerValue": 0.05,
            "priceLimitUpperValue": 0.05,
        },
    }


def orderbook_packet():
    return {
        "bids": [
            [100.0, 0.20],
            [99.9, 1.00],
            [99.0, 1.00],
        ],
        "asks": [
            [101.0, 0.20],
            [101.1, 1.00],
            [102.0, 1.00],
        ],
    }


def test_execution_observer_measures_spread_from_midpoint():
    result = (
        LBankExecutionObserver
        .measure_orderbook(
            "TEST/USDT:USDT",
            market_packet(),
            orderbook_packet(),
            (25.0,),
            observed_at=1000.0,
        )
    )

    assert result["available"] is True

    expected = (
        (101.0 - 100.0)
        / 100.5
        * 100.0
    )

    assert (
        result["spread_pct"]
        == round(expected, 6)
    )

    assert (
        result["spread_bps"]
        == round(
            expected * 100.0,
            4,
        )
    )


def test_raw_total_depth_is_diagnostic_only():
    result = (
        LBankExecutionObserver
        .measure_orderbook(
            "TEST/USDT:USDT",
            market_packet(),
            orderbook_packet(),
            (25.0,),
        )
    )

    raw = result[
        "depth"
    ][
        "raw_total_diagnostic"
    ]

    assert (
        raw["bid_depth_usdt"]
        == 218.9
    )

    assert (
        raw["ask_depth_usdt"]
        == 223.3
    )

    assert (
        "must not be used"
        in raw["warning"]
    )


def test_bounded_depth_excludes_far_away_levels():
    book = {
        "bids": [
            [100.0, 1.0],
            [99.95, 2.0],
            [50.0, 1_000_000.0],
        ],
        "asks": [
            [100.1, 1.0],
            [100.15, 2.0],
            [1_000_000.0, 1_000_000.0],
        ],
    }

    result = (
        LBankExecutionObserver
        .measure_orderbook(
            "TEST/USDT:USDT",
            market_packet(),
            book,
            (25.0,),
            depth_bands_bps=(
                10,
                100,
            ),
        )
    )

    raw = result[
        "depth"
    ][
        "raw_total_diagnostic"
    ]

    bounded = result[
        "depth"
    ][
        "bounded"
    ]

    assert (
        raw["ask_depth_usdt"]
        > 1_000_000_000
    )

    assert (
        bounded["100"][
            "ask"
        ][
            "depth_usdt"
        ]
        < 1_000
    )

    assert (
        bounded["100"][
            "bid"
        ][
            "depth_usdt"
        ]
        < 1_000
    )


def test_bounded_depth_is_anchored_to_best_bid_and_ask():
    book = {
        "bids": [
            [98.0, 2.0],
            [97.95, 3.0],
        ],
        "asks": [
            [102.0, 4.0],
            [102.05, 5.0],
        ],
    }

    result = (
        LBankExecutionObserver
        .measure_orderbook(
            "WIDE/USDT:USDT",
            market_packet(),
            book,
            (25.0,),
            depth_bands_bps=(10,),
        )
    )

    packet = result[
        "depth"
    ][
        "bounded"
    ][
        "10"
    ]

    assert (
        result["spread_pct"]
        == 4.0
    )

    assert (
        packet["anchor"]
        == "best_bid_best_ask"
    )

    assert (
        packet[
            "bid"
        ][
            "depth_usdt"
        ]
        > 0
    )

    assert (
        packet[
            "ask"
        ][
            "depth_usdt"
        ]
        > 0
    )


def test_bounded_depth_reports_minimum_of_both_sides():
    book = {
        "bids": [
            [100.0, 10.0],
        ],
        "asks": [
            [101.0, 2.0],
        ],
    }

    result = (
        LBankExecutionObserver
        .measure_orderbook(
            "TEST/USDT:USDT",
            market_packet(),
            book,
            (25.0,),
            depth_bands_bps=(100,),
        )
    )

    packet = result[
        "depth"
    ][
        "bounded"
    ][
        "100"
    ]

    assert (
        packet[
            "bid"
        ][
            "depth_usdt"
        ]
        == 1000.0
    )

    assert (
        packet[
            "ask"
        ][
            "depth_usdt"
        ]
        == 202.0
    )

    assert (
        packet[
            "minimum_side_depth_usdt"
        ]
        == 202.0
    )


def test_execution_observer_calculates_real_sell_vwap():
    result = (
        LBankExecutionObserver
        .measure_orderbook(
            "TEST/USDT:USDT",
            market_packet(),
            orderbook_packet(),
            (25.0,),
        )
    )

    packet = result[
        "execution"
    ]["25"]

    expected_quantity = (
        20.0 / 100.0
        + 5.0 / 99.9
    )

    expected_vwap = (
        25.0
        / expected_quantity
    )

    assert (
        packet["sell"]["vwap"]
        == round(
            expected_vwap,
            12,
        )
    )

    assert (
        packet["sell"]["complete"]
        is True
    )


def test_execution_observer_calculates_real_buy_vwap():
    result = (
        LBankExecutionObserver
        .measure_orderbook(
            "TEST/USDT:USDT",
            market_packet(),
            orderbook_packet(),
            (25.0,),
        )
    )

    packet = result[
        "execution"
    ]["25"]

    expected_quantity = (
        20.2 / 101.0
        + 4.8 / 101.1
    )

    expected_vwap = (
        25.0
        / expected_quantity
    )

    assert (
        packet["buy"]["vwap"]
        == round(
            expected_vwap,
            12,
        )
    )

    assert (
        packet["buy"]["complete"]
        is True
    )


def test_effective_crossing_cost_includes_spread_and_impact():
    book = {
        "bids": [
            [100.0, 0.5],
            [99.0, 10.0],
        ],
        "asks": [
            [101.0, 0.5],
            [102.0, 10.0],
        ],
    }

    result = (
        LBankExecutionObserver
        .measure_orderbook(
            "TEST/USDT:USDT",
            market_packet(),
            book,
            (100.0,),
        )
    )

    packet = result[
        "execution"
    ]["100"]

    assert (
        packet[
            "round_trip_slippage_pct"
        ]
        is not None
    )

    expected = (
        result["spread_pct"]
        + packet[
            "round_trip_slippage_pct"
        ]
    )

    assert (
        packet[
            "effective_crossing_cost_pct"
        ]
        == round(
            expected,
            6,
        )
    )


def test_execution_observer_reports_incomplete_depth_honestly():
    result = (
        LBankExecutionObserver
        .measure_orderbook(
            "TEST/USDT:USDT",
            market_packet(),
            {
                "bids": [
                    [100.0, 0.1],
                ],
                "asks": [
                    [101.0, 0.1],
                ],
            },
            (50.0,),
        )
    )

    packet = result[
        "execution"
    ]["50"]

    assert (
        packet["sell"]["complete"]
        is False
    )

    assert (
        packet["buy"]["complete"]
        is False
    )

    assert (
        packet[
            "entry_slippage_pct"
        ]
        is None
    )

    assert (
        packet[
            "exit_slippage_pct"
        ]
        is None
    )

    assert (
        packet[
            "effective_crossing_cost_pct"
        ]
        is None
    )


def test_execution_observer_exposes_market_constraints():
    result = (
        LBankExecutionObserver
        .measure_orderbook(
            "TEST/USDT:USDT",
            market_packet(),
            orderbook_packet(),
            (25.0,),
        )
    )

    filters = result[
        "market_filters"
    ]

    assert (
        filters[
            "contract_size"
        ]
        == 1.0
    )

    assert (
        filters[
            "minimum_amount"
        ]
        == 1.0
    )

    assert (
        filters[
            "explicit_min_cost"
        ]
        == 5.0
    )

    assert (
        filters[
            "effective_min_notional"
        ]
        == 100.5
    )
    assert filters["price_tick"] == 0.01
    assert filters["quantity_step"] == 1.0
    assert filters["maximum_leverage"] == 20.0
    assert filters["price_limit_lower_rate"] == 0.05
    assert filters["price_limit_upper_rate"] == 0.05


def test_market_constraints_parse_numeric_strings_and_use_mark_price():
    market = market_packet()
    market["info"].update(
        {
            "markPrice": "110.0",
            "priceTick": "0.01",
            "volumeTick": "1.0",
            "maxLeverage": "20",
            "priceLimitLowerValue": "0.05",
            "priceLimitUpperValue": "0.05",
        }
    )
    result = LBankExecutionObserver.measure_orderbook(
        "TEST/USDT:USDT",
        market,
        orderbook_packet(),
        (25.0,),
    )

    assert result["available"] is True
    assert result["mark_price"] == 110.0
    assert result["market_filters"]["maximum_leverage"] == 20.0
    assert result["market_filters"]["effective_min_notional"] == 110.0


def test_execution_observer_fails_closed_without_mark_price():
    market = market_packet()
    market["info"].pop("markPrice")
    result = LBankExecutionObserver.measure_orderbook(
        "TEST/USDT:USDT",
        market,
        orderbook_packet(),
        (25.0,),
    )

    assert result["available"] is False
    assert result["reason"] == "LBank mark price unavailable"


def test_execution_observer_rejects_non_linear_market():
    market = market_packet()
    market["linear"] = False

    result = (
        LBankExecutionObserver
        .measure_orderbook(
            "TEST/USDT:USDT",
            market,
            orderbook_packet(),
            (25.0,),
        )
    )

    assert (
        result["available"]
        is False
    )


def test_default_notional_profile_is_25_50_100():
    observer = (
        LBankExecutionObserver()
    )

    assert (
        observer.notionals
        == (
            25.0,
            50.0,
            100.0,
        )
    )


def test_default_depth_bands_are_10_25_50_100_bps():
    observer = (
        LBankExecutionObserver()
    )

    assert (
        observer.depth_bands_bps
        == (
            10,
            25,
            50,
            100,
        )
    )


def test_observe_reuses_single_exchange_and_loads_markets_once():
    class FakeExchange:
        def __init__(self):
            self.load_count = 0
            self.close_count = 0
            self.orderbook_count = 0

            self.markets = {
                "A/USDT:USDT": market_packet(),
                "B/USDT:USDT": market_packet(),
            }

        async def load_markets(self):
            self.load_count += 1
            return self.markets

        async def fetch_order_book(
            self,
            symbol,
            limit=50,
        ):
            self.orderbook_count += 1
            return orderbook_packet()

        async def fetch_ticker(self, symbol):
            return {"mark": 100.5}

        async def close(self):
            self.close_count += 1

    async def scenario():
        observer = (
            LBankExecutionObserver()
        )

        fake = FakeExchange()

        observer._create_exchange = (
            lambda: fake
        )

        first = await observer.observe(
            "A/USDT:USDT"
        )

        second = await observer.observe(
            "B/USDT:USDT"
        )

        assert first["available"] is True
        assert second["available"] is True
        assert fake.load_count == 1
        assert fake.orderbook_count == 2

        await observer.close()

        assert fake.close_count == 1

    asyncio.run(
        scenario()
    )


def test_observe_many_closes_shared_exchange_after_batch():
    class FakeExchange:
        def __init__(self):
            self.load_count = 0
            self.close_count = 0

            self.markets = {
                "A/USDT:USDT": market_packet(),
                "B/USDT:USDT": market_packet(),
            }

        async def load_markets(self):
            self.load_count += 1
            return self.markets

        async def fetch_order_book(
            self,
            symbol,
            limit=50,
        ):
            return orderbook_packet()

        async def fetch_ticker(self, symbol):
            return {"mark": 100.5}

        async def close(self):
            self.close_count += 1

    async def scenario():
        observer = (
            LBankExecutionObserver()
        )

        fake = FakeExchange()

        observer._create_exchange = (
            lambda: fake
        )

        results = (
            await observer.observe_many(
                (
                    "A/USDT:USDT",
                    "B/USDT:USDT",
                )
            )
        )

        assert (
            results[
                "A/USDT:USDT"
            ]["available"]
            is True
        )

        assert (
            results[
                "B/USDT:USDT"
            ]["available"]
            is True
        )

        assert fake.load_count == 1
        assert fake.close_count == 1

    asyncio.run(
        scenario()
    )
