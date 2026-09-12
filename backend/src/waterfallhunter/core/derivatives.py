import math
from typing import Any


class DerivativesAnalyzer:
    """Normalize complete, live derivative data without filling unavailable fields."""

    max_data_age_seconds = 15 * 60
    max_funding_age_seconds = 9 * 60 * 60
    max_funding_history_age_seconds = 32 * 24 * 60 * 60
    min_oi_span_seconds = 55 * 60
    max_oi_span_seconds = 75 * 60

    @staticmethod
    def _finite(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    def _is_fresh(self, timestamp: Any, retrieved_at: float) -> bool:
        if not isinstance(timestamp, (int, float)) or timestamp <= 0:
            return False
        age_seconds = retrieved_at - float(timestamp) / 1000.0
        return 0.0 <= age_seconds <= self.max_data_age_seconds

    @staticmethod
    def _provenance(exchange: str, mapped_symbol: str, market_id: str, retrieved_at: float) -> dict[str, Any]:
        return {
            "source_exchange": exchange,
            "mapped_symbol": mapped_symbol,
            "market_id": market_id,
            "retrieved_at": retrieved_at,
            "fallback_attempts": [],
        }

    @classmethod
    def _timestamp(cls, row: Any, field: str) -> int | None:
        if not isinstance(row, dict):
            return None
        timestamp = cls._finite(row.get(field))
        if timestamp is None or timestamp <= 0:
            return None
        return int(timestamp)

    @staticmethod
    def _matches_market(row: dict[str, Any], market_id: str) -> bool:
        symbol = row.get("symbol")
        return symbol is None or symbol == market_id

    @staticmethod
    def _funding_z_score(values: list[float]) -> float | None:
        if len(values) < 2:
            return None
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        if variance <= 0:
            return 0.0
        return (values[-1] - mean) / math.sqrt(variance)

    @staticmethod
    def _oi_acceleration(parsed_oi: list[tuple[int, float]]) -> float | None:
        if len(parsed_oi) < 3:
            return None
        first, middle, latest = parsed_oi[-3:]
        first_hours = (middle[0] - first[0]) / 3_600_000.0
        second_hours = (latest[0] - middle[0]) / 3_600_000.0
        if first_hours <= 0 or second_hours <= 0 or first[1] <= 0 or middle[1] <= 0:
            return None
        first_rate = ((middle[1] - first[1]) / first[1] * 100.0) / first_hours
        second_rate = ((latest[1] - middle[1]) / middle[1] * 100.0) / second_hours
        acceleration_hours = (first_hours + second_hours) / 2.0
        if acceleration_hours <= 0:
            return None
        return (second_rate - first_rate) / acceleration_hours

    def evaluate_binance_rows(
        self,
        *,
        mapped_symbol: str,
        market_id: str,
        funding_rows: Any,
        taker_rows: Any,
        top_trader_rows: Any,
        open_interest_rows: Any,
        retrieved_at: float,
    ) -> dict[str, Any]:
        """Validate raw Binance USD-M rows before producing the normalized packet."""
        funding_history: list[float] | None = None
        current_funding: float | None = None
        if isinstance(funding_rows, list) and len(funding_rows) >= 2:
            parsed_funding: list[tuple[int, float]] = []
            for row in funding_rows:
                timestamp = self._timestamp(row, "fundingTime")
                rate = self._finite(row.get("fundingRate")) if isinstance(row, dict) else None
                if timestamp is None or rate is None or not self._matches_market(row, market_id):
                    parsed_funding = []
                    break
                age_seconds = retrieved_at - timestamp / 1000.0
                if not 0.0 <= age_seconds <= self.max_funding_history_age_seconds:
                    parsed_funding = []
                    break
                parsed_funding.append((timestamp, rate))
            if (
                parsed_funding
                and all(later[0] > earlier[0] for earlier, later in zip(parsed_funding, parsed_funding[1:]))
                and retrieved_at - parsed_funding[-1][0] / 1000.0 <= self.max_funding_age_seconds
            ):
                funding_history = [rate for _, rate in parsed_funding]
                current_funding = funding_history[-1]

        taker_ratio: float | None = None
        taker_ratio_change_1h: float | None = None
        if isinstance(taker_rows, list) and taker_rows:
            row = taker_rows[-1]
            timestamp = self._timestamp(row, "timestamp")
            candidate = self._finite(row.get("buySellRatio")) if isinstance(row, dict) else None
            if (
                candidate is not None
                and candidate > 0
                and timestamp is not None
                and self._matches_market(row, market_id)
                and self._is_fresh(timestamp, retrieved_at)
            ):
                taker_ratio = candidate
            if len(taker_rows) >= 2:
                parsed_taker: list[tuple[int, float]] = []
                for history_row in taker_rows:
                    timestamp = self._timestamp(history_row, "timestamp")
                    ratio = self._finite(history_row.get("buySellRatio")) if isinstance(history_row, dict) else None
                    if (
                        timestamp is None
                        or ratio is None
                        or ratio <= 0
                        or not self._matches_market(history_row, market_id)
                        or timestamp > int(retrieved_at * 1000)
                    ):
                        parsed_taker = []
                        break
                    parsed_taker.append((timestamp, ratio))
                if parsed_taker and all(later[0] > earlier[0] for earlier, later in zip(parsed_taker, parsed_taker[1:])):
                    span_seconds = (parsed_taker[-1][0] - parsed_taker[0][0]) / 1000.0
                    if self.min_oi_span_seconds <= span_seconds <= self.max_oi_span_seconds and self._is_fresh(parsed_taker[-1][0], retrieved_at):
                        taker_ratio_change_1h = round(parsed_taker[-1][1] - parsed_taker[0][1], 4)

        top_trader_ratio: float | None = None
        if isinstance(top_trader_rows, list) and top_trader_rows:
            row = top_trader_rows[-1]
            timestamp = self._timestamp(row, "timestamp")
            candidate = self._finite(row.get("longShortRatio")) if isinstance(row, dict) else None
            if (
                candidate is not None
                and candidate > 0
                and timestamp is not None
                and self._matches_market(row, market_id)
                and self._is_fresh(timestamp, retrieved_at)
            ):
                top_trader_ratio = candidate

        current_oi: float | None = None
        oi_one_hour_ago: float | None = None
        parsed_oi: list[tuple[int, float]] = []
        if isinstance(open_interest_rows, list) and len(open_interest_rows) >= 2:
            for row in open_interest_rows:
                timestamp = self._timestamp(row, "timestamp")
                value = self._finite(row.get("sumOpenInterestValue")) if isinstance(row, dict) else None
                amount = self._finite(row.get("sumOpenInterest")) if isinstance(row, dict) else None
                if (
                    timestamp is None
                    or value is None
                    or value <= 0
                    or amount is None
                    or amount <= 0
                    or not self._matches_market(row, market_id)
                ):
                    parsed_oi = []
                    break
                age_seconds = retrieved_at - timestamp / 1000.0
                if not 0.0 <= age_seconds <= self.max_oi_span_seconds:
                    parsed_oi = []
                    break
                parsed_oi.append((timestamp, value))
            if parsed_oi and all(later[0] > earlier[0] for earlier, later in zip(parsed_oi, parsed_oi[1:])):
                span_seconds = (parsed_oi[-1][0] - parsed_oi[0][0]) / 1000.0
                if span_seconds >= self.min_oi_span_seconds and self._is_fresh(parsed_oi[-1][0], retrieved_at):
                    oi_one_hour_ago = parsed_oi[0][1]
                    current_oi = parsed_oi[-1][1]

        result = self.evaluate_packet(
            exchange="binance",
            mapped_symbol=mapped_symbol,
            market_id=market_id,
            funding_history=funding_history,
            current_funding=current_funding,
            current_oi=current_oi,
            oi_one_hour_ago=oi_one_hour_ago,
            taker_buy_sell_ratio=taker_ratio,
            top_trader_long_short_ratio=top_trader_ratio,
            retrieved_at=retrieved_at,
            taker_ratio_change_1h=taker_ratio_change_1h,
        )

        if result.get("available") is True:
            settled_rates = funding_history or []
            z_score = self._funding_z_score(settled_rates)
            oi_acceleration = self._oi_acceleration(parsed_oi)
            result["precrash_observations"] = {
                "contract_version": "precrash_derivatives_observation_v1",
                "observational_only": True,
                "hard_gating_allowed": False,
                "promotion_allowed": False,
                "funding_percentile": result.get("funding_percentile"),
                "funding_z_score": round(z_score, 4) if z_score is not None else None,
                "taker_ratio_momentum_1h": result.get("taker_ratio_change_1h"),
                "oi_acceleration_pct_per_hour2": (
                    round(oi_acceleration, 4)
                    if oi_acceleration is not None
                    else None
                ),
            }

        return result

    def evaluate_packet(
        self,
        *,
        exchange: str,
        mapped_symbol: str,
        market_id: str,
        funding_history: Any,
        current_funding: Any,
        current_oi: Any,
        oi_one_hour_ago: Any,
        taker_buy_sell_ratio: Any,
        top_trader_long_short_ratio: Any,
        retrieved_at: float,
        taker_ratio_change_1h: Any = None,
    ) -> dict[str, Any]:
        """Validate and normalize a complete real derivatives packet."""
        context = self._provenance(exchange, mapped_symbol, market_id, retrieved_at)

        if not isinstance(retrieved_at, (int, float)) or not math.isfinite(retrieved_at) or retrieved_at <= 0:
            return {"available": False, "reason": "invalid derivatives retrieval timestamp", **context}
        if not isinstance(market_id, str) or not market_id:
            return {"available": False, "reason": "missing canonical market id", **context}

        funding_rate = self._finite(current_funding)
        if funding_rate is None:
            return {"available": False, "reason": "missing valid funding rate", **context}
        if not isinstance(funding_history, list) or len(funding_history) < 2:
            return {"available": False, "reason": "missing valid funding history", **context}
        settled_rates = [self._finite(value) for value in funding_history]
        if any(value is None for value in settled_rates):
            return {"available": False, "reason": "missing valid funding history", **context}

        current_open_interest = self._finite(current_oi)
        if current_open_interest is None or current_open_interest <= 0:
            return {"available": False, "reason": "missing valid current open interest", **context}
        prior_open_interest = self._finite(oi_one_hour_ago)
        if prior_open_interest is None or prior_open_interest <= 0:
            return {"available": False, "reason": "missing valid one-hour open interest", **context}

        taker_ratio = self._finite(taker_buy_sell_ratio)
        if taker_ratio is None or taker_ratio <= 0:
            return {"available": False, "reason": "missing valid taker buy/sell ratio", **context}
        top_trader_ratio = self._finite(top_trader_long_short_ratio)
        if top_trader_ratio is None or top_trader_ratio <= 0:
            return {"available": False, "reason": "missing valid top-trader long/short ratio", **context}

        funding_percentile = sum(value <= funding_rate for value in settled_rates) / len(settled_rates)
        oi_change_pct = (current_open_interest - prior_open_interest) / prior_open_interest * 100.0
        result = {
            "available": True,
            **context,
            "funding_rate": funding_rate,
            "funding_percentile": round(funding_percentile, 4),
            "open_interest_usdt": current_open_interest,
            "oi_change_1h_pct": round(oi_change_pct, 4),
            "taker_buy_sell_ratio": taker_ratio,
            "top_trader_long_short_ratio": top_trader_ratio,
        }
        taker_change = self._finite(taker_ratio_change_1h)
        if taker_change is not None:
            result["taker_ratio_change_1h"] = round(taker_change, 4)
        return result
