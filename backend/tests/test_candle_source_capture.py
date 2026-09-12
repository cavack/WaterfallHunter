import asyncio
from unittest.mock import patch

from waterfallhunter.core.candle_analyzer import MultiTimeframeAnalyzer


NOW_SECONDS = 2_000_000_000.0


def _rows(gap: int):
    now_ms = int(NOW_SECONDS * 1000)
    return [
        [now_ms - (120 - index) * gap, 10.0, 10.5, 9.5, 9.8, 100.0]
        for index in range(120)
    ]


class PrimaryExchange:
    async def fetch_ohlcv(self, symbol, timeframe, limit):
        return _rows(MultiTimeframeAnalyzer.timeframe_ms[timeframe])


class ConfirmationExchange:
    async def fetch_ohlcv(self, symbol, timeframe, limit):
        assert timeframe == "15m"
        return _rows(MultiTimeframeAnalyzer.timeframe_ms["15m"])


def test_candle_analyzer_returns_exact_validated_closed_source_rows():
    analyzer = MultiTimeframeAnalyzer()
    with patch("waterfallhunter.core.candle_analyzer.time.time", return_value=NOW_SECONDS):
        result = asyncio.run(
            analyzer.analyze_candles(
                PrimaryExchange(),
                "TEST/USDT:USDT",
                ConfirmationExchange(),
                "TEST/USDT:USDT",
            )
        )

    capture = result["source_capture"]
    assert capture["raw_ohlcv_captured"] is True
    assert capture["confirmation_ohlcv_captured"] is True
    assert set(capture["primary_closed_ohlcv"]) == {"5m", "15m", "1h", "4h"}
    assert all(len(rows) == 120 for rows in capture["primary_closed_ohlcv"].values())
    assert len(capture["confirmation_closed_ohlcv_15m"]) == 120
    assert capture["primary_closed_ohlcv"]["5m"][-1][0] == int(NOW_SECONDS * 1000) - 300_000


class CountingExchange:
    def __init__(self, exchange_id: str, *, lag_intervals: int = 0):
        self.id = exchange_id
        self.lag_intervals = lag_intervals
        self.fetch_count = {}

    async def fetch_ohlcv(self, symbol, timeframe, limit):
        self.fetch_count[timeframe] = self.fetch_count.get(timeframe, 0) + 1
        gap = MultiTimeframeAnalyzer.timeframe_ms[timeframe]
        now_ms = int(NOW_SECONDS * 1000)
        expected = (now_ms // gap) * gap - gap
        latest = expected - self.lag_intervals * gap
        start = latest - (limit - 1) * gap
        return [
            [start + index * gap, 10.0, 10.5, 9.5, 9.8, 100.0]
            for index in range(limit)
        ]


def test_closed_ohlcv_cache_reuses_primary_and_confirmation_inside_same_bucket():
    analyzer = MultiTimeframeAnalyzer()
    primary = CountingExchange("primary")
    confirmation = CountingExchange("confirmation")
    with patch("waterfallhunter.core.candle_analyzer.time.time", return_value=NOW_SECONDS):
        asyncio.run(analyzer.analyze_candles(primary, "TEST/USDT:USDT", confirmation, "TEST/USDT:USDT"))
        asyncio.run(analyzer.analyze_candles(primary, "TEST/USDT:USDT", confirmation, "TEST/USDT:USDT"))
    assert primary.fetch_count == {tf: 1 for tf in analyzer.timeframes}
    assert confirmation.fetch_count == {"15m": 1}


def test_closed_ohlcv_cache_refetches_after_timeframe_boundary():
    analyzer = MultiTimeframeAnalyzer()
    primary = CountingExchange("primary")
    with patch("waterfallhunter.core.candle_analyzer.time.time", return_value=NOW_SECONDS):
        asyncio.run(analyzer.analyze_candles(primary, "TEST/USDT:USDT"))
    with patch(
        "waterfallhunter.core.candle_analyzer.time.time",
        return_value=NOW_SECONDS + 3600.0,
    ):
        asyncio.run(analyzer.analyze_candles(primary, "TEST/USDT:USDT"))

    assert primary.fetch_count["1h"] == 2


def test_lagged_closed_ohlcv_response_is_used_but_not_cached():
    analyzer = MultiTimeframeAnalyzer()
    exchange = CountingExchange("lagged", lag_intervals=1)
    with patch("waterfallhunter.core.candle_analyzer.time.time", return_value=NOW_SECONDS):
        first = asyncio.run(
            analyzer._load_closed_series(exchange, "TEST/USDT:USDT", "15m")
        )
        second = asyncio.run(
            analyzer._load_closed_series(exchange, "TEST/USDT:USDT", "15m")
        )

    assert first is not None
    assert second is not None
    assert exchange.fetch_count["15m"] == 2


def test_closed_ohlcv_cache_is_bounded_and_evicts_oldest_series():
    analyzer = MultiTimeframeAnalyzer()
    analyzer.cache_max_entries = 2
    exchange = CountingExchange("primary")

    with patch("waterfallhunter.core.candle_analyzer.time.time", return_value=NOW_SECONDS):
        for symbol in ("A/USDT:USDT", "B/USDT:USDT", "C/USDT:USDT"):
            asyncio.run(analyzer._load_closed_series(exchange, symbol, "15m"))

    assert len(analyzer._closed_series_cache) == 2
    assert analyzer.cache_diagnostics()["evictions"] == 1


def test_hype_context_probe_fetches_only_closed_4h_series():
    analyzer = MultiTimeframeAnalyzer()
    exchange = CountingExchange("hype-only")
    with patch("waterfallhunter.core.candle_analyzer.time.time", return_value=NOW_SECONDS):
        result = asyncio.run(analyzer.analyze_hype_context(exchange, "TEST/USDT:USDT"))

    assert result["valid"] is True
    assert result["hype_context"] is False
    assert result["details"]["valid"] is True
    assert exchange.fetch_count == {"4h": 1}
    assert set(result["source_capture"]["primary_closed_ohlcv"]) == {"4h"}
    assert result["source_capture"]["raw_ohlcv_captured"] is False
