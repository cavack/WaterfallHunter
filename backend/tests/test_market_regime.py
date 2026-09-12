from waterfallhunter.core.market_regime import (
    MarketRegimeAnalyzer,
)


def benchmark_packet():
    return {
        "available": True,
        "reason": None,
        "source_exchange": "binance",
        "mapped_symbol": "BTC/USDT:USDT",
        "retrieved_at": 1_700_000_000.0,
        "details": {
            "4h": {
                "valid": True,
                "atr_pct": 2.0,
                "distance_to_support_atr": 1.5,
                "support_broken": False,
                "lower_high": True,
                "return_3bars_pct": -1.0,
                "return_6bars_pct": -2.0,
                "return_12bars_pct": -3.0,
            },
            "1h": {
                "valid": True,
                "atr_pct": 1.0,
                "distance_to_support_atr": 0.8,
                "support_broken": False,
                "lower_high": True,
                "return_3bars_pct": -0.8,
                "return_6bars_pct": -1.2,
                "return_12bars_pct": -1.5,
            },
            "15m": {
                "valid": True,
                "atr_pct": 0.5,
                "distance_to_support_atr": 0.4,
                "support_broken": False,
                "lower_high": True,
                "return_3bars_pct": -0.4,
                "return_6bars_pct": -0.6,
                "return_12bars_pct": -0.8,
            },
            "5m": {
                "valid": True,
                "atr_pct": 0.25,
                "distance_to_support_atr": 0.2,
                "support_broken": True,
                "lower_high": True,
                "return_3bars_pct": -0.2,
                "return_6bars_pct": -0.3,
                "return_12bars_pct": -0.4,
            },
        },
    }


def test_market_regime_measurement_preserves_benchmark_context():
    result = MarketRegimeAnalyzer.measure(
        benchmark_packet()
    )

    assert result["available"] is True

    assert (
        result["source_exchange"]
        == "binance"
    )

    assert (
        result["mapped_symbol"]
        == "BTC/USDT:USDT"
    )

    assert (
        result["timeframes"]["5m"]
        ["return_3bars_pct"]
        == -0.2
    )

    assert (
        result["timeframes"]["5m"]
        ["support_broken"]
        is True
    )


def test_market_regime_computes_cross_timeframe_measurements():
    result = MarketRegimeAnalyzer.measure(
        benchmark_packet()
    )

    cross = result[
        "cross_timeframe"
    ]

    assert (
        cross[
            "negative_timeframes_3bars"
        ]
        == 4
    )

    assert (
        cross[
            "positive_timeframes_3bars"
        ]
        == 0
    )

    assert (
        cross[
            "available_timeframes_3bars"
        ]
        == 4
    )

    assert (
        cross[
            "support_broken_count"
        ]
        == 1
    )

    assert (
        cross[
            "lower_high_count"
        ]
        == 4
    )

    assert (
        cross[
            "mean_return_3bars_pct"
        ]
        == -0.6
    )


def test_market_regime_computes_atr_term_structure_without_thresholds():
    result = MarketRegimeAnalyzer.measure(
        benchmark_packet()
    )

    atr = result[
        "atr_term_structure"
    ]

    assert (
        atr[
            "atr_5m_to_15m_ratio"
        ]
        == 0.5
    )

    assert (
        atr[
            "atr_15m_to_1h_ratio"
        ]
        == 0.5
    )

    assert (
        atr[
            "atr_1h_to_4h_ratio"
        ]
        == 0.5
    )


def test_market_regime_exposes_return_acceleration_as_measurement_only():
    result = MarketRegimeAnalyzer.measure(
        benchmark_packet()
    )

    five_minute = result[
        "timeframes"
    ]["5m"]

    assert (
        five_minute[
            "return_acceleration_3_vs_12"
        ]
        == 0.2
    )


def test_market_regime_does_not_invent_missing_values():
    packet = benchmark_packet()

    packet["details"]["15m"][
        "return_6bars_pct"
    ] = None

    packet["details"]["15m"][
        "atr_pct"
    ] = None

    result = MarketRegimeAnalyzer.measure(
        packet
    )

    assert (
        result["timeframes"]["15m"]
        ["return_6bars_pct"]
        is None
    )

    assert (
        result["atr_term_structure"]
        ["atr_5m_to_15m_ratio"]
        is None
    )

    assert (
        result["cross_timeframe"]
        ["available_timeframes_6bars"]
        == 3
    )


def test_market_regime_fails_soft_when_benchmark_is_unavailable():
    result = MarketRegimeAnalyzer.measure(
        {
            "available": False,
            "reason": (
                "BTC benchmark unavailable"
            ),
            "source_exchange": "bingx",
            "mapped_symbol": None,
        }
    )

    assert result["available"] is False

    assert (
        result["reason"]
        == "BTC benchmark unavailable"
    )

    assert (
        result["source_exchange"]
        == "bingx"
    )

    assert result[
        "timeframes"
    ] == {}
