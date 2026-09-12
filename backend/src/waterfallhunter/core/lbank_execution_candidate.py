import copy
import logging
import threading
import time
from typing import Any

from waterfallhunter.core.lbank_execution_stats import (
    LBankExecutionStats,
)
from waterfallhunter.core.lbank_execution_suitability import (
    LBankExecutionSuitability,
    SUITABILITY_UNKNOWN,
)


logger = logging.getLogger(
    "WaterfallHunter.LBankExecutionCandidate"
)

EXECUTION_SUITABILITY_ENRICHMENT_UNAVAILABLE = (
    "execution suitability enrichment unavailable"
)


class LBankExecutionCandidateEnricher:
    """
    Read-only compact execution-suitability exposure for candidates.

    This layer intentionally does NOT:
    - mutate scan_eligible
    - mutate catalogue state
    - mutate hunter state
    - mutate score
    - send alerts
    - place orders
    - determine trade eligibility

    A short in-memory cache prevents high-frequency candidate/SSE reads
    from repeatedly querying SQLite for the same historical statistics.
    """

    def __init__(
        self,
        db_path: str = "/app/data/waterfall_registry.db",
        *,
        cache_ttl_seconds: float = 60.0,
        stats: LBankExecutionStats | None = None,
        classifier: LBankExecutionSuitability | None = None,
    ):
        self.stats = (
            stats
            if stats is not None
            else LBankExecutionStats(
                db_path=db_path
            )
        )

        self.classifier = (
            classifier
            if classifier is not None
            else LBankExecutionSuitability()
        )

        self.cache_ttl_seconds = max(
            0.0,
            float(
                cache_ttl_seconds
            ),
        )

        self._cache: dict[
            str,
            tuple[
                float,
                dict,
            ],
        ] = {}

        self._cache_lock = (
            threading.Lock()
        )

    @staticmethod
    def _finite(
        value: Any,
    ) -> float | None:
        if isinstance(
            value,
            bool,
        ):
            return None

        if not isinstance(
            value,
            (
                int,
                float,
            ),
        ):
            return None

        number = float(
            value
        )

        if number != number:
            return None

        if number in {
            float("inf"),
            float("-inf"),
        }:
            return None

        return number

    @classmethod
    def _metric(
        cls,
        summary: dict,
        metric_name: str,
        field: str,
    ) -> float | None:
        metrics = (
            summary.get(
                "metrics"
            )
            if isinstance(
                summary.get(
                    "metrics"
                ),
                dict,
            )
            else {}
        )

        metric = (
            metrics.get(
                metric_name
            )
            if isinstance(
                metrics.get(
                    metric_name
                ),
                dict,
            )
            else {}
        )

        return cls._finite(
            metric.get(
                field
            )
        )

    @staticmethod
    def _unknown_packet(
        symbol: str,
        reason: str,
    ) -> dict:
        return {
            "symbol": symbol,
            "status": SUITABILITY_UNKNOWN,
            "reason": reason,
            "evidence_status": None,
            "observed_samples": None,
            "observation_span_hours": None,
            "availability_rate": None,
            "cost_100_p90_pct": None,
            "spread_p90_pct": None,
            "depth_25bps_p50_usdt": None,
            "failed_checks": [],
            "observational_only": True,
            "trade_eligible": None,
        }

    def _packet_from_summary(
        self,
        symbol: str,
        summary: dict,
    ) -> dict:
        classification = self.classifier.classify_summary(summary)
        evidence = (
            summary.get("evidence")
            if isinstance(summary, dict)
            and isinstance(summary.get("evidence"), dict)
            else {}
        )
        return {
            "symbol": symbol,
            "status": classification.get("status") or SUITABILITY_UNKNOWN,
            "reason": (
                classification.get("reason")
                or "execution suitability unavailable"
            ),
            "evidence_status": classification.get("evidence_status"),
            "observed_samples": evidence.get("observed_samples"),
            "observation_span_hours": evidence.get("observation_span_hours"),
            "availability_rate": self._finite(summary.get("availability_rate")),
            "cost_100_p90_pct": self._metric(summary, "cost_100_pct", "p90"),
            "spread_p90_pct": self._metric(summary, "spread_pct", "p90"),
            "depth_25bps_p50_usdt": self._metric(
                summary,
                "depth_25bps_min_usdt",
                "p50",
            ),
            "failed_checks": list(classification.get("failed_checks") or []),
            "observational_only": True,
            "trade_eligible": None,
        }

    def _build_packet(
        self,
        symbol: str,
    ) -> dict:
        try:
            summary = self.stats.summarize_symbol(symbol)
            return self._packet_from_summary(symbol, summary)
        except Exception as exc:
            logger.warning(
                "Execution suitability candidate enrichment failed for %s: %s",
                symbol,
                exc,
            )
            return self._unknown_packet(
                symbol,
                EXECUTION_SUITABILITY_ENRICHMENT_UNAVAILABLE,
            )

    def _cached_packets_and_misses(
        self,
        requested: list[str],
        *,
        now: float,
    ) -> tuple[dict[str, dict], list[str]]:
        packets: dict[str, dict] = {}
        misses: list[str] = []
        with self._cache_lock:
            for symbol in requested:
                if not symbol:
                    packets[symbol] = self._unknown_packet("", "symbol missing")
                    continue
                cached = self._cache.get(symbol)
                if cached is None:
                    misses.append(symbol)
                    continue
                cached_at, packet = cached
                if now - cached_at > self.cache_ttl_seconds:
                    misses.append(symbol)
                    continue
                packets[symbol] = copy.deepcopy(packet)
        return packets, misses

    def _fresh_bulk_packets(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        if not symbols:
            return {}
        try:
            suitability_bulk = getattr(
                self.stats, "summarize_symbols_for_suitability", None
            )
            summaries = (
                suitability_bulk(symbols)
                if callable(suitability_bulk)
                else self.stats.summarize_symbols(symbols)
            )
            summaries_by_symbol = {
                str(summary.get("symbol") or ""): summary
                for summary in summaries
                if isinstance(summary, dict)
            }
        except Exception as exc:
            logger.warning(
                "Bulk execution suitability enrichment failed: %s",
                exc,
            )
            summaries_by_symbol = {}

        packets: dict[str, dict] = {}
        for symbol in symbols:
            summary = summaries_by_symbol.get(symbol)
            if summary is None:
                packets[symbol] = self._unknown_packet(
                    symbol,
                    EXECUTION_SUITABILITY_ENRICHMENT_UNAVAILABLE,
                )
                continue
            try:
                packets[symbol] = self._packet_from_summary(symbol, summary)
            except Exception as exc:
                logger.warning(
                    "Execution suitability candidate enrichment failed for %s: %s",
                    symbol,
                    exc,
                )
                packets[symbol] = self._unknown_packet(
                    symbol,
                    EXECUTION_SUITABILITY_ENRICHMENT_UNAVAILABLE,
                )
        return packets

    def for_symbols(
        self,
        symbols,
    ) -> dict[str, dict]:
        requested = list(dict.fromkeys(str(symbol or "") for symbol in symbols))
        now = time.monotonic()
        packets, misses = self._cached_packets_and_misses(requested, now=now)
        fresh_packets = self._fresh_bulk_packets(misses)

        if fresh_packets:
            with self._cache_lock:
                for symbol, packet in fresh_packets.items():
                    self._cache[symbol] = (now, copy.deepcopy(packet))
                    packets[symbol] = copy.deepcopy(packet)

        return {
            symbol: copy.deepcopy(packets[symbol])
            for symbol in requested
        }

    def for_symbol(
        self,
        symbol: str,
    ) -> dict:
        symbol = str(
            symbol
            or ""
        )

        if not symbol:
            return self._unknown_packet(
                "",
                "symbol missing",
            )

        now = time.monotonic()

        with self._cache_lock:
            cached = self._cache.get(
                symbol
            )

            if cached is not None:
                cached_at, packet = cached

                if (
                    now
                    - cached_at
                    <= self.cache_ttl_seconds
                ):
                    return copy.deepcopy(
                        packet
                    )

        packet = self._build_packet(
            symbol
        )

        with self._cache_lock:
            self._cache[
                symbol
            ] = (
                now,
                copy.deepcopy(
                    packet
                ),
            )

        return copy.deepcopy(
            packet
        )

    def invalidate(
        self,
        symbol: str | None = None,
    ) -> None:
        with self._cache_lock:
            if symbol is None:
                self._cache.clear()
                return

            self._cache.pop(
                str(
                    symbol
                ),
                None,
            )
