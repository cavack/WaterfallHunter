from waterfallhunter.core.anti_chase import (
    AntiChaseAnalyzer,
)


def candle_packet():
    return {
        "1h": {
            "distance_to_support_atr": 0.4,
            "distance_from_recent_high_pct": 12.0,
            "support_broken": False,
            "lower_high": True,
            "return_3bars_pct": -4.0,
            "return_6bars_pct": -8.0,
            "return_12bars_pct": -15.0,
        },
        "15m": {
            "distance_to_support_atr": -0.3,
            "distance_from_recent_high_pct": 14.0,
            "support_broken": False,
            "lower_high": True,
            "return_3bars_pct": -2.0,
            "return_6bars_pct": -5.0,
            "return_12bars_pct": -9.0,
        },
        "5m": {
            "distance_to_support_atr": -1.2,
            "distance_from_recent_high_pct": 16.0,
            "support_broken": True,
            "lower_high": True,
            "return_3bars_pct": -1.0,
            "return_6bars_pct": -3.0,
            "return_12bars_pct": -6.0,
        },
    }


def relative_packet():
    return {
        "available": True,
        "timeframes": {
            "1h": {
                "relative_return_3bars_pct": -4.5,
                "relative_return_6bars_pct": -8.5,
                "relative_return_12bars_pct": -15.5,
            },
            "15m": {
                "relative_return_3bars_pct": -2.2,
                "relative_return_6bars_pct": -5.2,
                "relative_return_12bars_pct": -9.2,
            },
            "5m": {
                "relative_return_3bars_pct": -1.1,
                "relative_return_6bars_pct": -3.1,
                "relative_return_12bars_pct": -6.1,
            },
        },
    }


def test_anti_chase_separates_pre_break_and_post_break_distance():
    result = AntiChaseAnalyzer.measure(
        candle_packet(),
        relative_packet(),
    )

    one_hour = result[
        "timeframes"
    ]["1h"]

    fifteen_minute = result[
        "timeframes"
    ]["15m"]

    assert (
        one_hour[
            "pre_break_distance_atr"
        ]
        == 0.4
    )

    assert (
        one_hour[
            "post_break_extension_atr"
        ]
        == 0.0
    )

    assert (
        fifteen_minute[
            "pre_break_distance_atr"
        ]
        == 0.0
    )

    assert (
        fifteen_minute[
            "post_break_extension_atr"
        ]
        == 0.3
    )


def test_anti_chase_distinguishes_single_close_from_confirmed_break():
    result = AntiChaseAnalyzer.measure(
        candle_packet(),
        relative_packet(),
    )

    fifteen_minute = result[
        "timeframes"
    ]["15m"]

    five_minute = result[
        "timeframes"
    ]["5m"]

    assert (
        fifteen_minute[
            "below_support"
        ]
        is True
    )

    assert (
        fifteen_minute[
            "single_close_below_support"
        ]
        is True
    )

    assert (
        fifteen_minute[
            "confirmed_support_break"
        ]
        is False
    )

    assert (
        five_minute[
            "confirmed_support_break"
        ]
        is True
    )


def test_anti_chase_preserves_absolute_and_relative_selloff_measurements():
    result = AntiChaseAnalyzer.measure(
        candle_packet(),
        relative_packet(),
    )

    five_minute = result[
        "timeframes"
    ]["5m"]

    assert (
        five_minute[
            "selloff_12bars_pct"
        ]
        == 6.0
    )

    assert (
        five_minute[
            "relative_weakness_12bars_pct"
        ]
        == 6.1
    )


def test_anti_chase_cross_timeframe_summary_is_measurement_only():
    result = AntiChaseAnalyzer.measure(
        candle_packet(),
        relative_packet(),
    )

    cross = result[
        "cross_timeframe"
    ]

    assert (
        cross[
            "valid_timeframes"
        ]
        == 3
    )

    assert (
        cross[
            "below_support_count"
        ]
        == 2
    )

    assert (
        cross[
            "confirmed_support_break_count"
        ]
        == 1
    )

    assert (
        cross[
            "single_close_below_support_count"
        ]
        == 1
    )

    assert (
        cross[
            "max_post_break_extension_atr"
        ]
        == 1.2
    )

    assert (
        cross[
            "max_distance_from_recent_high_pct"
        ]
        == 16.0
    )

    assert (
        cross[
            "largest_absolute_selloff_pct"
        ]
        == 15.0
    )

    assert (
        cross[
            "largest_relative_weakness_pct"
        ]
        == 15.5
    )


def test_anti_chase_does_not_treat_positive_returns_as_selloff():
    result = AntiChaseAnalyzer.measure(
        {
            "5m": {
                "distance_to_support_atr": 1.0,
                "distance_from_recent_high_pct": 2.0,
                "support_broken": False,
                "lower_high": False,
                "return_3bars_pct": 2.0,
                "return_6bars_pct": 3.0,
                "return_12bars_pct": 4.0,
            }
        },
        {
            "timeframes": {
                "5m": {
                    "relative_return_3bars_pct": 1.0,
                    "relative_return_6bars_pct": 2.0,
                    "relative_return_12bars_pct": 3.0,
                }
            }
        },
    )

    five_minute = result[
        "timeframes"
    ]["5m"]

    assert (
        five_minute[
            "selloff_12bars_pct"
        ]
        == 0.0
    )

    assert (
        five_minute[
            "relative_weakness_12bars_pct"
        ]
        == 0.0
    )


def test_anti_chase_fails_soft_without_features():
    result = AntiChaseAnalyzer.measure(
        None
    )

    assert result[
        "available"
    ] is False

    assert result[
        "timeframes"
    ] == {}

    assert result[
        "cross_timeframe"
    ] == {}
