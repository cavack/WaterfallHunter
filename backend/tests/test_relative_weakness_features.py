from waterfallhunter.core.dashboard import compact_metrics
from waterfallhunter.core.multi_exchange_validator import (
    MultiExchangeValidator,
)


def test_relative_return_features_measure_candidate_underperformance():
    candidate = {
        "5m": {
            "return_3bars_pct": -1.5,
            "return_6bars_pct": -2.0,
            "return_12bars_pct": -3.0,
        }
    }

    benchmark = {
        "5m": {
            "return_3bars_pct": 0.5,
            "return_6bars_pct": -0.5,
            "return_12bars_pct": -1.0,
        }
    }

    result = (
        MultiExchangeValidator
        ._relative_return_features(
            candidate,
            benchmark,
        )
    )

    assert result["available"] is True

    five_minute = result[
        "timeframes"
    ]["5m"]

    assert (
        five_minute[
            "relative_return_3bars_pct"
        ]
        == -2.0
    )

    assert (
        five_minute[
            "relative_return_6bars_pct"
        ]
        == -1.5
    )

    assert (
        five_minute[
            "relative_return_12bars_pct"
        ]
        == -2.0
    )


def test_relative_return_features_do_not_invent_missing_data():
    result = (
        MultiExchangeValidator
        ._relative_return_features(
            {
                "15m": {
                    "return_3bars_pct": -1.0,
                }
            },
            {
                "15m": {
                    "return_3bars_pct": None,
                }
            },
        )
    )

    assert result["available"] is False
    assert result["available_pairs"] == 0

    assert (
        result["timeframes"]["15m"]
        ["relative_return_3bars_pct"]
        is None
    )


def test_compact_metrics_preserves_benchmark_and_relative_features():
    metrics = {
        "benchmark_context": {
            "available": True,
            "source_exchange": "binance",
            "mapped_symbol": "BTC/USDT:USDT",
            "details": {
                "5m": {
                    "return_3bars_pct": 0.4,
                }
            },
        },
        "relative_weakness_features": {
            "available": True,
            "benchmark": "BTC",
            "available_pairs": 1,
            "timeframes": {
                "5m": {
                    "relative_return_3bars_pct": -1.2,
                }
            },
        },
    }

    compact = compact_metrics(metrics)

    assert (
        compact["benchmark_context"]
        ["available"]
        is True
    )

    assert (
        compact[
            "relative_weakness_features"
        ]["available"]
        is True
    )

    assert (
        compact[
            "relative_weakness_features"
        ]["timeframes"]["5m"]
        ["relative_return_3bars_pct"]
        == -1.2
    )
