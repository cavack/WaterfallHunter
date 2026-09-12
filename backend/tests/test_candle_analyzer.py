import time

from waterfallhunter.core.candle_analyzer import MultiTimeframeAnalyzer


def closed_failed_pullback_candles():
    start = (
        int(time.time() * 1000)
        - 23 * 300_000
    )

    rows = [
        [
            start + index * 300_000,
            10.0,
            10.2,
            9.8,
            10.0,
            10.0,
        ]
        for index in range(20)
    ]

    rows.extend(
        [
            [
                start + 20 * 300_000,
                10.0,
                10.0,
                9.2,
                9.3,
                12.0,
            ],
            [
                start + 21 * 300_000,
                9.9,
                10.0,
                9.5,
                9.6,
                14.0,
            ],
            [
                start + 22 * 300_000,
                9.7,
                9.9,
                9.0,
                9.2,
                30.0,
            ],
        ]
    )

    return rows


def test_failed_pullback_has_regime_setup_and_trigger_evidence():
    result = MultiTimeframeAnalyzer()._evaluate(
        closed_failed_pullback_candles()
    )

    assert result["regime_bearish"] is True
    assert result["setup"] == "FAILED_PULLBACK"
    assert result["trigger_ready"] is True
    assert result["bearish_close"] is True


def test_gapped_candles_are_rejected():
    analyzer = MultiTimeframeAnalyzer()

    now = int(time.time() * 1000)
    start = now - 25 * 300_000

    rows = [
        [
            start + index * 300_000,
            10.0,
            10.2,
            9.8,
            10.0,
            1.0,
        ]
        for index in range(20)
    ]

    rows[10][0] += 300_000

    assert analyzer._closed_candles(
        rows,
        "5m",
    ) is None


def test_stale_or_zero_price_candles_are_rejected():
    analyzer = MultiTimeframeAnalyzer()

    gap = analyzer.timeframe_ms["5m"]

    start = (
        int(time.time() * 1000)
        - 31 * 86_400_000
    )

    stale = [
        [
            start + index * gap,
            10.0,
            10.2,
            9.8,
            10.0,
            1.0,
        ]
        for index in range(20)
    ]

    assert analyzer._closed_candles(
        stale,
        "5m",
    ) is None

    fresh = closed_failed_pullback_candles()
    fresh[0][1] = 0.0

    assert analyzer._closed_candles(
        fresh,
        "5m",
    ) is None


def test_evaluate_exposes_atr_geometry_and_rolling_returns():
    result = MultiTimeframeAnalyzer()._evaluate(
        closed_failed_pullback_candles()
    )

    expected_fields = (
        "atr_14",
        "atr_pct",
        "distance_to_support_pct",
        "distance_to_support_atr",
        "distance_from_recent_high_pct",
        "extension_from_support_atr",
        "return_3bars_pct",
        "return_6bars_pct",
        "return_12bars_pct",
    )

    for key in expected_fields:
        assert key in result

    assert result["atr_14"] is not None
    assert result["atr_14"] > 0

    assert result["atr_pct"] is not None

    assert (
        result["distance_to_support_atr"]
        is not None
    )


def test_atr_uses_true_range_not_only_high_low():
    analyzer = MultiTimeframeAnalyzer()

    rows = []

    for index in range(20):
        opening = (
            10.0
            if index == 0
            else rows[-1][4]
        )

        close = opening

        rows.append(
            [
                index,
                opening,
                opening + 0.1,
                opening - 0.1,
                close,
                1.0,
            ]
        )

    # Large gap from the previous close.
    # True Range must capture the gap,
    # not only current high-low.
    rows[-1] = [
        19,
        12.0,
        12.1,
        11.9,
        12.0,
        1.0,
    ]

    atr = analyzer._atr(
        rows,
        period=14,
    )

    assert atr is not None
    assert atr > 0.2


def test_rolling_return_returns_none_when_history_is_too_short():
    rows = [
        [
            0,
            1.0,
            1.0,
            1.0,
            1.0,
            1.0,
        ]
    ]

    assert (
        MultiTimeframeAnalyzer._return_pct(
            rows,
            3,
        )
        is None
    )
