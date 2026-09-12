"""Pure, versioned evidence scoring for USDT-linear perpetual candidates."""

import math
from typing import Any


class ScoreV2:
    version = "score_v2"

    _TIMEFRAMES = ("4h", "1h", "15m", "5m")
    _CANDLE_FIELDS = {
        "4h": (
            "hype_context",
            "support_broken",
            "lower_high",
            "setup",
            "bearish_close",
            "volume_acceleration",
        ),
        "1h": (
            "two_closed_candles",
            "lower_high",
            "reclaim",
            "repump",
            "rsi_rollover",
            "bearish_close",
            "volume_acceleration",
        ),
        "15m": (
            "two_closed_candles",
            "lower_high",
            "reclaim",
            "repump",
            "rsi_rollover",
            "bearish_close",
            "volume_acceleration",
        ),
        "5m": (
            "two_closed_candles",
            "lower_high",
            "reclaim",
            "repump",
            "rsi_rollover",
            "bearish_close",
            "volume_acceleration",
        ),
    }
    _MICROSTRUCTURE_NUMBERS = (
        "sell_flow_usdt",
        "buy_flow_usdt",
        "bid_depth_usdt",
        "ask_depth_usdt",
        "spread_pct",
        "slippage_pct",
    )
    _DERIVATIVE_NUMBERS = (
        "funding_rate",
        "funding_percentile",
        "oi_change_1h_pct",
        "taker_buy_sell_ratio",
        "top_trader_long_short_ratio",
    )

    def evaluate(
        self,
        candles: dict,
        microstructure: dict,
        derivatives: dict,
        cross_exchange_confirmed: bool,
        price_location: dict,
    ) -> dict:
        gates = self._gates(candles, microstructure, derivatives, cross_exchange_confirmed, price_location)
        if not all(gates.values()):
            return {
                "score_version": self.version,
                "is_valid": False,
                "score": None,
                "components": {},
                "gates": gates,
                "reason": self._reason(gates),
            }

        components = {
            "structural_post_pump": self._structural(candles),
            "entry_timing": self._timing(candles),
            "execution_microstructure": self._execution(microstructure),
            "derivatives_confirmation": self._derivatives(derivatives, candles),
            "cross_exchange_confirmation": 5.0,
            "same_contract_price_location": 5.0 if price_location.get("below_vwap") is True else 0.0,
        }
        return {
            "score_version": self.version,
            "is_valid": True,
            "score": round(sum(components.values()), 2),
            "components": components,
            "gates": gates,
            "reason": None,
        }

    def evaluate_watch(
        self,
        candles: dict,
        microstructure: dict,
        derivatives: dict,
        cross_exchange_confirmed: bool | None,
        price_location: dict,
    ) -> dict:
        components: dict[str, float | None] = {}
        maximums: dict[str, float] = {}

        if self._complete_timeframe(candles, "4h"):
            components["structural_post_pump"] = self._structural(candles)
            maximums["structural_post_pump"] = 35.0
        else:
            components["structural_post_pump"] = None
            maximums["structural_post_pump"] = 0.0

        timing_score = 0.0
        timing_maximum = 0.0
        for timeframe, weight in {"1h": 8.0, "15m": 7.0, "5m": 5.0}.items():
            if self._complete_timeframe(candles, timeframe):
                timing_maximum += weight
                if self._timing_signal(candles[timeframe]):
                    timing_score += weight
        components["entry_timing"] = self._bounded(timing_score, timing_maximum) if timing_maximum else None
        maximums["entry_timing"] = timing_maximum

        if self._observable_microstructure_packet(microstructure):
            components["execution_microstructure"] = (
                self._execution(microstructure)
                if microstructure.get("approved") is True and microstructure.get("spoofing_detected") is False
                else 0.0
            )
            maximums["execution_microstructure"] = 20.0
        else:
            components["execution_microstructure"] = None
            maximums["execution_microstructure"] = 0.0

        if self._complete_derivatives_packet(derivatives):
            components["derivatives_confirmation"] = self._derivatives(derivatives, candles)
            maximums["derivatives_confirmation"] = 15.0
        else:
            components["derivatives_confirmation"] = None
            maximums["derivatives_confirmation"] = 0.0

        if isinstance(cross_exchange_confirmed, bool):
            components["cross_exchange_confirmation"] = 5.0 if cross_exchange_confirmed else 0.0
            maximums["cross_exchange_confirmation"] = 5.0
        else:
            components["cross_exchange_confirmation"] = None
            maximums["cross_exchange_confirmation"] = 0.0

        if self._complete_price_location(price_location):
            components["same_contract_price_location"] = 5.0 if price_location["below_vwap"] else 0.0
            maximums["same_contract_price_location"] = 5.0
        else:
            components["same_contract_price_location"] = None
            maximums["same_contract_price_location"] = 0.0

        available_weight = round(sum(maximums.values()), 2)
        raw_score = round(sum(value for value in components.values() if value is not None), 2)
        unavailable = [name for name, value in components.items() if value is None]
        return {
            "score_version": "score_v2_watch_v1",
            "trade_eligible": False,
            "score": round(raw_score / available_weight * 100, 2) if available_weight else None,
            "raw_score": raw_score,
            "available_weight": available_weight,
            # Historical semantics: absolute available weight on the model's
            # own 0-100 scale (max watch-model weight is 85). The name is kept
            # for contract compatibility with EvidenceQualityPacket.
            "coverage_pct": round(available_weight, 2),
            # True percentage of the fully observable watch packet that was
            # evaluable in this snapshot (component maximums total 100).
            "coverage_ratio_pct": round(available_weight / 100.0 * 100, 2)
            if available_weight else None,
            "components": components,
            "component_maximums": maximums,
            "unavailable_components": unavailable,
        }

    def _gates(
        self,
        candles: dict,
        microstructure: dict,
        derivatives: dict,
        cross_exchange_confirmed: bool,
        price_location: dict,
    ) -> dict:
        return {
            "all_timeframes_valid": all(
                isinstance(candles.get(timeframe), dict) and candles[timeframe].get("valid") is True
                for timeframe in self._TIMEFRAMES
            ),
            "complete_candle_packet": self._complete_candle_packet(candles),
            "complete_microstructure_packet": self._complete_microstructure_packet(microstructure),
            "complete_fresh_derivatives_packet": self._complete_derivatives_packet(derivatives),
            "taker_sell_dominance": self._taker_sell_dominance(derivatives),
            "cross_exchange_confirmed": cross_exchange_confirmed is True,
            "complete_price_location": self._complete_price_location(price_location),
        }

    def _reason(self, gates: dict) -> str:
        reasons = {
            "all_timeframes_valid": "incomplete valid candle packet",
            "complete_candle_packet": "incomplete candle evidence packet",
            "complete_microstructure_packet": "incomplete fresh microstructure packet",
            "complete_fresh_derivatives_packet": "incomplete fresh derivatives packet",
            "taker_sell_dominance": "taker buy/sell has not confirmed sell dominance",
            "cross_exchange_confirmed": "cross-exchange confirmation unavailable",
            "complete_price_location": "incomplete same-contract price-location packet",
        }
        return next(reasons[name] for name, passed in gates.items() if not passed)

    def _structural(self, candles: dict) -> float:
        context = candles["4h"]
        score = (
            (8.0 if context["hype_context"] is True else 0.0)
            + (7.0 if context["support_broken"] is True else 0.0)
            + (5.0 if context["lower_high"] is True else 0.0)
            + (10.0 if context["setup"] == "FAILED_PULLBACK" else 0.0)
            + (3.0 if context["bearish_close"] is True else 0.0)
            + (2.0 if context["volume_acceleration"] is True else 0.0)
        )
        return self._bounded(score, 35.0)

    def _timing(self, candles: dict) -> float:
        weights = {"1h": 8.0, "15m": 7.0, "5m": 5.0}
        score = sum(
            weight
            for timeframe, weight in weights.items()
            if self._timing_signal(candles[timeframe])
        )
        return self._bounded(score, 20.0)

    @staticmethod
    def _timing_signal(context: dict) -> bool:
        return bool(
            context["two_closed_candles"]
            and context["lower_high"]
            and (context["reclaim"] or context["repump"])
            and context["rsi_rollover"]
            and context["bearish_close"]
            and context["volume_acceleration"]
        )

    def _execution(self, microstructure: dict) -> float:
        footprint = microstructure["footprint"]
        score = (
            (5.0 if microstructure["sell_flow_usdt"] > microstructure["buy_flow_usdt"] else 0.0)
            + (5.0 if footprint["available"] is True and footprint["aggressive_selling"] is True else 0.0)
            + (5.0 if microstructure["bid_depth_usdt"] > 0 and microstructure["ask_depth_usdt"] > 0 else 0.0)
            + (5.0 if microstructure["spread_pct"] <= 0.5 and microstructure["slippage_pct"] <= 0.3 else 0.0)
        )
        return self._bounded(score, 20.0)

    def _derivatives(self, derivatives: dict, candles: dict) -> float:
        """Score the short setup continuously; crowding never overrides active buying."""
        funding_rate = float(derivatives["funding_rate"])
        funding_percentile = float(derivatives["funding_percentile"])
        oi_change = float(derivatives["oi_change_1h_pct"])
        taker_ratio = float(derivatives["taker_buy_sell_ratio"])
        top_trader_ratio = float(derivatives["top_trader_long_short_ratio"])
        one_hour = candles.get("1h") if isinstance(candles, dict) else None
        bearish_price_action = isinstance(one_hour, dict) and one_hour.get("bearish_close") is True

        funding_level = self._ramp(funding_rate, 0.0, 0.0005, 1.5)
        funding_extremity = self._ramp(funding_percentile, 0.5, 0.95, 2.5)
        oi_behavior = self._oi_short_behavior(oi_change, bearish_price_action)
        taker_pressure = self._taker_short_pressure(taker_ratio)
        taker_pressure = self._apply_taker_momentum(taker_pressure, derivatives.get("taker_ratio_change_1h"))
        crowding = self._crowding_modifier(top_trader_ratio, taker_ratio)
        return self._bounded(
            funding_level + funding_extremity + oi_behavior + taker_pressure + crowding,
            15.0,
        )

    @classmethod
    def _oi_short_behavior(cls, oi_change_pct: float, bearish_price_action: bool) -> float:
        if not bearish_price_action:
            return 0.4 if oi_change_pct < 0 else 0.0
        if oi_change_pct >= 0.5:
            return 3.0
        if oi_change_pct > 0:
            return 2.0
        if oi_change_pct <= -0.5:
            return 2.3
        if oi_change_pct <= -0.25:
            return 1.7
        return 0.9

    @classmethod
    def _taker_short_pressure(cls, ratio: float) -> float:
        if ratio <= 0.8:
            return 5.0
        if ratio <= 0.9:
            return cls._ramp(0.9 - ratio, 0.0, 0.1, 1.0) + 4.0
        if ratio <= 1.1:
            return 1.5 + cls._ramp(1.1 - ratio, 0.0, 0.2, 2.5)
        if ratio <= 1.5:
            return 0.25 + cls._ramp(1.5 - ratio, 0.0, 0.4, 1.25)
        return 0.0

    @classmethod
    def _apply_taker_momentum(cls, current_pressure: float, ratio_change_1h: Any) -> float:
        change = cls._finite_number(ratio_change_1h)
        if change is None:
            return current_pressure
        if change <= -0.4:
            return min(5.0, current_pressure + 0.75)
        if change <= -0.2:
            return min(5.0, current_pressure + 0.35)
        if change >= 0.4:
            return max(0.0, current_pressure - 0.75)
        if change >= 0.2:
            return max(0.0, current_pressure - 0.35)
        return current_pressure

    @classmethod
    def _crowding_modifier(cls, top_trader_ratio: float, taker_ratio: float) -> float:
        if top_trader_ratio <= 1.2:
            crowding = 0.0
        elif top_trader_ratio <= 1.5:
            crowding = cls._ramp(top_trader_ratio, 1.2, 1.5, 1.0)
        elif top_trader_ratio <= 2.0:
            crowding = 1.0 + cls._ramp(top_trader_ratio, 1.5, 2.0, 2.0)
        else:
            crowding = 3.0
        if taker_ratio <= 1.1:
            return crowding
        if taker_ratio <= 1.5:
            return crowding * (0.5 + cls._ramp(1.5 - taker_ratio, 0.0, 0.4, 0.5))
        return 0.0

    def _complete_candle_packet(self, candles: dict) -> bool:
        return all(
            isinstance(candles.get(timeframe), dict)
            and all(field in candles[timeframe] for field in fields)
            for timeframe, fields in self._CANDLE_FIELDS.items()
        )

    def _complete_timeframe(self, candles: dict, timeframe: str) -> bool:
        context = candles.get(timeframe)
        return bool(
            isinstance(context, dict)
            and context.get("valid") is True
            and all(field in context for field in self._CANDLE_FIELDS[timeframe])
        )

    def _complete_microstructure_packet(self, microstructure: dict) -> bool:
        footprint = microstructure.get("footprint")
        return bool(
            microstructure.get("approved") is True
            and microstructure.get("spoofing_detected") is False
            and isinstance(footprint, dict)
            and "available" in footprint
            and "aggressive_selling" in footprint
            and all(self._finite(microstructure.get(name)) for name in self._MICROSTRUCTURE_NUMBERS)
        )

    def _observable_microstructure_packet(self, microstructure: dict) -> bool:
        footprint = microstructure.get("footprint")
        return bool(
            isinstance(footprint, dict)
            and "available" in footprint
            and "aggressive_selling" in footprint
            and isinstance(microstructure.get("approved"), bool)
            and isinstance(microstructure.get("spoofing_detected"), bool)
            and all(self._finite(microstructure.get(name)) for name in self._MICROSTRUCTURE_NUMBERS)
        )

    def _complete_derivatives_packet(self, derivatives: dict) -> bool:
        return bool(
            derivatives.get("available") is True
            and all(self._finite(derivatives.get(name)) for name in self._DERIVATIVE_NUMBERS)
        )

    def _taker_sell_dominance(self, derivatives: dict) -> bool:
        return self._complete_derivatives_packet(derivatives) and float(derivatives["taker_buy_sell_ratio"]) < 1.0

    @staticmethod
    def _complete_price_location(price_location: dict) -> bool:
        return bool(
            isinstance(price_location, dict)
            and isinstance(price_location.get("below_vwap"), bool)
        )

    @staticmethod
    def _finite(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    @staticmethod
    def _finite_number(value: Any) -> float | None:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None

    @staticmethod
    def _bounded(value: float, maximum: float) -> float:
        return round(max(0.0, min(float(value), maximum)), 2)

    @staticmethod
    def _ramp(value: float, lower: float, upper: float, maximum: float) -> float:
        if upper <= lower:
            raise ValueError("upper ramp bound must exceed lower bound")
        return max(0.0, min((value - lower) / (upper - lower), 1.0)) * maximum
