import asyncio
import copy
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

from waterfallhunter.core.channel_strategy import channel_stages


class MultiTimeframeAnalyzer:
    timeframes = ("5m", "15m", "1h", "4h")
    timeframe_ms = {
        "5m": 300_000,
        "15m": 900_000,
        "1h": 3_600_000,
        "4h": 14_400_000,
    }
    candle_limit = 120
    max_closed_candle_age_intervals = 2
    cache_max_entries = 1024

    def __init__(self) -> None:
        self._closed_series_cache: OrderedDict[tuple[str, str, str, int], dict[str, Any]] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_evictions = 0

    @staticmethod
    def _exchange_cache_id(exchange: Any) -> str:
        exchange_id = getattr(exchange, "id", None)
        if isinstance(exchange_id, str) and exchange_id:
            return exchange_id
        return f"{type(exchange).__module__}.{type(exchange).__qualname__}:{id(exchange)}"

    def _expected_closed_start(self, timeframe: str, *, now_ms: int) -> int:
        gap = self.timeframe_ms[timeframe]
        return (int(now_ms) // gap) * gap - gap

    def cache_diagnostics(self) -> Dict[str, int]:
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "evictions": self._cache_evictions,
            "entries": len(self._closed_series_cache),
        }

    async def _load_closed_series(
        self,
        exchange: Any,
        symbol: str,
        timeframe: str,
        *,
        now_ms: int | None = None,
    ) -> Optional[List[List[float]]]:
        observed_now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        expected_closed_start = self._expected_closed_start(
            timeframe, now_ms=observed_now_ms
        )
        key = (
            self._exchange_cache_id(exchange),
            symbol,
            timeframe,
            self.candle_limit,
        )
        cached = self._closed_series_cache.get(key)
        if (
            isinstance(cached, dict)
            and cached.get("expected_closed_start") == expected_closed_start
        ):
            self._cache_hits += 1
            self._closed_series_cache.move_to_end(key)
            return copy.deepcopy(cached["candles"])

        self._cache_misses += 1
        rows = await exchange.fetch_ohlcv(
            symbol, timeframe=timeframe, limit=self.candle_limit
        )
        candles = self._closed_candles(rows, timeframe, now_ms=observed_now_ms)
        if candles is not None and candles[-1][0] >= expected_closed_start:
            self._closed_series_cache[key] = {
                "expected_closed_start": expected_closed_start,
                "candles": copy.deepcopy(candles),
            }
            self._closed_series_cache.move_to_end(key)
            while len(self._closed_series_cache) > max(1, int(self.cache_max_entries)):
                self._closed_series_cache.popitem(last=False)
                self._cache_evictions += 1
        return candles

    def _closed_candles(
        self,
        rows: List[List[float]],
        timeframe: str,
        *,
        now_ms: int | None = None,
    ) -> Optional[List[List[float]]]:
        if not isinstance(rows, list) or len(rows) < 20:
            return None

        gap = self.timeframe_ms[timeframe]
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        candles = []

        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                return None

            try:
                ts, opening, high, low, close, volume = (
                    float(value) for value in row[:6]
                )
            except (TypeError, ValueError):
                return None

            if (
                ts <= 0
                or min(opening, high, low, close) <= 0
                or volume < 0
                or high < max(opening, close)
                or low > min(opening, close)
            ):
                return None

            if now < ts + gap:
                if row is not rows[-1]:
                    return None
                continue

            candles.append(
                [int(ts), opening, high, low, close, volume]
            )

        if len(candles) < 20:
            return None

        for previous, current in zip(candles, candles[1:]):
            if current[0] - previous[0] != gap:
                return None

        if (
            now - (candles[-1][0] + gap)
            > gap * self.max_closed_candle_age_intervals
        ):
            return None

        return candles

    @staticmethod
    def _atr(
        candles: List[List[float]],
        period: int = 14,
    ) -> Optional[float]:
        if len(candles) < period + 1:
            return None

        true_ranges: List[float] = []
        for previous, current in zip(
            candles[-period - 1 : -1],
            candles[-period:],
        ):
            previous_close = float(previous[4])
            high = float(current[2])
            low = float(current[3])
            true_ranges.append(
                max(
                    high - low,
                    abs(high - previous_close),
                    abs(low - previous_close),
                )
            )

        if not true_ranges:
            return None
        atr = sum(true_ranges) / len(true_ranges)
        return atr if atr > 0 else None

    @staticmethod
    def _return_pct(
        candles: List[List[float]],
        bars: int,
    ) -> Optional[float]:
        if bars <= 0 or len(candles) < bars + 1:
            return None
        earlier_close = float(candles[-bars - 1][4])
        latest_close = float(candles[-1][4])
        if earlier_close <= 0:
            return None
        return (latest_close / earlier_close - 1.0) * 100.0

    @staticmethod
    def _rsi(
        closes: List[float],
        period: int = 14,
    ) -> Optional[float]:
        if len(closes) < period + 1:
            return None

        gains = 0.0
        losses = 0.0
        for earlier, later in zip(
            closes[-period - 1 : -1],
            closes[-period:],
        ):
            change = later - earlier
            gains += max(change, 0.0)
            losses += max(-change, 0.0)

        if losses == 0:
            return 100.0 if gains else 50.0
        rs = (gains / period) / (losses / period)
        return 100 - (100 / (1 + rs))

    @classmethod
    def _precrash_observations(
        cls,
        candles: List[List[float]],
        *,
        return_3bars_pct: float | None,
    ) -> Dict[str, Any]:
        peak_index = max(
            range(len(candles)),
            key=lambda index: float(candles[index][2]),
        )
        peak = candles[peak_index]
        bars_since_peak = len(candles) - 1 - peak_index

        baseline_rows = candles[-23:-3]
        baseline_volume = (
            sum(float(row[5]) for row in baseline_rows) / len(baseline_rows)
            if baseline_rows
            else None
        )
        volume_ratio = (
            float(candles[-1][5]) / baseline_volume
            if baseline_volume is not None and baseline_volume > 0
            else None
        )

        recent_ranges = [float(row[2]) - float(row[3]) for row in candles[-3:]]
        baseline_ranges = [float(row[2]) - float(row[3]) for row in baseline_rows]
        recent_range = sum(recent_ranges) / len(recent_ranges) if recent_ranges else None
        baseline_range = (
            sum(baseline_ranges) / len(baseline_ranges)
            if baseline_ranges
            else None
        )
        volatility_expansion = (
            recent_range / baseline_range
            if recent_range is not None
            and baseline_range is not None
            and baseline_range > 0
            else None
        )

        return {
            "contract_version": "precrash_candle_observation_v1",
            "observational_only": True,
            "hard_gating_allowed": False,
            "promotion_allowed": False,
            "peak_at": int(peak[0]),
            "peak_price": round(float(peak[2]), 10),
            "bars_since_peak": bars_since_peak,
            "price_return_3bars_pct": (
                round(return_3bars_pct, 4)
                if return_3bars_pct is not None
                else None
            ),
            "volume_ratio_to_baseline": (
                round(volume_ratio, 4)
                if volume_ratio is not None
                else None
            ),
            "volatility_expansion_ratio": (
                round(volatility_expansion, 4)
                if volatility_expansion is not None
                else None
            ),
        }

    def _evaluate(
        self,
        candles: List[List[float]],
    ) -> Dict[str, Any]:
        previous, reclaim_bar, latest = candles[-3:]
        closes = [row[4] for row in candles]
        prior_rsi = self._rsi(closes[:-1])
        current_rsi = self._rsi(closes)
        baseline_volume = sum(row[5] for row in candles[-13:-3]) / 10

        two_closed = (
            reclaim_bar[4] < reclaim_bar[1]
            and latest[4] < latest[1]
        )
        lower_high = latest[2] < reclaim_bar[2]
        volume_acceleration = (
            latest[5] > baseline_volume
            and latest[5] > reclaim_bar[5]
        )
        rsi_rollover = (
            prior_rsi is not None
            and current_rsi is not None
            and prior_rsi > current_rsi
            and current_rsi <= 55
        )
        reclaim = (
            previous[4] < candles[-4][3]
            and reclaim_bar[2] >= candles[-4][3]
            and latest[4] < candles[-4][3]
        )
        repump = (
            reclaim_bar[2] > previous[2]
            and reclaim_bar[4] > previous[4]
            and latest[4] < reclaim_bar[4]
        )
        bearish_close = latest[4] < latest[1]
        support = min(row[3] for row in candles[-23:-3])
        atr_14 = self._atr(candles, period=14)
        latest_close = float(latest[4])
        recent_high = max(float(row[2]) for row in candles[-23:])
        distance_to_support = latest_close - support
        distance_to_support_pct = (
            distance_to_support / latest_close * 100.0
            if latest_close > 0
            else None
        )
        distance_to_support_atr = (
            distance_to_support / atr_14
            if atr_14 is not None and atr_14 > 0
            else None
        )
        atr_pct = (
            atr_14 / latest_close * 100.0
            if atr_14 is not None and latest_close > 0
            else None
        )
        distance_from_recent_high_pct = (
            (recent_high - latest_close) / recent_high * 100.0
            if recent_high > 0
            else None
        )
        extension_from_support_atr = (
            abs(distance_to_support) / atr_14
            if atr_14 is not None and atr_14 > 0
            else None
        )
        return_3bars_pct = self._return_pct(candles, 3)
        return_6bars_pct = self._return_pct(candles, 6)
        return_12bars_pct = self._return_pct(candles, 12)

        support_broken = previous[4] < support and latest[4] < support
        failed_pullback = (
            support_broken
            and reclaim_bar[2] >= support
            and reclaim_bar[4] < support
            and lower_high
        )
        strong_breakdown = support_broken and volume_acceleration and bearish_close
        continuation = latest[3] < reclaim_bar[3] and lower_high and bearish_close

        if failed_pullback:
            setup = "FAILED_PULLBACK"
        elif strong_breakdown:
            setup = "BREAKDOWN"
        elif continuation:
            setup = "CONTINUATION"
        else:
            setup = None

        regime_bearish = support_broken and lower_high
        trigger_ready = (
            two_closed
            and lower_high
            and volume_acceleration
            and bearish_close
            and (reclaim or repump)
        )
        is_bearish = all(
            (
                two_closed,
                lower_high,
                volume_acceleration,
                rsi_rollover,
                bearish_close,
                reclaim or repump,
            )
        )

        pre_pump_base = (
            min(row[3] for row in candles[-100:-40])
            if len(candles) >= 100
            else None
        )
        pump_peak = max(row[2] for row in candles[-80:-3])
        pump_pct = (
            (pump_peak / pre_pump_base - 1.0) * 100.0
            if pre_pump_base
            else None
        )
        pre_pump_volume = (
            sum(row[5] for row in candles[-100:-40]) / 60
            if len(candles) >= 100
            else None
        )
        pump_volume = max(row[5] for row in candles[-80:-3])
        volume_climax = bool(
            pre_pump_volume
            and pump_volume >= pre_pump_volume * 1.8
        )

        return {
            "valid": True,
            "two_closed_candles": two_closed,
            "reclaim": reclaim,
            "repump": repump,
            "rsi_rollover": rsi_rollover,
            "rsi": round(current_rsi, 2) if current_rsi is not None else None,
            "lower_high": lower_high,
            "volume_acceleration": volume_acceleration,
            "is_bearish": is_bearish,
            "dynamic_support": support,
            "support_broken": support_broken,
            "setup": setup,
            "regime_bearish": regime_bearish,
            "trigger_ready": trigger_ready,
            "bearish_close": bearish_close,
            "pump_pct": round(pump_pct, 4) if pump_pct is not None else None,
            "volume_climax": volume_climax,
            "atr_14": round(atr_14, 10) if atr_14 is not None else None,
            "atr_pct": round(atr_pct, 4) if atr_pct is not None else None,
            "distance_to_support_pct": (
                round(distance_to_support_pct, 4)
                if distance_to_support_pct is not None
                else None
            ),
            "distance_to_support_atr": (
                round(distance_to_support_atr, 4)
                if distance_to_support_atr is not None
                else None
            ),
            "distance_from_recent_high_pct": (
                round(distance_from_recent_high_pct, 4)
                if distance_from_recent_high_pct is not None
                else None
            ),
            "extension_from_support_atr": (
                round(extension_from_support_atr, 4)
                if extension_from_support_atr is not None
                else None
            ),
            "return_3bars_pct": (
                round(return_3bars_pct, 4)
                if return_3bars_pct is not None
                else None
            ),
            "return_6bars_pct": (
                round(return_6bars_pct, 4)
                if return_6bars_pct is not None
                else None
            ),
            "return_12bars_pct": (
                round(return_12bars_pct, 4)
                if return_12bars_pct is not None
                else None
            ),
            "hype_context": bool(
                pump_pct is not None
                and pump_pct >= 20.0
                and volume_climax
            ),
            "precrash_observations": self._precrash_observations(
                candles,
                return_3bars_pct=return_3bars_pct,
            ),
        }

    @staticmethod
    def channel_stages(
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        required = ("5m", "15m", "1h", "4h")
        if not all(details.get(timeframe, {}).get("valid") for timeframe in required):
            return {
                "hype": False,
                "damage": False,
                "setup": False,
                "setup_type": None,
                "trigger": False,
                "passed": False,
            }

        checks = {
            timeframe: {
                "hype_context": details[timeframe].get("hype_context", False),
                "support_broken": details[timeframe].get("support_broken", False),
                "failed_pullback": details[timeframe].get("setup") == "FAILED_PULLBACK",
                "flags": {
                    "two_bearish": details[timeframe].get("two_closed_candles", False),
                    "lower_high": details[timeframe].get("lower_high", False),
                    "bearish_close": details[timeframe].get("bearish_close", False),
                    "volume_acceleration": details[timeframe].get("volume_acceleration", False),
                },
            }
            for timeframe in required
        }

        stages = channel_stages(checks)
        stages["passed"] = bool(
            stages["hype"]
            and stages["damage"]
            and stages["setup"]
            and stages["trigger"]
        )
        return stages

    async def analyze_hype_context(
        self,
        exchange: Any,
        symbol: str,
    ) -> Dict[str, Any]:
        """Evaluate only the closed 4h series for observational WATCH hype.

        This is deliberately not a promotion or trade gate. Missing, stale,
        gapped, or provider-failed OHLCV is inconclusive and callers must fall
        back to the canonical full validator.
        """
        observed_now_ms = int(time.time() * 1000)
        try:
            candles = await self._load_closed_series(
                exchange, symbol, "4h", now_ms=observed_now_ms
            )
        except Exception as exc:
            return {
                "valid": False,
                "hype_context": False,
                "reason": f"4h OHLCV unavailable: {type(exc).__name__}",
                "details": None,
                "source_capture": {},
            }
        if not candles:
            return {
                "valid": False,
                "hype_context": False,
                "reason": "missing, open, duplicate, gapped, stale, or invalid 4h OHLCV",
                "details": None,
                "source_capture": {},
            }
        details = self._evaluate(candles)
        if not isinstance(details, dict) or details.get("valid") is not True:
            return {
                "valid": False,
                "hype_context": False,
                "reason": "invalid 4h candle evaluation",
                "details": details if isinstance(details, dict) else None,
                "source_capture": {},
            }
        return {
            "valid": True,
            "hype_context": bool(details.get("hype_context")),
            "details": details,
            "source_capture": {
                "primary_closed_ohlcv": {"4h": candles},
                "raw_ohlcv_captured": False,
                "confirmation_ohlcv_captured": False,
            },
        }

    async def analyze_candles(
        self,
        exchange: Any,
        symbol: str,
        confirmation_exchange: Any = None,
        confirmation_symbol: str | None = None,
    ) -> Dict[str, Any]:
        observed_now_ms = int(time.time() * 1000)
        primary = await asyncio.gather(
            *(
                self._load_closed_series(
                    exchange, symbol, tf, now_ms=observed_now_ms
                )
                for tf in self.timeframes
            ),
            return_exceptions=True,
        )

        source_ohlcv: Dict[str, List[List[float]]] = {}
        for tf, candles in zip(self.timeframes, primary):
            if isinstance(candles, Exception) or candles is None:
                continue
            source_ohlcv[tf] = candles

        confirmation_ohlcv = None
        if confirmation_exchange and confirmation_symbol:
            try:
                confirmation_ohlcv = await self._load_closed_series(
                    confirmation_exchange,
                    confirmation_symbol,
                    "15m",
                    now_ms=observed_now_ms,
                )
            except Exception:
                confirmation_ohlcv = None

        return self.evaluate_closed_sources(source_ohlcv, confirmation_ohlcv)

    def evaluate_closed_sources(
        self,
        source_ohlcv: Dict[str, List[List[float]]],
        confirmation_ohlcv: List[List[float]] | None,
    ) -> Dict[str, Any]:
        details: Dict[str, Any] = {}
        confirmed = 0
        for timeframe in self.timeframes:
            candles = source_ohlcv.get(timeframe)
            if not candles:
                details[timeframe] = {
                    "valid": False,
                    "reason": "missing, open, duplicate, gapped, or invalid OHLCV",
                }
                continue
            result = self._evaluate(candles)
            details[timeframe] = result
            confirmed += int(result["is_bearish"])

        confirmation = self._evaluate(confirmation_ohlcv) if confirmation_ohlcv else None
        cross_exchange = bool(
            confirmation
            and all(
                confirmation[name]
                for name in ("two_closed_candles", "lower_high", "bearish_close")
            )
        )
        return {
            "is_breakdown_confirmed": confirmed >= 2 and cross_exchange,
            "breakdown_score": confirmed,
            "cross_exchange_confirmed": cross_exchange,
            "details": details,
            "source_capture": {
                "primary_closed_ohlcv": source_ohlcv,
                "confirmation_closed_ohlcv_15m": confirmation_ohlcv,
                "raw_ohlcv_captured": bool(
                    len(source_ohlcv) == len(self.timeframes)
                    and all(source_ohlcv.get(tf) for tf in self.timeframes)
                ),
                "confirmation_ohlcv_captured": bool(confirmation_ohlcv),
            },
        }
