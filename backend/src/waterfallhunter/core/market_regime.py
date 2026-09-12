import math
from typing import Any


class MarketRegimeAnalyzer:
    """
    Pure BTC benchmark measurement layer.

    This module intentionally does NOT:
    - change candidate state
    - create trade eligibility
    - apply PRE-TRIGGER/ARMED thresholds
    - act as a hard gate

    It only summarizes the already validated BTC benchmark packet.
    """

    timeframes = (
        "4h",
        "1h",
        "15m",
        "5m",
    )

    return_windows = (
        3,
        6,
        12,
    )

    @staticmethod
    def _finite(value: Any) -> float | None:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return float(value)

        return None

    @staticmethod
    def _safe_ratio(
        numerator: Any,
        denominator: Any,
    ) -> float | None:
        num = MarketRegimeAnalyzer._finite(
            numerator
        )

        den = MarketRegimeAnalyzer._finite(
            denominator
        )

        if (
            num is None
            or den is None
            or den == 0
        ):
            return None

        return round(
            num / den,
            4,
        )

    @classmethod
    def measure(
        cls,
        benchmark_context: dict,
    ) -> dict:
        """
        Summarize BTC market context without imposing trading rules.

        Input is the benchmark_context produced by MultiExchangeValidator.
        """
        if not isinstance(
            benchmark_context,
            dict,
        ):
            return {
                "available": False,
                "reason": (
                    "benchmark context unavailable"
                ),
                "benchmark": "BTC",
                "source_exchange": None,
                "mapped_symbol": None,
                "timeframes": {},
                "cross_timeframe": {},
                "atr_term_structure": {},
            }

        if (
            benchmark_context.get(
                "available"
            )
            is not True
        ):
            return {
                "available": False,
                "reason": (
                    benchmark_context.get(
                        "reason"
                    )
                    or (
                        "benchmark context "
                        "not complete"
                    )
                ),
                "benchmark": "BTC",
                "source_exchange": (
                    benchmark_context.get(
                        "source_exchange"
                    )
                ),
                "mapped_symbol": (
                    benchmark_context.get(
                        "mapped_symbol"
                    )
                ),
                "timeframes": {},
                "cross_timeframe": {},
                "atr_term_structure": {},
            }

        details = benchmark_context.get(
            "details"
        )

        if not isinstance(
            details,
            dict,
        ):
            return {
                "available": False,
                "reason": (
                    "benchmark details unavailable"
                ),
                "benchmark": "BTC",
                "source_exchange": (
                    benchmark_context.get(
                        "source_exchange"
                    )
                ),
                "mapped_symbol": (
                    benchmark_context.get(
                        "mapped_symbol"
                    )
                ),
                "timeframes": {},
                "cross_timeframe": {},
                "atr_term_structure": {},
            }

        result_timeframes: dict[
            str,
            dict[str, Any],
        ] = {}

        returns_by_window: dict[
            int,
            list[float],
        ] = {
            bars: []
            for bars in cls.return_windows
        }

        atr_by_timeframe: dict[
            str,
            float | None,
        ] = {}

        complete_timeframes = 0

        for timeframe in cls.timeframes:
            context = details.get(
                timeframe
            )

            if not isinstance(
                context,
                dict,
            ):
                continue

            if (
                context.get("valid")
                is not True
            ):
                continue

            complete_timeframes += 1

            timeframe_packet: dict[
                str,
                Any,
            ] = {
                "atr_pct": cls._finite(
                    context.get(
                        "atr_pct"
                    )
                ),
                "distance_to_support_atr": (
                    cls._finite(
                        context.get(
                            "distance_to_support_atr"
                        )
                    )
                ),
                "support_broken": (
                    context.get(
                        "support_broken"
                    )
                    if isinstance(
                        context.get(
                            "support_broken"
                        ),
                        bool,
                    )
                    else None
                ),
                "lower_high": (
                    context.get(
                        "lower_high"
                    )
                    if isinstance(
                        context.get(
                            "lower_high"
                        ),
                        bool,
                    )
                    else None
                ),
            }

            negative_returns = 0
            positive_returns = 0
            zero_returns = 0

            for bars in cls.return_windows:
                field = (
                    f"return_{bars}bars_pct"
                )

                value = cls._finite(
                    context.get(
                        field
                    )
                )

                timeframe_packet[
                    field
                ] = value

                if value is None:
                    continue

                returns_by_window[
                    bars
                ].append(
                    value
                )

                if value < 0:
                    negative_returns += 1
                elif value > 0:
                    positive_returns += 1
                else:
                    zero_returns += 1

            timeframe_packet[
                "negative_return_count"
            ] = negative_returns

            timeframe_packet[
                "positive_return_count"
            ] = positive_returns

            timeframe_packet[
                "zero_return_count"
            ] = zero_returns

            short_return = (
                timeframe_packet.get(
                    "return_3bars_pct"
                )
            )

            long_return = (
                timeframe_packet.get(
                    "return_12bars_pct"
                )
            )

            timeframe_packet[
                "return_acceleration_3_vs_12"
            ] = (
                round(
                    short_return
                    - long_return,
                    4,
                )
                if (
                    short_return
                    is not None
                    and long_return
                    is not None
                )
                else None
            )

            result_timeframes[
                timeframe
            ] = timeframe_packet

            atr_by_timeframe[
                timeframe
            ] = timeframe_packet[
                "atr_pct"
            ]

        cross_timeframe: dict[
            str,
            Any,
        ] = {}

        for bars in cls.return_windows:
            values = returns_by_window[
                bars
            ]

            cross_timeframe[
                f"mean_return_{bars}bars_pct"
            ] = (
                round(
                    sum(values)
                    / len(values),
                    4,
                )
                if values
                else None
            )

            cross_timeframe[
                f"negative_timeframes_{bars}bars"
            ] = sum(
                value < 0
                for value in values
            )

            cross_timeframe[
                f"positive_timeframes_{bars}bars"
            ] = sum(
                value > 0
                for value in values
            )

            cross_timeframe[
                f"available_timeframes_{bars}bars"
            ] = len(
                values
            )

        support_broken_count = sum(
            packet.get(
                "support_broken"
            )
            is True
            for packet
            in result_timeframes.values()
        )

        lower_high_count = sum(
            packet.get(
                "lower_high"
            )
            is True
            for packet
            in result_timeframes.values()
        )

        cross_timeframe[
            "support_broken_count"
        ] = support_broken_count

        cross_timeframe[
            "lower_high_count"
        ] = lower_high_count

        cross_timeframe[
            "complete_timeframes"
        ] = complete_timeframes

        atr_term_structure = {
            "atr_5m_pct": (
                atr_by_timeframe.get(
                    "5m"
                )
            ),
            "atr_15m_pct": (
                atr_by_timeframe.get(
                    "15m"
                )
            ),
            "atr_1h_pct": (
                atr_by_timeframe.get(
                    "1h"
                )
            ),
            "atr_4h_pct": (
                atr_by_timeframe.get(
                    "4h"
                )
            ),
            "atr_5m_to_15m_ratio": (
                cls._safe_ratio(
                    atr_by_timeframe.get(
                        "5m"
                    ),
                    atr_by_timeframe.get(
                        "15m"
                    ),
                )
            ),
            "atr_15m_to_1h_ratio": (
                cls._safe_ratio(
                    atr_by_timeframe.get(
                        "15m"
                    ),
                    atr_by_timeframe.get(
                        "1h"
                    ),
                )
            ),
            "atr_1h_to_4h_ratio": (
                cls._safe_ratio(
                    atr_by_timeframe.get(
                        "1h"
                    ),
                    atr_by_timeframe.get(
                        "4h"
                    ),
                )
            ),
        }

        return {
            "available": (
                complete_timeframes > 0
            ),
            "reason": (
                None
                if complete_timeframes > 0
                else (
                    "no valid benchmark "
                    "timeframes"
                )
            ),
            "benchmark": "BTC",
            "source_exchange": (
                benchmark_context.get(
                    "source_exchange"
                )
            ),
            "mapped_symbol": (
                benchmark_context.get(
                    "mapped_symbol"
                )
            ),
            "retrieved_at": (
                benchmark_context.get(
                    "retrieved_at"
                )
            ),
            "timeframes": (
                result_timeframes
            ),
            "cross_timeframe": (
                cross_timeframe
            ),
            "atr_term_structure": (
                atr_term_structure
            ),
        }
