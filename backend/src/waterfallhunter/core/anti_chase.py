import math
from typing import Any


class AntiChaseAnalyzer:
    """
    Pure anti-chase / exhaustion measurement layer.

    This module intentionally does NOT:
    - assign EXHAUSTED
    - assign PRE-TRIGGER
    - reject a trade
    - modify ScoreV2
    - apply trading thresholds

    It only measures how far a candidate has already travelled,
    whether price is above/below structural support, and whether
    relative weakness is already materially developed.
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
    def _finite(
        value: Any,
    ) -> float | None:
        if (
            isinstance(
                value,
                (int, float),
            )
            and not isinstance(
                value,
                bool,
            )
            and math.isfinite(
                value
            )
        ):
            return float(
                value
            )

        return None

    @staticmethod
    def _bool_or_none(
        value: Any,
    ) -> bool | None:
        if isinstance(
            value,
            bool,
        ):
            return value

        return None

    @classmethod
    def measure(
        cls,
        candle_features: dict,
        relative_weakness_features: dict | None = None,
    ) -> dict:
        """
        Produce neutral anti-chase measurements.

        Important semantics:

        distance_to_support_atr > 0
            price remains above dynamic support.

        distance_to_support_atr < 0
            latest close is below dynamic support.

        support_broken=True
            the existing candle analyzer has confirmed its stricter
            two-close support-break condition.

        A negative distance with support_broken=False is therefore
        exposed separately as single_close_below_support rather than
        being called a confirmed breakdown.
        """

        if not isinstance(
            candle_features,
            dict,
        ):
            return {
                "available": False,
                "reason": (
                    "candle features unavailable"
                ),
                "timeframes": {},
                "cross_timeframe": {},
            }

        relative_packet = (
            relative_weakness_features
            if isinstance(
                relative_weakness_features,
                dict,
            )
            else {}
        )

        relative_timeframes = (
            relative_packet.get(
                "timeframes"
            )
            if isinstance(
                relative_packet.get(
                    "timeframes"
                ),
                dict,
            )
            else {}
        )

        result_timeframes: dict[
            str,
            dict[str, Any],
        ] = {}

        below_support_count = 0
        confirmed_support_break_count = 0
        single_close_below_support_count = 0
        lower_high_count = 0

        post_break_extensions: list[
            float
        ] = []

        recent_high_drawdowns: list[
            float
        ] = []

        negative_absolute_returns: list[
            float
        ] = []

        negative_relative_returns: list[
            float
        ] = []

        valid_timeframes = 0

        for timeframe in cls.timeframes:
            context = candle_features.get(
                timeframe
            )

            if not isinstance(
                context,
                dict,
            ):
                continue

            distance_atr = cls._finite(
                context.get(
                    "distance_to_support_atr"
                )
            )

            distance_from_high = cls._finite(
                context.get(
                    "distance_from_recent_high_pct"
                )
            )

            support_broken = cls._bool_or_none(
                context.get(
                    "support_broken"
                )
            )

            lower_high = cls._bool_or_none(
                context.get(
                    "lower_high"
                )
            )

            if (
                distance_atr is None
                and distance_from_high is None
                and support_broken is None
                and lower_high is None
            ):
                continue

            valid_timeframes += 1

            below_support = (
                distance_atr is not None
                and distance_atr < 0
            )

            confirmed_support_break = (
                support_broken is True
            )

            single_close_below_support = (
                below_support
                and support_broken is not True
            )

            pre_break_distance_atr = (
                round(
                    distance_atr,
                    4,
                )
                if (
                    distance_atr is not None
                    and distance_atr >= 0
                )
                else 0.0
                if distance_atr is not None
                else None
            )

            post_break_extension_atr = (
                round(
                    abs(
                        distance_atr
                    ),
                    4,
                )
                if (
                    distance_atr is not None
                    and distance_atr < 0
                )
                else 0.0
                if distance_atr is not None
                else None
            )

            if below_support:
                below_support_count += 1

            if confirmed_support_break:
                confirmed_support_break_count += 1

            if single_close_below_support:
                single_close_below_support_count += 1

            if lower_high is True:
                lower_high_count += 1

            if (
                post_break_extension_atr
                is not None
                and post_break_extension_atr > 0
            ):
                post_break_extensions.append(
                    post_break_extension_atr
                )

            if (
                distance_from_high is not None
                and distance_from_high >= 0
            ):
                recent_high_drawdowns.append(
                    distance_from_high
                )

            relative_context = (
                relative_timeframes.get(
                    timeframe
                )
                if isinstance(
                    relative_timeframes.get(
                        timeframe
                    ),
                    dict,
                )
                else {}
            )

            timeframe_packet: dict[
                str,
                Any,
            ] = {
                "distance_to_support_atr": (
                    distance_atr
                ),
                "pre_break_distance_atr": (
                    pre_break_distance_atr
                ),
                "post_break_extension_atr": (
                    post_break_extension_atr
                ),
                "distance_from_recent_high_pct": (
                    distance_from_high
                ),
                "support_broken": (
                    support_broken
                ),
                "below_support": (
                    below_support
                    if distance_atr is not None
                    else None
                ),
                "confirmed_support_break": (
                    confirmed_support_break
                ),
                "single_close_below_support": (
                    single_close_below_support
                ),
                "lower_high": (
                    lower_high
                ),
            }

            for bars in cls.return_windows:
                absolute_field = (
                    f"return_{bars}bars_pct"
                )

                relative_field = (
                    f"relative_return_{bars}bars_pct"
                )

                absolute_return = cls._finite(
                    context.get(
                        absolute_field
                    )
                )

                relative_return = cls._finite(
                    relative_context.get(
                        relative_field
                    )
                )

                timeframe_packet[
                    absolute_field
                ] = absolute_return

                timeframe_packet[
                    relative_field
                ] = relative_return

                timeframe_packet[
                    f"selloff_{bars}bars_pct"
                ] = (
                    round(
                        abs(
                            absolute_return
                        ),
                        4,
                    )
                    if (
                        absolute_return is not None
                        and absolute_return < 0
                    )
                    else 0.0
                    if absolute_return is not None
                    else None
                )

                timeframe_packet[
                    f"relative_weakness_{bars}bars_pct"
                ] = (
                    round(
                        abs(
                            relative_return
                        ),
                        4,
                    )
                    if (
                        relative_return is not None
                        and relative_return < 0
                    )
                    else 0.0
                    if relative_return is not None
                    else None
                )

                if (
                    absolute_return is not None
                    and absolute_return < 0
                ):
                    negative_absolute_returns.append(
                        absolute_return
                    )

                if (
                    relative_return is not None
                    and relative_return < 0
                ):
                    negative_relative_returns.append(
                        relative_return
                    )

            result_timeframes[
                timeframe
            ] = timeframe_packet

        cross_timeframe = {
            "valid_timeframes": (
                valid_timeframes
            ),
            "below_support_count": (
                below_support_count
            ),
            "confirmed_support_break_count": (
                confirmed_support_break_count
            ),
            "single_close_below_support_count": (
                single_close_below_support_count
            ),
            "lower_high_count": (
                lower_high_count
            ),
            "max_post_break_extension_atr": (
                round(
                    max(
                        post_break_extensions
                    ),
                    4,
                )
                if post_break_extensions
                else 0.0
            ),
            "max_distance_from_recent_high_pct": (
                round(
                    max(
                        recent_high_drawdowns
                    ),
                    4,
                )
                if recent_high_drawdowns
                else None
            ),
            "largest_absolute_selloff_pct": (
                round(
                    abs(
                        min(
                            negative_absolute_returns
                        )
                    ),
                    4,
                )
                if negative_absolute_returns
                else 0.0
            ),
            "largest_relative_weakness_pct": (
                round(
                    abs(
                        min(
                            negative_relative_returns
                        )
                    ),
                    4,
                )
                if negative_relative_returns
                else 0.0
            ),
        }

        return {
            "available": (
                valid_timeframes > 0
            ),
            "reason": (
                None
                if valid_timeframes > 0
                else (
                    "no usable candle features"
                )
            ),
            "timeframes": (
                result_timeframes
            ),
            "cross_timeframe": (
                cross_timeframe
            ),
        }
