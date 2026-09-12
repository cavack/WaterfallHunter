import asyncio
import logging
import math
import time
from typing import Any, Dict

from waterfallhunter.config import settings
from waterfallhunter.core.candle_analyzer import MultiTimeframeAnalyzer
from waterfallhunter.core.cascade_intelligence import build_cascade_evidence
from waterfallhunter.core.coinglass import CoinGlassDerivativesClient
from waterfallhunter.core.derivatives import DerivativesAnalyzer
from waterfallhunter.core.microstructure import MicrostructureAnalyzer
from waterfallhunter.core.multi_exchange import MultiExchangeGateway
from waterfallhunter.core.position_calculator import PositionCalculator
from waterfallhunter.core.score_v2 import ScoreV2
from waterfallhunter.core.signal_metadata import STRICT_STRATEGY_PROFILE
from waterfallhunter.core.ws_streamer import WebSocketManager


logger = logging.getLogger("WaterfallHunter.Validator")


class MultiExchangeValidator:
    experimental_profile = "experimental_pretrigger_v1"
    armed_threshold = 60
    triggered_threshold = 85
    analysis_prefilter_score = 10
    max_cross_exchange_deviation_pct = 5.0

    benchmark_symbol = "BTC/USDT:USDT"
    benchmark_cache_ttl_seconds = 60.0

    def __init__(self):
        self.gateway = MultiExchangeGateway()
        self.ws_manager = WebSocketManager()
        self.candle_analyzer = MultiTimeframeAnalyzer()
        self.position_calculator = PositionCalculator()
        self.microstructure = MicrostructureAnalyzer()
        self.derivatives = DerivativesAnalyzer()
        self.coinglass = CoinGlassDerivativesClient(
            settings.coinglass_api_key,
            settings.coinglass_base_url,
        )

        self._benchmark_cache: dict[str, tuple[float, dict]] = {}
        self.stage_lifecycle_store = None

    @staticmethod
    def _finite_positive(value: Any) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value > 0
        )

    @classmethod
    def _market_maximum_leverage(cls, market: Any) -> float | None:
        packet = market if isinstance(market, dict) else {}
        info = packet.get("info") if isinstance(packet.get("info"), dict) else {}
        limits = (
            packet.get("limits") if isinstance(packet.get("limits"), dict) else {}
        )
        leverage_limits = (
            limits.get("leverage")
            if isinstance(limits.get("leverage"), dict)
            else {}
        )
        value = info.get("maxLeverage", leverage_limits.get("max"))
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if cls._finite_positive(number) else None

    @classmethod
    def _position_reference_price(
        cls,
        ticker: dict[str, Any],
        microstructure: dict[str, Any],
    ) -> tuple[float | None, str | None]:
        """Choose a live price without inventing or carrying stale values."""
        for field in ("mark", "last", "close"):
            value = ticker.get(field)
            if cls._finite_positive(value):
                return float(value), f"ticker.{field}"

        best_bid = microstructure.get("best_bid")
        best_ask = microstructure.get("best_ask")
        if cls._finite_positive(best_bid) and cls._finite_positive(best_ask):
            return (float(best_bid) + float(best_ask)) / 2.0, "orderbook.mid"

        return None, None

    @classmethod
    def _price_location_packet(cls, ticker: dict[str, Any]) -> dict[str, Any]:
        last = ticker.get("last") if isinstance(ticker, dict) else None
        vwap = ticker.get("vwap") if isinstance(ticker, dict) else None
        if not (cls._finite_positive(last) and cls._finite_positive(vwap)):
            return {
                "available": False,
                "reason": "same-contract price location unavailable",
            }
        last_value = float(last)
        vwap_value = float(vwap)
        return {
            "available": True,
            "last": last_value,
            "vwap": vwap_value,
            "below_vwap": last_value < vwap_value,
        }

    def _attach_live_liquidation_flow(
        self,
        metrics: dict[str, Any],
        *,
        exchange_name: str,
        mapped_symbol: str,
        now: float | None = None,
    ) -> None:
        getter = getattr(self.ws_manager, "get_realtime_liquidation_flow", None)
        if not callable(getter):
            metrics.pop("liquidation_flow", None)
            return
        flow = getter(exchange_name, mapped_symbol, now=now)
        if not isinstance(flow, dict) or flow.get("available") is not True:
            metrics.pop("liquidation_flow", None)
            return
        metrics["liquidation_flow"] = flow
        sources = metrics.get("data_sources")
        if isinstance(sources, dict):
            sources["liquidations"] = f"{exchange_name}:public_ws"

    def _position_setup_from_candle_capture(
        self,
        *,
        candle_results: dict[str, Any],
        ticker: dict[str, Any],
        microstructure: dict[str, Any],
        market_info: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        source_capture = (
            candle_results.get("source_capture")
            if isinstance(candle_results.get("source_capture"), dict)
            else {}
        )
        primary = (
            source_capture.get("primary_closed_ohlcv")
            if isinstance(source_capture.get("primary_closed_ohlcv"), dict)
            else {}
        )
        history = primary.get("5m") if isinstance(primary.get("5m"), list) else []
        capture = {
            "attempted": True,
            "timeframe": "5m",
            "requested_limit": len(history),
            "evaluated_at_ms": int(time.time() * 1000),
            "source": "candle_analysis.primary_closed_ohlcv.5m",
            "reused_existing_capture": True,
            "sample_count": len(history),
            "raw_ohlcv": history,
        }
        mark_price, mark_price_source = self._position_reference_price(
            ticker,
            microstructure,
        )
        reference = {
            "price": mark_price,
            "source": mark_price_source,
        }
        vwap_entry = microstructure.get("best_bid")
        if not history:
            return {"status": "REJECTED: Missing captured 5m history"}, capture, reference

        recent_high = None
        try:
            recent_high = max(
                float(row[2])
                for row in history[-24:]
                if isinstance(row, (list, tuple))
                and len(row) >= 6
                and isinstance(row[2], (int, float))
                and not isinstance(row[2], bool)
                and math.isfinite(float(row[2]))
                and float(row[2]) > 0
            )
        except (TypeError, ValueError, IndexError):
            recent_high = None

        setup = self.position_calculator.calculate_short_position(
            vwap_entry,
            recent_high=recent_high,
            market_info=market_info,
            mark_price=mark_price,
            entry_slippage_pct=microstructure.get("entry_slippage_pct"),
            exit_slippage_pct=microstructure.get("exit_slippage_pct"),
        )
        return setup, capture, reference

    @staticmethod
    def _technical_trade_plan_unavailable(reasons: list[str]) -> dict[str, Any]:
        return {
            "version": "technical_trade_plan_shadow_v1",
            "observational_only": True,
            "hard_gating_allowed": False,
            "trade_eligible": False,
            "available": False,
            "feasible": None,
            "status": "UNAVAILABLE",
            "reason": "required causal plan inputs unavailable",
            "unavailable_reasons": sorted(set(reasons)),
        }

    @classmethod
    def _captured_technical_history(
        cls,
        candle_results: dict[str, Any],
    ) -> list:
        source = candle_results.get("source_capture")
        primary = (
            source.get("primary_closed_ohlcv")
            if isinstance(source, dict)
            and isinstance(source.get("primary_closed_ohlcv"), dict)
            else {}
        )
        history = primary.get("5m")
        if not isinstance(history, list):
            return []
        window = history[-24:]
        if not window:
            return []
        return history if all(
            isinstance(row, (list, tuple))
            and len(row) >= 6
            and cls._finite_positive(row[2])
            for row in window
        ) else []

    @classmethod
    def _captured_technical_market(
        cls,
        microstructure: dict[str, Any],
    ) -> dict[str, Any] | None:
        source = microstructure.get("source_capture")
        market = source.get("market") if isinstance(source, dict) else None
        if not isinstance(market, dict):
            return None
        precision = market.get("precision")
        limits = market.get("limits")
        cost = limits.get("cost") if isinstance(limits, dict) else None
        required_values = (
            precision.get("price") if isinstance(precision, dict) else None,
            precision.get("amount") if isinstance(precision, dict) else None,
            market.get("contractSize"),
            cost.get("min") if isinstance(cost, dict) else None,
        )
        return market if all(cls._finite_positive(value) for value in required_values) else None

    @staticmethod
    def _finite_nonnegative(value: Any) -> bool:
        return bool(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0
        )

    def build_technical_trade_plan_shadow(
        self,
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """Calculate an observational plan with the canonical calculator only."""
        candle_results = metrics.get("candle_analysis")
        ticker = metrics.get("ticker")
        microstructure = metrics.get("microstructure")
        packet_checks = (
            ("CANDLE_PACKET", candle_results),
            ("TICKER_PACKET", ticker),
            ("MICROSTRUCTURE_PACKET", microstructure),
        )
        packet_reasons = [
            reason for reason, packet in packet_checks if not isinstance(packet, dict)
        ]
        if packet_reasons:
            return self._technical_trade_plan_unavailable(packet_reasons)

        history = self._captured_technical_history(candle_results)
        reference_price, _ = self._position_reference_price(ticker, microstructure)
        market_info = self._captured_technical_market(microstructure)
        causal_checks = (
            ("HISTORY", bool(history)),
            ("ENTRY_PRICE", self._finite_positive(microstructure.get("best_bid"))),
            ("REFERENCE_PRICE", self._finite_positive(reference_price)),
            ("ENTRY_SLIPPAGE", self._finite_nonnegative(microstructure.get("entry_slippage_pct"))),
            ("EXIT_SLIPPAGE", self._finite_nonnegative(microstructure.get("exit_slippage_pct"))),
            ("MARKET_FILTERS", market_info is not None),
        )
        unavailable_reasons = [
            reason for reason, available in causal_checks if not available
        ]
        if unavailable_reasons:
            return self._technical_trade_plan_unavailable(unavailable_reasons)
        setup, _, reference = self._position_setup_from_candle_capture(
            candle_results=candle_results,
            ticker=ticker,
            microstructure=microstructure,
            market_info=market_info,
        )
        calculator_status = str(setup.get("status") or "REJECTED: Missing calculator status")
        feasible = calculator_status == "READY"
        return {
            "version": "technical_trade_plan_shadow_v1",
            "observational_only": True,
            "hard_gating_allowed": False,
            "trade_eligible": False,
            "available": True,
            "feasible": feasible,
            "status": "FEASIBLE" if feasible else "INFEASIBLE",
            "calculator_status": calculator_status,
            "setup": setup,
            "reference": reference,
        }

    def _attach_position_setup_from_capture(
        self,
        metrics: dict[str, Any],
        *,
        status: str,
        candle_results: dict[str, Any],
        ticker: dict[str, Any],
        microstructure: dict[str, Any],
        market_info: dict[str, Any],
    ) -> str:
        if status not in {"FUEL-RICH", "PRE-TRIGGER", "ARMED", "TRIGGERED"}:
            return status
        setup, capture, reference = self._position_setup_from_candle_capture(
            candle_results=candle_results,
            ticker=ticker,
            microstructure=microstructure,
            market_info=market_info,
        )
        metrics.setdefault("source_capture", {})["position"] = capture
        metrics["position_reference_price"] = reference
        metrics["position_setup"] = setup
        if status in {"ARMED", "TRIGGERED"} and str(setup.get("status") or "").startswith("REJECTED"):
            return "WATCH"
        return status

    @staticmethod
    def _finite_number(value: Any) -> float | None:
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            return float(value)
        return None

    @staticmethod
    def _stage_chain_complete(stages: Dict[str, Any]) -> bool:
        return bool(
            isinstance(stages, dict)
            and stages.get("passed") is True
            and all(
                stages.get(stage) is True
                for stage in (
                    "hype",
                    "damage",
                    "setup",
                    "trigger",
                )
            )
        )

    async def _advance_stage_lifecycle(
        self,
        symbol: str,
        lifecycle_id: int | None,
        strategy_stages: Dict[str, Any],
    ) -> tuple[dict[str, Any] | None, bool]:
        lifecycle_store = getattr(self, "stage_lifecycle_store", None)
        if lifecycle_store is None or lifecycle_id is None:
            return None, False
        try:
            stage_lifecycle = await asyncio.to_thread(
                lifecycle_store.advance,
                symbol,
                int(lifecycle_id),
                strategy_stages,
            )
        except Exception as exc:
            logger.exception(
                "Stage lifecycle persistence unavailable for %s lifecycle %s",
                symbol,
                lifecycle_id,
            )
            return {
                "available": False,
                "stale": False,
                "lifecycle_id": int(lifecycle_id),
                "confirmed": {},
                "reason": "stage lifecycle persistence unavailable",
                "error_type": type(exc).__name__,
                "observational_only": True,
                "hard_gating_allowed": False,
            }, False

        confirmed = (
            stage_lifecycle.get("confirmed")
            if isinstance(stage_lifecycle, dict)
            and isinstance(stage_lifecycle.get("confirmed"), dict)
            else {}
        )
        persisted_complete = bool(
            isinstance(stage_lifecycle, dict)
            and stage_lifecycle.get("available") is True
            and stage_lifecycle.get("stale") is False
            and int(stage_lifecycle.get("lifecycle_id") or -1) == int(lifecycle_id)
            and confirmed.get("passed") is True
            and strategy_stages.get("trigger") is True
            and stage_lifecycle.get("observational_only") is False
            and stage_lifecycle.get("hard_gating_allowed") is True
        )
        return stage_lifecycle, persisted_complete

    @staticmethod
    def _observational_status(stages: Dict[str, Any]) -> str:
        """
        Transitional bridge only.

        This is intentionally not the authoritative v3 classifier.
        """
        if not isinstance(stages, dict):
            return "WATCH"

        hype = stages.get("hype") is True
        damage = stages.get("damage") is True
        setup = stages.get("setup") is True
        trigger = stages.get("trigger") is True

        if hype and (damage or setup or trigger):
            return "PRE-TRIGGER"

        if hype:
            return "FUEL-RICH"

        return "WATCH"

    @classmethod
    def _experimental_pretrigger_eligible(
        cls,
        *,
        enabled: bool,
        threshold: float,
        observation_score: Any,
        observation_status: str,
        strategy_stages: Dict[str, Any],
        quality_gates: Dict[str, Any],
        microstructure: Dict[str, Any],
        derivatives: Dict[str, Any],
    ) -> bool:
        required_gates = (
            "all_timeframes_valid",
            "complete_candle_packet",
            "complete_microstructure_packet",
            "complete_fresh_derivatives_packet",
            "taker_sell_dominance",
            "complete_price_location",
            "live_orderbook",
        )
        return bool(
            enabled
            and isinstance(observation_score, (int, float))
            and not isinstance(observation_score, bool)
            and math.isfinite(float(observation_score))
            and float(observation_score) >= float(threshold)
            and observation_status == "PRE-TRIGGER"
            and strategy_stages.get("hype") is True
            and strategy_stages.get("trigger") is True
            and microstructure.get("approved") is True
            and derivatives.get("available") is True
            and all(quality_gates.get(name) is True for name in required_gates)
        )

    @classmethod
    def _relative_return_features(
        cls,
        candidate_details: dict,
        benchmark_details: dict,
    ) -> dict:
        """
        Measure candidate return relative to BTC.

        Negative relative return means the candidate underperformed BTC.

        This method intentionally applies no threshold, state transition,
        or trading decision.
        """
        result: dict[str, Any] = {
            "available": False,
            "benchmark": "BTC",
            "timeframes": {},
        }

        available_pairs = 0

        for timeframe in (
            "4h",
            "1h",
            "15m",
            "5m",
        ):
            candidate = candidate_details.get(timeframe)
            benchmark = benchmark_details.get(timeframe)

            if not isinstance(candidate, dict):
                continue

            if not isinstance(benchmark, dict):
                continue

            timeframe_result: dict[str, Any] = {}

            for bars in (3, 6, 12):
                field = f"return_{bars}bars_pct"

                candidate_return = cls._finite_number(
                    candidate.get(field)
                )

                benchmark_return = cls._finite_number(
                    benchmark.get(field)
                )

                relative_return = None

                if (
                    candidate_return is not None
                    and benchmark_return is not None
                ):
                    relative_return = round(
                        candidate_return - benchmark_return,
                        4,
                    )

                    available_pairs += 1

                timeframe_result[field] = (
                    candidate_return
                )

                timeframe_result[
                    f"benchmark_{field}"
                ] = benchmark_return

                timeframe_result[
                    f"relative_{field}"
                ] = relative_return

            result["timeframes"][
                timeframe
            ] = timeframe_result

        result["available"] = available_pairs > 0
        result["available_pairs"] = available_pairs

        return result

    async def _benchmark_context(
        self,
        exchange: Any,
    ) -> dict:
        """
        Fetch BTC perpetual candle features from the same selected venue.

        Benchmark data is strictly observational.

        Failure of this auxiliary measurement layer must never invalidate
        an otherwise valid candidate analysis.
        """
        exchange_name = str(
            getattr(
                exchange,
                "id",
                "unknown",
            )
        )

        now = time.time()

        cache = getattr(
            self,
            "_benchmark_cache",
            None,
        )

        if not isinstance(cache, dict):
            cache = {}
            self._benchmark_cache = cache

        cached = cache.get(
            exchange_name
        )

        if cached is not None:
            cached_at, cached_packet = cached

            if (
                isinstance(cached_at, (int, float))
                and now - float(cached_at)
                <= self.benchmark_cache_ttl_seconds
            ):
                return cached_packet

        gateway = getattr(
            self,
            "gateway",
            None,
        )

        analyzer = getattr(
            self,
            "candle_analyzer",
            None,
        )

        map_symbol = getattr(
            gateway,
            "_map_symbol",
            None,
        )

        analyzer_timeframes = getattr(
            analyzer,
            "timeframes",
            None,
        )

        analyzer_candle_limit = getattr(
            analyzer,
            "candle_limit",
            None,
        )

        closed_candles = getattr(
            analyzer,
            "_closed_candles",
            None,
        )

        evaluate = getattr(
            analyzer,
            "_evaluate",
            None,
        )

        if (
            not callable(map_symbol)
            or not isinstance(
                analyzer_timeframes,
                (tuple, list),
            )
            or not analyzer_timeframes
            or not isinstance(
                analyzer_candle_limit,
                int,
            )
            or analyzer_candle_limit <= 0
            or not callable(closed_candles)
            or not callable(evaluate)
        ):
            return {
                "available": False,
                "reason": (
                    "benchmark infrastructure unavailable"
                ),
                "source_exchange": exchange_name,
                "mapped_symbol": None,
                "retrieved_at": now,
                "details": {},
            }

        markets = getattr(
            exchange,
            "markets",
            {},
        )

        if not isinstance(markets, dict):
            return {
                "available": False,
                "reason": (
                    "benchmark exchange markets unavailable"
                ),
                "source_exchange": exchange_name,
                "mapped_symbol": None,
                "retrieved_at": now,
                "details": {},
            }

        mapped_symbol = map_symbol(
            self.benchmark_symbol,
            markets,
        )

        if mapped_symbol is None:
            packet = {
                "available": False,
                "reason": (
                    "BTC benchmark unavailable "
                    "on selected exchange"
                ),
                "source_exchange": exchange_name,
                "mapped_symbol": None,
                "retrieved_at": now,
                "details": {},
            }

            cache[exchange_name] = (
                now,
                packet,
            )

            return packet

        fetch_ohlcv = getattr(
            exchange,
            "fetch_ohlcv",
            None,
        )

        if not callable(fetch_ohlcv):
            return {
                "available": False,
                "reason": (
                    "benchmark OHLCV interface unavailable"
                ),
                "source_exchange": exchange_name,
                "mapped_symbol": mapped_symbol,
                "retrieved_at": now,
                "details": {},
            }

        try:
            rows_by_timeframe = await asyncio.gather(
                *(
                    fetch_ohlcv(
                        mapped_symbol,
                        timeframe=timeframe,
                        limit=analyzer_candle_limit,
                    )
                    for timeframe
                    in analyzer_timeframes
                ),
                return_exceptions=True,
            )
        except Exception as exc:
            return {
                "available": False,
                "reason": (
                    "benchmark OHLCV collection failed: "
                    f"{type(exc).__name__}"
                ),
                "source_exchange": exchange_name,
                "mapped_symbol": mapped_symbol,
                "retrieved_at": now,
                "details": {},
            }

        details: dict[str, Any] = {}

        for timeframe, rows in zip(
            analyzer_timeframes,
            rows_by_timeframe,
        ):
            if isinstance(rows, Exception):
                details[timeframe] = {
                    "valid": False,
                    "reason": (
                        "benchmark OHLCV request failed"
                    ),
                }
                continue

            try:
                candles = closed_candles(
                    rows,
                    timeframe,
                )
            except Exception:
                candles = None

            if candles is None:
                details[timeframe] = {
                    "valid": False,
                    "reason": (
                        "benchmark OHLCV invalid or stale"
                    ),
                }
                continue

            try:
                evaluated = evaluate(
                    candles
                )
            except Exception:
                details[timeframe] = {
                    "valid": False,
                    "reason": (
                        "benchmark feature extraction failed"
                    ),
                }
                continue

            details[timeframe] = {
                "valid": True,
                "atr_pct": evaluated.get(
                    "atr_pct"
                ),
                "distance_to_support_atr": (
                    evaluated.get(
                        "distance_to_support_atr"
                    )
                ),
                "support_broken": (
                    evaluated.get(
                        "support_broken"
                    )
                ),
                "lower_high": evaluated.get(
                    "lower_high"
                ),
                "return_3bars_pct": (
                    evaluated.get(
                        "return_3bars_pct"
                    )
                ),
                "return_6bars_pct": (
                    evaluated.get(
                        "return_6bars_pct"
                    )
                ),
                "return_12bars_pct": (
                    evaluated.get(
                        "return_12bars_pct"
                    )
                ),
            }

        available = all(
            isinstance(
                details.get(timeframe),
                dict,
            )
            and details[
                timeframe
            ].get("valid") is True
            for timeframe
            in analyzer_timeframes
        )

        packet = {
            "available": available,
            "reason": (
                None
                if available
                else (
                    "incomplete BTC benchmark "
                    "candle packet"
                )
            ),
            "source_exchange": exchange_name,
            "mapped_symbol": mapped_symbol,
            "retrieved_at": now,
            "details": details,
        }

        cache[exchange_name] = (
            now,
            packet,
        )

        return packet

    def _merge_score_v2(
        self,
        *,
        candles: dict,
        microstructure: dict,
        derivatives: dict,
        cross_exchange_confirmed: bool,
        ticker: dict,
        reference_price: float,
        strategy_stages: Dict[str, Any],
        persisted_stage_chain_complete: bool = False,
    ) -> dict:
        del reference_price

        last = (
            ticker.get("last")
            if isinstance(ticker, dict)
            else None
        )

        vwap = (
            ticker.get("vwap")
            if isinstance(ticker, dict)
            else None
        )

        location_packet = self._price_location_packet(ticker)
        price_location = (
            {"below_vwap": location_packet["below_vwap"]}
            if location_packet.get("available") is True
            else {}
        )

        score_result = ScoreV2().evaluate(
            candles,
            microstructure,
            derivatives,
            cross_exchange_confirmed is True,
            price_location,
        )

        quality_gates = {
            **score_result["gates"],
            "channel_stage_chain": (
                self._stage_chain_complete(strategy_stages)
                or (
                    persisted_stage_chain_complete is True
                    and strategy_stages.get("trigger") is True
                )
            ),
        }

        if not quality_gates[
            "channel_stage_chain"
        ]:
            return {
                "score_version": ScoreV2.version,
                "is_valid": False,
                "score": None,
                "score_components": {},
                "quality_gates": (
                    quality_gates
                ),
                "reason": (
                    "channel stage chain incomplete"
                ),
            }

        return {
            "score_version": (
                score_result[
                    "score_version"
                ]
            ),
            "is_valid": (
                score_result[
                    "is_valid"
                ]
            ),
            "score": (
                score_result[
                    "score"
                ]
            ),
            "score_components": (
                score_result[
                    "components"
                ]
            ),
            "quality_gates": (
                quality_gates
            ),
            "reason": (
                score_result[
                    "reason"
                ]
            ),
        }

    def _watch_score(
        self,
        *,
        candles: dict,
        microstructure: dict,
        derivatives: dict,
        cross_exchange_confirmed: bool | None,
        ticker: dict,
    ) -> dict:
        last = (
            ticker.get("last")
            if isinstance(ticker, dict)
            else None
        )

        vwap = (
            ticker.get("vwap")
            if isinstance(ticker, dict)
            else None
        )

        location_packet = self._price_location_packet(ticker)
        price_location = (
            {"below_vwap": location_packet["below_vwap"]}
            if location_packet.get("available") is True
            else {}
        )

        return ScoreV2().evaluate_watch(
            candles,
            microstructure,
            derivatives,
            cross_exchange_confirmed,
            price_location,
        )

    def _suggested_status(
        self,
        score: float,
        stages: Dict[str, Any],
        microstructure_approved: bool,
        cross_exchange_confirmed: bool,
        persisted_stage_chain_complete: bool = False,
    ) -> str:
        chain_complete = (
            self._stage_chain_complete(stages)
            or (
                persisted_stage_chain_complete is True
                and stages.get("trigger") is True
            )
        )
        if not (
            chain_complete
            and microstructure_approved
            and cross_exchange_confirmed
        ):
            return "WATCH"

        if score >= self.triggered_threshold:
            return "TRIGGERED"

        if score >= self.armed_threshold:
            return "ARMED"

        return "WATCH"

    async def resolve_live_reference(
        self,
        symbol: str,
    ) -> Dict[str, Any]:
        result = (
            await self.gateway.fetch_ticker(
                symbol
            )
        )

        ticker = (
            result.get("data")
            if result
            else None
        )

        price = (
            ticker.get("last")
            if isinstance(
                ticker,
                dict,
            )
            else None
        )

        if (
            not isinstance(
                price,
                (int, float),
            )
            or price <= 0
        ):
            return {}

        quote_volume = (
            ticker.get("quoteVolume")
            if isinstance(
                ticker,
                dict,
            )
            else None
        )

        return {
            "price": float(price),
            "exchange": (
                result["exchange"]
            ),
            "mapped_symbol": (
                result[
                    "mapped_symbol"
                ]
            ),
            "quote_volume": (
                float(quote_volume)
                if (
                    isinstance(
                        quote_volume,
                        (int, float),
                    )
                    and quote_volume >= 0
                )
                else None
            ),
        }

    @staticmethod
    def _requires_source_fallback(
        microstructure: Dict[str, Any],
    ) -> bool:
        reason = str(
            microstructure.get(
                "reason",
                "",
            )
        )

        return reason.startswith(
            (
                "missing ",
                "empty ",
                "stale ",
                (
                    "orderbook receipt timestamp "
                    "unavailable"
                ),
                (
                    "insufficient fresh trades"
                ),
                (
                    "invalid exchange filters"
                ),
            )
        )

    async def _derivatives_context(
        self,
        symbol: str,
        reference_price: float,
        selected_exchange: str,
        selected_symbol: str,
        selected_instance: Any,
    ) -> dict:
        attempted: list[
            dict[str, Any]
        ] = []

        coinglass_candidates: list[
            tuple[str, str, Any]
        ] = []

        def record_failure(
            exchange_name: str,
            mapped_symbol: str | None,
            market_id: str | None,
            retrieved_at: float | None,
            reason: str,
        ) -> None:
            attempted.append(
                {
                    "exchange": (
                        exchange_name
                    ),
                    "mapped_symbol": (
                        mapped_symbol
                    ),
                    "market_id": (
                        market_id
                    ),
                    "retrieved_at": (
                        retrieved_at
                    ),
                    "reason": reason,
                }
            )

        async def inspect(
            exchange_name: str,
            mapped_symbol: str,
            exchange_instance: Any,
        ) -> dict | None:
            context = (
                await self.gateway
                .fetch_derivatives_context(
                    exchange_instance,
                    mapped_symbol,
                    self.derivatives,
                )
            )

            if context.get(
                "available"
            ):
                context[
                    "fallback_attempts"
                ] = list(
                    attempted
                )

                return context

            record_failure(
                exchange_name,
                context.get(
                    "mapped_symbol",
                    mapped_symbol,
                ),
                context.get(
                    "market_id"
                ),
                context.get(
                    "retrieved_at"
                ),
                str(
                    context.get(
                        "reason",
                        (
                            "derivatives "
                            "unavailable"
                        ),
                    )
                ),
            )
            if context.get("source_capture") is not None:
                attempted[-1]["source_capture"] = context.get("source_capture")

            return None

        if (
            selected_exchange
            == "binance"
        ):
            context = await inspect(
                selected_exchange,
                selected_symbol,
                selected_instance,
            )

            if context is not None:
                return context

            coinglass_candidates.append(
                (
                    selected_exchange,
                    selected_symbol,
                    selected_instance,
                )
            )

        else:
            try:
                binance = (
                    await self.gateway
                    ._get_exchange(
                        "binance"
                    )
                )

            except Exception as exc:
                record_failure(
                    "binance",
                    None,
                    None,
                    None,
                    (
                        "source unavailable: "
                        f"{type(exc).__name__}"
                    ),
                )
            else:
                if not (
                    self.gateway
                    ._markets_loaded
                    .get("binance")
                ):
                    record_failure(
                        "binance",
                        None,
                        None,
                        None,
                        (
                            "markets "
                            "unavailable"
                        ),
                    )

                else:
                    binance_symbol = (
                        self.gateway
                        ._map_symbol(
                            symbol,
                            binance.markets,
                        )
                    )

                    if (
                        binance_symbol
                        is None
                    ):
                        record_failure(
                            "binance",
                            None,
                            None,
                            None,
                            (
                                "no active USDT-linear "
                                "perpetual mapping"
                            ),
                        )

                    else:
                        market = (
                            binance.markets[
                                binance_symbol
                            ]
                        )

                        market_id = (
                            market.get(
                                "id"
                            )
                        )

                        try:
                            ticker = (
                                await asyncio
                                .wait_for(
                                    binance
                                    .fetch_ticker(
                                        binance_symbol
                                    ),
                                    timeout=(
                                        self.gateway
                                        .derivatives_request_timeout_seconds
                                    ),
                                )
                            )

                        except Exception as exc:
                            record_failure(
                                "binance",
                                binance_symbol,
                                market_id,
                                None,
                                (
                                    "price compatibility "
                                    "check unavailable: "
                                    f"{type(exc).__name__}"
                                ),
                            )

                        else:
                            if not (
                                self.gateway
                                ._price_is_compatible(
                                    ticker,
                                    reference_price,
                                    (
                                        self
                                        .max_cross_exchange_deviation_pct
                                    ),
                                )
                            ):
                                record_failure(
                                    "binance",
                                    binance_symbol,
                                    market_id,
                                    None,
                                    (
                                        "price incompatible "
                                        "with reference"
                                    ),
                                )

                            else:
                                context = (
                                    await inspect(
                                        "binance",
                                        binance_symbol,
                                        binance,
                                    )
                                )

                                if (
                                    context
                                    is not None
                                ):
                                    return (
                                        context
                                    )

                                coinglass_candidates.append(
                                    (
                                        "binance",
                                        binance_symbol,
                                        binance,
                                    )
                                )

            context = await inspect(
                selected_exchange,
                selected_symbol,
                selected_instance,
            )

            if context is not None:
                return context

            coinglass_candidates.append(
                (
                    selected_exchange,
                    selected_symbol,
                    selected_instance,
                )
            )

        coinglass_client = getattr(
            self,
            "coinglass",
            None,
        )

        if (
            coinglass_client
            is not None
        ):
            for (
                exchange_name,
                mapped_symbol,
                exchange_instance,
            ) in coinglass_candidates:
                selected_market = (
                    getattr(
                        exchange_instance,
                        "markets",
                        {},
                    )
                    .get(
                        mapped_symbol,
                        {},
                    )
                )

                coinglass = (
                    await coinglass_client
                    .fetch_packet(
                        exchange=(
                            exchange_name
                        ),
                        mapped_symbol=(
                            mapped_symbol
                        ),
                        market_id=(
                            selected_market.get(
                                "id",
                                "",
                            )
                            if isinstance(
                                selected_market,
                                dict,
                            )
                            else ""
                        ),
                        analyzer=(
                            self.derivatives
                        ),
                    )
                )

                if coinglass.get(
                    "available"
                ):
                    coinglass[
                        "fallback_attempts"
                    ] = attempted

                    return coinglass

                record_failure(
                    str(
                        coinglass.get(
                            "source_exchange",
                            "coinglass",
                        )
                    ),
                    coinglass.get(
                        "mapped_symbol"
                    ),
                    coinglass.get(
                        "market_id"
                    ),
                    coinglass.get(
                        "retrieved_at"
                    ),
                    str(
                        coinglass.get(
                            "reason",
                            (
                                "CoinGlass "
                                "derivatives "
                                "unavailable"
                            ),
                        )
                    ),
                )
                if coinglass.get("source_capture") is not None:
                    attempted[-1]["source_capture"] = coinglass.get("source_capture")

        return {
            "available": False,
            "reason": (
                "no complete live derivatives "
                "data source in exchange waterfall"
            ),
            "source_exchange": None,
            "mapped_symbol": None,
            "market_id": None,
            "retrieved_at": None,
            "fallback_attempts": (
                attempted
            ),
        }

    async def watch_prefilter_waterfall(
        self,
        symbol: str,
        reference_price: float,
        *,
        minimum_no_hype_sources: int = 2,
    ) -> Dict[str, Any]:
        """Cheap 4h-only WATCH classifier across compatible venues.

        It can only keep a candidate in WATCH. Any observed hype or absence of
        valid 4h evidence is fail-closed to ``cross_check_symbol``. No
        microstructure, derivatives, entry gate, or promotion is evaluated here.
        """
        started_at = time.monotonic()
        required_no_hype = max(1, int(minimum_no_hype_sources))
        sources_checked = 0
        valid_no_hype = 0
        first_valid: Dict[str, Any] | None = None
        first_probe: Dict[str, Any] | None = None
        invalid_reasons: list[dict[str, str]] = []

        async for source in self.gateway.compatible_market_sources(
            symbol,
            reference_price=reference_price,
            max_deviation_pct=self.max_cross_exchange_deviation_pct,
            realtime_ticker_getter=getattr(
                self.ws_manager, "get_realtime_ticker", None
            ),
        ):
            sources_checked += 1
            exchange_name = str(source.get("exchange") or "")
            mapped_symbol = str(source.get("mapped_symbol") or "")
            exchange = source.get("exchange_instance")
            try:
                probe = await self.candle_analyzer.analyze_hype_context(
                    exchange, mapped_symbol
                )
            except Exception as exc:
                invalid_reasons.append({
                    "exchange": exchange_name,
                    "reason": f"4h prefilter unavailable: {type(exc).__name__}",
                })
                continue
            if not isinstance(probe, dict) or probe.get("valid") is not True:
                invalid_reasons.append({
                    "exchange": exchange_name,
                    "reason": str(
                        probe.get("reason") if isinstance(probe, dict) else "invalid 4h prefilter"
                    ),
                })
                continue
            if probe.get("hype_context") is True:
                return {
                    "conclusive": True,
                    "requires_full_validation": True,
                    "reason": "watch prefilter detected hype context",
                    "result": None,
                }
            valid_no_hype += 1
            if first_valid is None:
                first_valid = dict(source)
                first_probe = dict(probe)

        if invalid_reasons:
            return {
                "conclusive": False,
                "requires_full_validation": True,
                "reason": "watch prefilter has incomplete 4h coverage",
                "result": None,
            }
        if (
            valid_no_hype < required_no_hype
            or first_valid is None
            or first_probe is None
        ):
            return {
                "conclusive": False,
                "requires_full_validation": True,
                "reason": "watch prefilter has no valid 4h coverage",
                "result": None,
            }

        elapsed = max(0.0, time.monotonic() - started_at)
        details = first_probe.get("details")
        candle_analysis = {
            "is_breakdown_confirmed": False,
            "breakdown_score": 0,
            "cross_exchange_confirmed": False,
            "details": {"4h": details} if isinstance(details, dict) else {},
            "source_capture": dict(first_probe.get("source_capture") or {}),
        }
        strategy_stages = {
            "hype": False,
            "damage": False,
            "setup": False,
            "setup_type": None,
            "trigger": False,
            "passed": False,
        }
        result = {
            "is_valid": False,
            "score": None,
            "suggested_status": "REJECTED",
            "observation_status": "WATCH",
            "observation_score": None,
            "metrics": {
                "exchange": first_valid.get("exchange"),
                "mapped_symbol": first_valid.get("mapped_symbol"),
                "ticker": first_valid.get("data"),
                "candle_analysis": candle_analysis,
                "valid_candle_timeframes": 1,
                "strategy_stages": strategy_stages,
                "trade_eligible": False,
                "analysis_reason": "watch 4h waterfall prefilter: no hype context",
                "watch_prefilter": {
                    "version": "watch_prefilter_v2_4h_waterfall",
                    "observational_only": True,
                    "hard_gating_allowed": False,
                    "conclusive_no_hype": True,
                    "valid_no_hype_sources": valid_no_hype,
                    "sources_checked": sources_checked,
                    "minimum_no_hype_sources": required_no_hype,
                    "invalid_sources": invalid_reasons,
                },
            },
            "_runtime_diagnostics": {
                "source_attempts": sources_checked,
                "ws_evidence_hits": 0,
                "rest_evidence_fallbacks": 0,
                "outcome": "complete",
                "stage_durations_seconds": {
                    "candles": round(elapsed, 6),
                    "total": round(elapsed, 6),
                },
            },
        }
        return {
            "conclusive": True,
            "requires_full_validation": False,
            "reason": "watch 4h waterfall prefilter conclusively found no hype context",
            "result": result,
        }

    async def watch_prefilter(
        self,
        symbol: str,
        reference_price: float,
        *,
        exchange_name: str,
        mapped_symbol: str,
    ) -> Dict[str, Any]:
        """4h-only WATCH probe on the lifecycle-fenced last full source.

        This path can only keep a candidate in WATCH. Any hype, incomplete data,
        incompatible source, or provider failure requires the canonical full
        validator before a promotion can be persisted.
        """
        started_at = time.monotonic()

        def full_required(reason: str, *, conclusive: bool = False) -> Dict[str, Any]:
            return {
                "conclusive": conclusive,
                "requires_full_validation": True,
                "reason": reason,
                "result": None,
            }

        if exchange_name not in self.gateway.priority_chain:
            return full_required("previous full-validation exchange unavailable")
        if not isinstance(mapped_symbol, str) or not mapped_symbol.strip():
            return full_required("previous full-validation symbol unavailable")

        try:
            exchange = await self.gateway._get_exchange(exchange_name)
            if not self.gateway._markets_loaded.get(exchange_name, False):
                return full_required("previous full-validation market metadata unavailable")
            if mapped_symbol not in getattr(exchange, "markets", {}):
                return full_required("previous full-validation market unavailable")

            ticker = self.ws_manager.get_realtime_ticker(exchange_name, mapped_symbol)
            if not self.gateway._price_is_compatible(
                ticker if isinstance(ticker, dict) else {},
                reference_price,
                self.max_cross_exchange_deviation_pct,
            ):
                ticker = await exchange.fetch_ticker(mapped_symbol)
            if not self.gateway._price_is_compatible(
                ticker if isinstance(ticker, dict) else {},
                reference_price,
                self.max_cross_exchange_deviation_pct,
            ):
                return full_required("previous full-validation source price incompatible")

            hype_probe = await self.candle_analyzer.analyze_hype_context(
                exchange, mapped_symbol
            )
        except Exception as exc:
            return full_required(f"watch prefilter unavailable: {type(exc).__name__}")

        if not isinstance(hype_probe, dict) or hype_probe.get("valid") is not True:
            return full_required("watch prefilter incomplete completed 4h OHLCV")
        if hype_probe.get("hype_context") is True:
            return full_required(
                "watch prefilter detected hype context", conclusive=True
            )

        elapsed = max(0.0, time.monotonic() - started_at)
        details = hype_probe.get("details")
        candle_analysis = {
            "is_breakdown_confirmed": False,
            "breakdown_score": 0,
            "cross_exchange_confirmed": False,
            "details": {"4h": details} if isinstance(details, dict) else {},
            "source_capture": dict(hype_probe.get("source_capture") or {}),
        }
        strategy_stages = {
            "hype": False,
            "damage": False,
            "setup": False,
            "setup_type": None,
            "trigger": False,
            "passed": False,
        }
        result = {
            "is_valid": False,
            "score": None,
            "suggested_status": "REJECTED",
            "observation_status": "WATCH",
            "observation_score": None,
            "metrics": {
                "exchange": exchange_name,
                "mapped_symbol": mapped_symbol,
                "ticker": ticker,
                "candle_analysis": candle_analysis,
                "valid_candle_timeframes": 1,
                "strategy_stages": strategy_stages,
                "trade_eligible": False,
                "analysis_reason": "watch 4h source prefilter: no hype context",
                "watch_prefilter": {
                    "version": "watch_prefilter_v2_4h_source",
                    "observational_only": True,
                    "hard_gating_allowed": False,
                    "conclusive_no_hype": True,
                },
            },
            "_runtime_diagnostics": {
                "source_attempts": 1,
                "ws_evidence_hits": 0,
                "rest_evidence_fallbacks": 0,
                "outcome": "complete",
                "stage_durations_seconds": {
                    "candles": round(elapsed, 6),
                    "total": round(elapsed, 6),
                },
            },
        }
        return {
            "conclusive": True,
            "requires_full_validation": False,
            "reason": "watch prefilter conclusively found no hype context",
            "result": result,
        }

    async def cross_check_symbol(
        self,
        symbol: str,
        reference_price: float,
        reference_source: str = "lbank",
        lifecycle_id: int | None = None,
    ) -> Dict[str, Any]:
        source_failures = []
        selected = None
        runtime_started_at = time.monotonic()
        runtime_diagnostics: dict[str, Any] = {
            "source_attempts": 0,
            "ws_evidence_hits": 0,
            "rest_evidence_fallbacks": 0,
            "outcome": "unavailable",
            "stage_durations_seconds": {},
        }

        def add_stage_duration(stage: str, started_at: float) -> None:
            elapsed = max(0.0, time.monotonic() - started_at)
            stages = runtime_diagnostics["stage_durations_seconds"]
            stages[stage] = round(float(stages.get(stage, 0.0)) + elapsed, 6)

        def finish_runtime(result: Dict[str, Any], *, outcome: str) -> Dict[str, Any]:
            runtime_diagnostics["outcome"] = outcome
            runtime_diagnostics["stage_durations_seconds"]["total"] = round(
                max(0.0, time.monotonic() - runtime_started_at), 6
            )
            result["_runtime_diagnostics"] = {
                "source_attempts": int(runtime_diagnostics["source_attempts"]),
                "ws_evidence_hits": int(runtime_diagnostics["ws_evidence_hits"]),
                "rest_evidence_fallbacks": int(runtime_diagnostics["rest_evidence_fallbacks"]),
                "outcome": str(runtime_diagnostics["outcome"]),
                "stage_durations_seconds": dict(runtime_diagnostics["stage_durations_seconds"]),
            }
            return result

        async for wf_result in (
            self.gateway
            .compatible_market_sources(
                symbol,
                reference_price=(
                    reference_price
                ),
                max_deviation_pct=(
                    self
                    .max_cross_exchange_deviation_pct
                ),
                realtime_ticker_getter=getattr(
                    self.ws_manager,
                    "get_realtime_ticker",
                    None,
                ),
            )
        ):
            runtime_diagnostics["source_attempts"] += 1
            ticker = (
                wf_result["data"]
            )

            ex_name = (
                wf_result[
                    "exchange"
                ]
            )

            mapped_sym = (
                wf_result[
                    "mapped_symbol"
                ]
            )

            ex_instance = (
                wf_result[
                    "exchange_instance"
                ]
            )

            if not self._finite_positive(
                ticker.get("last")
                if isinstance(
                    ticker,
                    dict,
                )
                else None
            ):
                source_failures.append(
                    {
                        "exchange": (
                            ex_name
                        ),
                        "reason": (
                            "invalid price"
                        ),
                    }
                )
                continue

            microstructure_started_at = time.monotonic()
            orderbook = (
                self.ws_manager
                .get_realtime_orderbook(
                    ex_name,
                    mapped_sym,
                )
            )

            if not orderbook:
                try:
                    orderbook = (
                        await ex_instance
                        .fetch_order_book(
                            mapped_sym,
                            limit=20,
                        )
                    )

                except Exception as exc:
                    source_failures.append(
                        {
                            "exchange": (
                                ex_name
                            ),
                            "reason": (
                                "orderbook "
                                "unavailable: "
                                f"{type(exc).__name__}"
                            ),
                        }
                    )
                    continue

            if (
                not orderbook.get(
                    "bids"
                )
                or not orderbook.get(
                    "asks"
                )
            ):
                source_failures.append(
                    {
                        "exchange": (
                            ex_name
                        ),
                        "reason": (
                            "empty orderbook"
                        ),
                    }
                )
                continue

            market_info = (
                ex_instance
                .markets
                .get(
                    mapped_sym,
                    {},
                )
            )

            sample_getter = getattr(
                self.ws_manager,
                "get_realtime_orderbook_samples",
                None,
            )
            trade_getter = getattr(
                self.ws_manager,
                "get_realtime_trades",
                None,
            )
            preloaded_snapshots = (
                sample_getter(
                    ex_name,
                    mapped_sym,
                    count=3,
                    min_span_seconds=(
                        2.0 * float(getattr(self.microstructure, "snapshot_delay_seconds", 0.25))
                    ),
                )
                if callable(sample_getter)
                else None
            )
            preloaded_trades = (
                trade_getter(ex_name, mapped_sym)
                if callable(trade_getter)
                else None
            )
            preload_checker = getattr(
                self.microstructure,
                "_preloaded_evidence_is_usable",
                None,
            )
            preloaded_usable = False
            if callable(preload_checker):
                try:
                    preloaded_usable = bool(
                        preload_checker(
                            preloaded_snapshots,
                            preloaded_trades,
                            now=time.time(),
                        )
                    )
                except Exception:
                    preloaded_usable = False
            if preloaded_usable:
                runtime_diagnostics["ws_evidence_hits"] += 1
            else:
                runtime_diagnostics["rest_evidence_fallbacks"] += 1

            microstructure = (
                await self
                .microstructure
                .analyze(
                    ex_instance,
                    mapped_sym,
                    orderbook,
                    market_info,
                    preloaded_snapshots=preloaded_snapshots,
                    preloaded_trades=preloaded_trades,
                )
            )
            add_stage_duration("microstructure", microstructure_started_at)

            if (
                self
                ._requires_source_fallback(
                    microstructure
                )
            ):
                source_failures.append(
                    {
                        "exchange": (
                            ex_name
                        ),
                        "reason": (
                            microstructure
                            .get(
                                "reason"
                            )
                        ),
                    }
                )
                continue

            candles_started_at = time.monotonic()
            (
                confirmation_exchange,
                confirmation_symbol,
            ) = (
                await self.gateway
                .get_confirmation_exchange(
                    symbol,
                    ex_name,
                    reference_price=(
                        reference_price
                    ),
                    max_deviation_pct=(
                        self
                        .max_cross_exchange_deviation_pct
                    ),
                )
            )

            candle_results = (
                await self
                .candle_analyzer
                .analyze_candles(
                    ex_instance,
                    mapped_sym,
                    confirmation_exchange,
                    confirmation_symbol,
                )
            )
            add_stage_duration("candles", candles_started_at)

            candle_details = (
                candle_results.get(
                    "details",
                    {},
                )
            )

            valid_timeframes = sum(
                bool(
                    candle_details
                    .get(
                        timeframe,
                        {},
                    )
                    .get(
                        "valid"
                    )
                )
                for timeframe in (
                    self.candle_analyzer
                    .timeframes
                )
            )

            if (
                valid_timeframes
                != len(
                    self.candle_analyzer
                    .timeframes
                )
            ):
                source_failures.append(
                    {
                        "exchange": (
                            ex_name
                        ),
                        "reason": (
                            "incomplete completed "
                            "OHLCV"
                        ),
                    }
                )
                continue

            metrics = {
                "orderbook": (
                    orderbook
                ),
                "ticker": ticker,
                (
                    "selected_quote_"
                    "volume_usdt"
                ): (
                    float(
                        ticker[
                            "quoteVolume"
                        ]
                    )
                    if self
                    ._finite_positive(
                        ticker.get(
                            "quoteVolume"
                        )
                    )
                    else None
                ),
                "mapped_symbol": (
                    mapped_sym
                ),
                "exchange": (
                    ex_name
                ),
                "market_constraints": {
                    "maximum_leverage": self._market_maximum_leverage(
                        market_info
                    ),
                },
                "data_sources": {
                    "reference": (
                        reference_source
                    ),
                    (
                        "ticker_orderbook_"
                        "candles_trades"
                    ): ex_name,
                    "confirmation": (
                        getattr(
                            confirmation_exchange,
                            "id",
                            None,
                        )
                    ),
                    "primary_exchange": ex_name,
                    "primary_symbol": mapped_sym,
                    "confirmation_exchange": (
                        getattr(
                            confirmation_exchange,
                            "id",
                            None,
                        )
                    ),
                    "confirmation_symbol": confirmation_symbol,
                    "fallback_attempts": (
                        source_failures
                    ),
                },
                "candle_analysis": (
                    candle_results
                ),
                (
                    "valid_candle_"
                    "timeframes"
                ): valid_timeframes,
                "microstructure": (
                    microstructure
                ),
            }

            selected = (
                metrics,
                ticker,
                orderbook,
                ex_instance,
                mapped_sym,
                market_info,
                candle_results,
                microstructure,
            )

            break

        if selected is None:
            return finish_runtime(
                {
                    "is_valid": False,
                    "score": None,
                    "suggested_status": "REJECTED",
                    "metrics": {
                        "error": (
                            "no complete live USDT perpetual data source in "
                            "exchange waterfall"
                        ),
                        "max_cross_exchange_deviation_pct": (
                            self.max_cross_exchange_deviation_pct
                        ),
                        "source_failures": source_failures,
                    },
                },
                outcome="unavailable",
            )

        (
            metrics,
            ticker,
            orderbook,
            ex_instance,
            mapped_sym,
            market_info,
            candle_results,
            microstructure,
        ) = selected

        metrics["price_location"] = self._price_location_packet(ticker)

        context_started_at = time.monotonic()
        derivatives, benchmark = (
            await asyncio.gather(
                self._derivatives_context(
                    symbol,
                    reference_price,
                    metrics[
                        "exchange"
                    ],
                    mapped_sym,
                    ex_instance,
                ),
                self._benchmark_context(
                    ex_instance
                ),
            )
        )
        add_stage_duration("context", context_started_at)

        metrics[
            "derivatives"
        ] = derivatives

        metrics[
            "benchmark_context"
        ] = benchmark

        metrics[
            "data_sources"
        ][
            "derivatives"
        ] = (
            derivatives.get(
                "source_exchange"
            )
            if derivatives.get(
                "available"
            )
            else None
        )

        metrics[
            "data_sources"
        ][
            "benchmark"
        ] = (
            benchmark.get(
                "source_exchange"
            )
            if benchmark.get(
                "available"
            )
            else None
        )

        candle_results = (
            metrics.get(
                "candle_analysis",
                {},
            )
        )

        microstructure = (
            metrics.get(
                "microstructure",
                {},
            )
        )

        candle_details = (
            candle_results.get(
                "details",
                {},
            )
        )

        breakdown_score = candle_results.get(
            "breakdown_score"
        )
        metrics["breakdown_confirmation"] = {
            "primary_bearish_timeframes": (
                breakdown_score
                if isinstance(breakdown_score, int)
                and not isinstance(breakdown_score, bool)
                else None
            ),
            "primary_breakdown_confirmed": (
                breakdown_score >= 2
                if isinstance(breakdown_score, int)
                and not isinstance(breakdown_score, bool)
                else None
            ),
            "confirmation_exchange_15m": (
                candle_results.get("cross_exchange_confirmed")
                if isinstance(
                    candle_results.get("cross_exchange_confirmed"),
                    bool,
                )
                else None
            ),
            "composite_breakdown_confirmed": (
                candle_results.get("is_breakdown_confirmed")
                if isinstance(
                    candle_results.get("is_breakdown_confirmed"),
                    bool,
                )
                else None
            ),
        }

        metrics[
            "candle_features"
        ] = {
            timeframe: {
                "valid": context.get("valid"),
                "hype_context": context.get("hype_context"),
                "reclaim": context.get("reclaim"),
                "repump": context.get("repump"),
                "rsi_rollover": context.get("rsi_rollover"),
                "bearish_close": context.get("bearish_close"),
                "volume_acceleration": context.get("volume_acceleration"),
                "atr_14": (
                    context.get(
                        "atr_14"
                    )
                ),
                "atr_pct": (
                    context.get(
                        "atr_pct"
                    )
                ),
                "dynamic_support": (
                    context.get(
                        "dynamic_support"
                    )
                ),
                (
                    "distance_to_"
                    "support_pct"
                ): (
                    context.get(
                        "distance_to_support_pct"
                    )
                ),
                (
                    "distance_to_"
                    "support_atr"
                ): (
                    context.get(
                        "distance_to_support_atr"
                    )
                ),
                (
                    "distance_from_"
                    "recent_high_pct"
                ): (
                    context.get(
                        "distance_from_recent_high_pct"
                    )
                ),
                (
                    "extension_from_"
                    "support_atr"
                ): (
                    context.get(
                        "extension_from_support_atr"
                    )
                ),
                "return_3bars_pct": (
                    context.get(
                        "return_3bars_pct"
                    )
                ),
                "return_6bars_pct": (
                    context.get(
                        "return_6bars_pct"
                    )
                ),
                "return_12bars_pct": (
                    context.get(
                        "return_12bars_pct"
                    )
                ),
                "lower_high": (
                    context.get(
                        "lower_high"
                    )
                ),
                "support_broken": (
                    context.get(
                        "support_broken"
                    )
                ),
                "regime_bearish": (
                    context.get(
                        "regime_bearish"
                    )
                ),
                "trigger_ready": (
                    context.get(
                        "trigger_ready"
                    )
                ),
                "setup": (
                    context.get(
                        "setup"
                    )
                ),
                "pump_pct": (
                    context.get(
                        "pump_pct"
                    )
                ),
            }
            for (
                timeframe,
                context,
            ) in (
                candle_details.items()
            )
            if (
                isinstance(
                    context,
                    dict,
                )
                and context.get(
                    "valid"
                ) is True
            )
        }

        if benchmark.get(
            "available"
        ):
            metrics[
                "relative_weakness_features"
            ] = (
                self
                ._relative_return_features(
                    candle_details,
                    benchmark.get(
                        "details",
                        {},
                    ),
                )
            )

        else:
            metrics[
                "relative_weakness_features"
            ] = {
                "available": False,
                "benchmark": "BTC",
                "available_pairs": 0,
                "timeframes": {},
                "reason": (
                    benchmark.get(
                        "reason"
                    )
                ),
            }

        metrics[
            "watch_score"
        ] = (
            self._watch_score(
                candles=(
                    candle_details
                ),
                microstructure=(
                    microstructure
                ),
                derivatives=(
                    derivatives
                ),
                cross_exchange_confirmed=(
                    candle_results.get(
                        "is_breakdown_confirmed"
                    )
                ),
                ticker=ticker,
            )
        )

        strategy_stages = (
            self.candle_analyzer
            .channel_stages(
                candle_details
            )
        )

        metrics[
            "strategy_stages"
        ] = strategy_stages

        stage_lifecycle, persisted_stage_chain_complete = (
            await self._advance_stage_lifecycle(
                symbol,
                lifecycle_id,
                strategy_stages,
            )
        )
        if stage_lifecycle is not None:
            metrics["stage_lifecycle"] = stage_lifecycle

        cascade_evaluated_at = int(time.time())
        self._attach_live_liquidation_flow(
            metrics,
            exchange_name=str(metrics.get("exchange") or ""),
            mapped_symbol=mapped_sym,
            now=float(cascade_evaluated_at),
        )
        metrics["cascade_intelligence"] = build_cascade_evidence(
            metrics,
            evaluated_at=cascade_evaluated_at,
        )

        if not derivatives.get(
            "available"
        ):
            reason = str(
                derivatives.get(
                    "reason"
                )
                or (
                    "incomplete fresh "
                    "derivatives packet"
                )
            )

            observation_score = (
                metrics[
                    "watch_score"
                ].get(
                    "score"
                )
            )

            observation_state = (
                self
                ._observational_status(
                    strategy_stages
                )
            )

            metrics.update(
                {
                    (
                        "score_version"
                    ): (
                        ScoreV2.version
                    ),
                    "score": None,
                    (
                        "score_components"
                    ): {},
                    (
                        "quality_gates"
                    ): {
                        (
                            "complete_fresh_"
                            "derivatives_packet"
                        ): False
                    },
                    (
                        "analysis_reason"
                    ): reason,
                    "error": reason,
                    (
                        "total_score"
                    ): None,
                    (
                        "trade_eligible"
                    ): False,
                    (
                        "observation_score"
                    ): (
                        observation_score
                    ),
                    (
                        "observation_status"
                    ): (
                        observation_state
                    ),
                    (
                        "observation_"
                        "score_version"
                    ): (
                        metrics[
                            "watch_score"
                        ].get(
                            "score_version"
                        )
                    ),
                    (
                        "observation_"
                        "components"
                    ): (
                        metrics[
                            "watch_score"
                        ].get(
                            "components",
                            {},
                        )
                    ),
                }
            )

            return finish_runtime(
                {
                    "is_valid": False,
                    "score": None,
                    "suggested_status": "REJECTED",
                    "observation_score": observation_score,
                    "observation_status": observation_state,
                    "metrics": metrics,
                },
                outcome="complete",
            )

        score_result = (
            self._merge_score_v2(
                candles=(
                    candle_details
                ),
                microstructure=(
                    microstructure
                ),
                derivatives=(
                    derivatives
                ),
                cross_exchange_confirmed=(
                    bool(
                        candle_results.get(
                            "is_breakdown_confirmed"
                        )
                    )
                ),
                ticker=ticker,
                reference_price=(
                    reference_price
                ),
                strategy_stages=(
                    strategy_stages
                ),
                persisted_stage_chain_complete=(
                    persisted_stage_chain_complete
                ),
            )
        )

        metrics[
            "score_version"
        ] = (
            score_result[
                "score_version"
            ]
        )

        metrics[
            "score"
        ] = (
            score_result[
                "score"
            ]
        )

        metrics[
            "score_components"
        ] = (
            score_result[
                "score_components"
            ]
        )

        metrics[
            "quality_gates"
        ] = {
            "live_orderbook": (
                bool(orderbook)
            ),
            **score_result[
                "quality_gates"
            ],
        }

        metrics[
            "strategy_stages"
        ] = (
            strategy_stages
        )

        metrics[
            "analysis_reason"
        ] = (
            score_result[
                "reason"
            ]
        )

        metrics[
            "total_score"
        ] = (
            score_result[
                "score"
            ]
        )

        experimental_trigger = False

        if not score_result[
            "is_valid"
        ]:
            observation_score = (
                metrics[
                    "watch_score"
                ].get(
                    "score"
                )
            )

            observation_state = (
                self
                ._observational_status(
                    strategy_stages
                )
            )

            metrics[
                "analysis_reason"
            ] = (
                score_result[
                    "reason"
                ]
                or (
                    "strict trade gates "
                    "incomplete"
                )
            )

            metrics[
                "error"
            ] = (
                metrics[
                    "analysis_reason"
                ]
            )

            metrics[
                "score"
            ] = None

            metrics[
                "total_score"
            ] = None

            metrics[
                "score_version"
            ] = (
                ScoreV2.version
            )

            metrics[
                "score_components"
            ] = {}

            metrics[
                "trade_eligible"
            ] = False

            metrics[
                "observation_score"
            ] = (
                observation_score
            )

            metrics[
                "observation_status"
            ] = (
                observation_state
            )

            metrics[
                "observation_score_version"
            ] = (
                metrics[
                    "watch_score"
                ].get(
                    "score_version"
                )
            )

            metrics[
                "observation_components"
            ] = (
                metrics[
                    "watch_score"
                ].get(
                    "components",
                    {},
                )
            )

            experimental_trigger = self._experimental_pretrigger_eligible(
                enabled=bool(settings.experimental_pretrigger_enabled),
                threshold=float(settings.experimental_pretrigger_threshold),
                observation_score=observation_score,
                observation_status=observation_state,
                strategy_stages=strategy_stages,
                quality_gates=metrics["quality_gates"],
                microstructure=microstructure,
                derivatives=derivatives,
            )

            if not experimental_trigger:
                self._attach_position_setup_from_capture(
                    metrics,
                    status=observation_state,
                    candle_results=candle_results,
                    ticker=ticker,
                    microstructure=microstructure,
                    market_info=market_info,
                )
                return finish_runtime(
                    {
                        "is_valid": False,
                        "score": None,
                        "suggested_status": "REJECTED",
                        "observation_score": observation_score,
                        "observation_status": observation_state,
                        "metrics": metrics,
                    },
                    outcome="complete",
                )

            base_score = float(observation_score)
            metrics.update(
                {
                    "strategy_profile": self.experimental_profile,
                    "calibration_status": "pending",
                    "experimental_trigger_threshold": float(
                        settings.experimental_pretrigger_threshold
                    ),
                    "score": base_score,
                    "total_score": base_score,
                    "score_version": metrics["watch_score"].get("score_version"),
                    "score_components": metrics["watch_score"].get("components", {}),
                    "analysis_reason": "experimental pre-trigger threshold passed",
                    "trade_eligible": False,
                }
            )
            metrics.pop("error", None)
        else:
            base_score = score_result["score"]
            metrics["trade_eligible"] = True
            metrics["strategy_profile"] = STRICT_STRATEGY_PROFILE

        status = (
            "TRIGGERED"
            if experimental_trigger
            else self._suggested_status(
                base_score,
                strategy_stages,
                bool(microstructure.get("approved")),
                bool(candle_results.get("is_breakdown_confirmed")),
                persisted_stage_chain_complete=(
                    persisted_stage_chain_complete
                ),
            )
        )

        metrics[
            "total_score"
        ] = base_score

        status = self._attach_position_setup_from_capture(
            metrics,
            status=status,
            candle_results=candle_results,
            ticker=ticker,
            microstructure=microstructure,
            market_info=market_info,
        )

        return finish_runtime(
            {
                "is_valid": True,
                "score": base_score,
                "suggested_status": status,
                "metrics": metrics,
            },
            outcome="complete",
        )

    async def close_all(self):
        await self.ws_manager.close_all()
        await self.gateway.close_all()
