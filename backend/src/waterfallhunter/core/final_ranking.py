import math
from statistics import median
from typing import Any


class FinalRanking:
    """Read-only ranking over already-produced candidate measurements."""

    VERSION = "final_ranking_observational_v2"
    WEIGHTS = {
        "cascade_readiness": 25.0,
        "signal_score": 20.0,
        "execution_quality": 20.0,
        "relative_weakness": 15.0,
        "analysis_freshness": 5.0,
        "reference_freshness": 5.0,
    }

    @staticmethod
    def _finite(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    @classmethod
    def _component(cls, value: float | None, weight: float, reason: str | None = None) -> dict:
        if value is None:
            return {"available": False, "value": None, "weight": weight, "points": None, "reason": reason}
        bounded = min(max(float(value), 0.0), 1.0)
        return {"available": True, "value": round(bounded, 6), "weight": weight, "points": round(bounded * weight, 6)}

    @classmethod
    def _readiness(cls, candidate: dict, metrics: dict) -> float | None:
        fields = ("hype", "damage", "setup", "trigger")
        lifecycle = metrics.get("stage_lifecycle")
        confirmed = (
            lifecycle.get("confirmed")
            if isinstance(lifecycle, dict)
            and isinstance(lifecycle.get("confirmed"), dict)
            else None
        )
        if (
            isinstance(lifecycle, dict)
            and lifecycle.get("available") is True
            and lifecycle.get("stale") is False
            and lifecycle.get("observational_only") is False
            and lifecycle.get("hard_gating_allowed") is True
            and isinstance(confirmed, dict)
            and confirmed.get("passed") is True
            and all(confirmed.get(field) is True for field in fields)
        ):
            return 1.0

        stages = metrics.get("strategy_stages")
        if isinstance(stages, dict):
            if all(isinstance(stages.get(field), bool) for field in fields):
                return sum(stages[field] for field in fields) / len(fields)
        status = candidate.get("status") or metrics.get("observation_status")
        return {
            "WATCH": 0.0, "FUEL-RICH": 0.25, "PRE-TRIGGER": 0.5,
            "ARMED": 0.75, "TRIGGERED": 1.0,
        }.get(status)

    @classmethod
    def _relative_weakness(cls, metrics: dict) -> float | None:
        packet = metrics.get("relative_weakness_features")
        timeframes = packet.get("timeframes") if isinstance(packet, dict) and packet.get("available") is True else None
        if not isinstance(timeframes, dict):
            return None
        values = []
        for timeframe in ("4h", "1h", "15m", "5m"):
            fields = timeframes.get(timeframe)
            if not isinstance(fields, dict):
                continue
            value = cls._finite(fields.get("relative_return_6bars_pct"))
            if value is not None:
                values.append(value)
        if not values:
            return None
        # 0% relative return scores neutral; -5% or weaker reaches the cap.
        return min(max(-median(values) / 5.0, 0.0), 1.0)

    @classmethod
    def _freshness(
        cls,
        observed_at: Any,
        evaluation_time: float,
    ) -> float | None:
        observed = cls._finite(observed_at)
        if observed is None or observed < 0 or observed > evaluation_time:
            return None
        return max(0.0, 1.0 - (evaluation_time - observed) / 180.0)

    @classmethod
    def _signal_score(cls, candidate: dict, metrics: dict) -> float | None:
        watch = metrics.get("watch_score")
        partial_watch = (
            metrics.get("trade_eligible") is False
            and isinstance(watch, dict)
        )

        if partial_watch:
            watch_score = cls._finite(watch.get("score"))
            coverage = cls._finite(watch.get("coverage_pct"))
            if watch_score is None or coverage is None:
                return None
            return (watch_score / 100.0) * (coverage / 100.0)

        if (
            metrics.get("score_version") != "score_v2"
            or metrics.get("trade_eligible") is not True
        ):
            return None

        score = cls._finite(candidate.get("score"))
        if score is None:
            score = cls._finite(metrics.get("score"))
        if score is None:
            return None
        return score / 100.0

    @classmethod
    def for_candidate(
        cls,
        symbol: str,
        candidate: dict,
        *,
        evaluation_time: float,
    ) -> dict:
        evaluated_at = cls._finite(evaluation_time)
        if evaluated_at is None or evaluated_at < 0:
            raise ValueError("evaluation_time must be a non-negative finite timestamp")
        metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
        execution = candidate.get("execution_suitability")
        execution_status = execution.get("status") if isinstance(execution, dict) else None
        components = {
            "cascade_readiness": cls._component(
                cls._readiness(candidate, metrics), cls.WEIGHTS["cascade_readiness"], "lifecycle stages unavailable",
            ),
            "signal_score": cls._component(
                cls._signal_score(candidate, metrics),
                cls.WEIGHTS["signal_score"],
                "complete Score V2 or coverage-qualified Watch Score unavailable",
            ),
            "execution_quality": cls._component(
                {"SUITABLE": 1.0, "MARGINAL": 0.6, "POOR": 0.0}.get(execution_status),
                cls.WEIGHTS["execution_quality"], "execution suitability unavailable or unknown",
            ),
            "relative_weakness": cls._component(
                cls._relative_weakness(metrics), cls.WEIGHTS["relative_weakness"], "benchmark-relative returns unavailable",
            ),
            "analysis_freshness": cls._component(
                cls._freshness(candidate.get("analysis_observed_at"), evaluated_at),
                cls.WEIGHTS["analysis_freshness"],
                "analysis observation timestamp unavailable, invalid, or in the future",
            ),
            "reference_freshness": cls._component(
                cls._freshness(candidate.get("reference_observed_at"), evaluated_at),
                cls.WEIGHTS["reference_freshness"],
                "reference observation timestamp unavailable, invalid, or in the future",
            ),
        }
        available_weight = sum(packet["weight"] for packet in components.values() if packet["available"])
        points = sum(packet["points"] for packet in components.values() if packet["available"])
        normalized = points / available_weight * 100.0 if available_weight else None
        confidence = available_weight / sum(cls.WEIGHTS.values())
        ranking_score = normalized * confidence if normalized is not None else None
        missing = [name for name, packet in components.items() if not packet["available"]]
        return {
            "version": cls.VERSION,
            "symbol": symbol,
            "evaluation_time": evaluated_at,
            "analysis_observed_at": cls._finite(candidate.get("analysis_observed_at")),
            "reference_observed_at": cls._finite(candidate.get("reference_observed_at")),
            "score": round(ranking_score, 6) if ranking_score is not None else None,
            "normalized_available_score": round(normalized, 6) if normalized is not None else None,
            "confidence": round(confidence, 6),
            "available_weight": available_weight,
            "components": components,
            "missing_components": missing,
            "anti_chase": {"status": "NOT_EVALUATED", "veto": None, "reason": "no calibrated anti-chase decision packet"},
            "observational_only": True,
            "trade_eligible": None,
        }

    @classmethod
    def rank(
        cls,
        candidates: dict[str, dict],
        limit: int = 3,
        *,
        evaluation_time: float,
    ) -> dict:
        evaluated_at = cls._finite(evaluation_time)
        if evaluated_at is None or evaluated_at < 0:
            raise ValueError("evaluation_time must be a non-negative finite timestamp")
        packets = [
            cls.for_candidate(
                symbol,
                candidate,
                evaluation_time=evaluated_at,
            )
            for symbol, candidate in candidates.items()
        ]
        packets.sort(key=lambda packet: (
            packet["score"] is not None,
            packet["score"] if packet["score"] is not None else -1.0,
            packet["confidence"],
            packet["symbol"],
        ), reverse=True)
        for index, packet in enumerate(packets, start=1):
            packet["rank"] = index
        return {
            "version": cls.VERSION,
            "evaluation_time": evaluated_at,
            "observational_only": True,
            "trade_eligible": None,
            "ranked_count": len(packets),
            "top": packets[:max(0, int(limit))],
            "all": packets,
        }
