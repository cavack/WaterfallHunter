import asyncio
import time

from waterfallhunter.core.candle_analyzer import MultiTimeframeAnalyzer
from waterfallhunter.core.derivatives import DerivativesAnalyzer
from waterfallhunter.core.microstructure import MicrostructureAnalyzer


def test_derivatives_emit_observational_leading_features_without_gating():
    retrieved_at = 1_700_000_000.0
    market_id = "TESTUSDT"

    def ms(seconds_ago: int) -> int:
        return int((retrieved_at - seconds_ago) * 1000)

    result = DerivativesAnalyzer().evaluate_binance_rows(
        mapped_symbol="TEST/USDT:USDT",
        market_id=market_id,
        funding_rows=[
            {"symbol": market_id, "fundingTime": ms(16 * 3600), "fundingRate": "0.0001"},
            {"symbol": market_id, "fundingTime": ms(8 * 3600), "fundingRate": "0.0002"},
            {"symbol": market_id, "fundingTime": ms(0), "fundingRate": "0.0004"},
        ],
        taker_rows=[
            {"symbol": market_id, "timestamp": ms(3600), "buySellRatio": "1.2"},
            {"symbol": market_id, "timestamp": ms(0), "buySellRatio": "0.7"},
        ],
        top_trader_rows=[
            {"symbol": market_id, "timestamp": ms(0), "longShortRatio": "1.4"},
        ],
        open_interest_rows=[
            {
                "symbol": market_id,
                "timestamp": ms(3600),
                "sumOpenInterestValue": "1000",
                "sumOpenInterest": "10",
            },
            {
                "symbol": market_id,
                "timestamp": ms(1800),
                "sumOpenInterestValue": "1050",
                "sumOpenInterest": "10.5",
            },
            {
                "symbol": market_id,
                "timestamp": ms(0),
                "sumOpenInterestValue": "1155",
                "sumOpenInterest": "11.55",
            },
        ],
        retrieved_at=retrieved_at,
    )

    assert result["available"] is True
    packet = result["precrash_observations"]
    assert packet["contract_version"] == "precrash_derivatives_observation_v1"
    assert packet["observational_only"] is True
    assert packet["hard_gating_allowed"] is False
    assert packet["promotion_allowed"] is False
    assert packet["funding_percentile"] == 1.0
    assert packet["funding_z_score"] is not None
    assert packet["funding_z_score"] > 1.0
    assert packet["taker_ratio_momentum_1h"] == -0.5
    assert packet["oi_acceleration_pct_per_hour2"] == 20.0


def _synthetic_candles() -> list[list[float]]:
    rows: list[list[float]] = []
    base_ts = 1_700_000_000_000
    for index in range(120):
        close = 100.0 + index * 0.05
        high = close + 0.4
        low = close - 0.4
        volume = 100.0
        if index == 100:
            high = 125.0
            close = 120.0
            low = 99.0
            volume = 500.0
        if index >= 117:
            # Make the latest range/volume measurably larger without assigning
            # any predictive label or changing a gate.
            high = close + 1.2
            low = close - 1.2
            volume = 180.0 + (index - 117) * 20.0
        rows.append(
            [base_ts + index * 300_000, close, high, low, close, volume]
        )
    return rows


def test_candles_emit_event_and_volatility_observations_without_gating():
    result = MultiTimeframeAnalyzer()._evaluate(_synthetic_candles())

    packet = result["precrash_observations"]
    assert packet["contract_version"] == "precrash_candle_observation_v1"
    assert packet["observational_only"] is True
    assert packet["hard_gating_allowed"] is False
    assert packet["promotion_allowed"] is False
    assert packet["peak_at"] == 1_700_000_000_000 + 100 * 300_000
    assert packet["bars_since_peak"] == 19
    assert packet["price_return_3bars_pct"] == result["return_3bars_pct"]
    assert packet["volume_ratio_to_baseline"] is not None
    assert packet["volatility_expansion_ratio"] is not None


def test_orderbook_depth_dynamics_are_recorded_observationally_only():
    class FakeExchange:
        def __init__(self):
            self.books = [
                {"bids": [[100.0, 1.2]], "asks": [[101.0, 1.1]]},
                {"bids": [[100.0, 1.4]], "asks": [[101.0, 0.9]]},
            ]

        async def fetch_order_book(self, symbol, limit=20):
            return self.books.pop(0)

        async def fetch_trades(self, symbol, limit=100):
            now_ms = int(time.time() * 1000)
            return [
                {
                    "timestamp": now_ms,
                    "side": "sell" if index % 2 == 0 else "buy",
                    "price": 100.5,
                    "amount": 0.1,
                }
                for index in range(20)
            ]

    async def scenario():
        analyzer = MicrostructureAnalyzer(
            executable_notional=25.0,
            snapshot_delay_seconds=0.0,
        )
        # Keep freshness deterministic for this unit test by omitting exchange
        # timestamps and letting the analyzer use local receipt timestamps.
        first = {"bids": [[100.0, 1.0]], "asks": [[101.0, 1.0]]}
        market = {
            "contractSize": 1.0,
            "limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}},
            "precision": {"amount": 0.01, "price": 0.01},
        }
        return await analyzer.analyze(FakeExchange(), "TEST/USDT:USDT", first, market)

    result = asyncio.run(scenario())
    packet = result["precrash_observations"]
    assert packet["contract_version"] == "precrash_orderbook_observation_v1"
    assert packet["observational_only"] is True
    assert packet["hard_gating_allowed"] is False
    assert packet["promotion_allowed"] is False
    assert packet["bid_depth_change_pct"] == 40.0
    assert packet["ask_depth_change_pct"] == -10.0
