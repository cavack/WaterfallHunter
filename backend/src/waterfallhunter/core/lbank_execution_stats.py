import logging
import math
import sqlite3
import time
from collections import defaultdict
from typing import Any, Iterable, Mapping

from waterfallhunter.core.managed_sqlite import connect_managed_sqlite

logger = logging.getLogger("WaterfallHunter.LBankExecutionStats")

EXECUTION_HISTORY_METRICS = (
    "spread_pct",
    "cost_25_pct",
    "cost_50_pct",
    "cost_100_pct",
    "depth_10bps_min_usdt",
    "depth_25bps_min_usdt",
    "depth_50bps_min_usdt",
    "depth_100bps_min_usdt",
)

EVIDENCE_NO_EVIDENCE = "NO_EVIDENCE"
EVIDENCE_INSUFFICIENT = "INSUFFICIENT"
EVIDENCE_SUFFICIENT = "SUFFICIENT"

DEFAULT_MIN_OBSERVED_SAMPLES = 5
DEFAULT_MIN_SPAN_HOURS = 2.0

_HISTORY_COLUMNS = (
    "id",
    "symbol",
    "observation_status",
    "observed_at",
    *EXECUTION_HISTORY_METRICS,
)
_HISTORY_SELECT = ",\n                            ".join(_HISTORY_COLUMNS)
_COVERAGE_THRESHOLDS = (1, 2, 3, 5, 10, 20, 30)


class LBankExecutionStats:
    """Read-only statistics over append-only LBank execution history."""

    def __init__(self, db_path: str = "/app/data/waterfall_registry.db"):
        self.db_path = db_path

    def _connect(self, timeout: float = 10.0):
        return connect_managed_sqlite(self.db_path, timeout=timeout)

    @staticmethod
    def _finite(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    @staticmethod
    def _percentile_from_ordered(ordered: list[float], percentile_value: float) -> float | None:
        if not ordered:
            return None
        p = max(0.0, min(100.0, float(percentile_value)))
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * p / 100.0
        lower_index = int(math.floor(position))
        upper_index = int(math.ceil(position))
        if lower_index == upper_index:
            return ordered[lower_index]
        fraction = position - lower_index
        lower_value = ordered[lower_index]
        upper_value = ordered[upper_index]
        return lower_value + (upper_value - lower_value) * fraction

    @classmethod
    def percentile(cls, values, percentile_value: float) -> float | None:
        cleaned = [number for value in values if (number := cls._finite(value)) is not None]
        cleaned.sort()
        return cls._percentile_from_ordered(cleaned, percentile_value)

    @classmethod
    def _metric_summary(cls, values) -> dict:
        cleaned = [number for value in values if (number := cls._finite(value)) is not None]
        if not cleaned:
            return {
                "count": 0,
                "min": None,
                "max": None,
                "mean": None,
                "p10": None,
                "p50": None,
                "p90": None,
                "p90_minus_p50": None,
                "p90_to_p50_ratio": None,
            }
        cleaned.sort()
        p10 = cls._percentile_from_ordered(cleaned, 10.0)
        p50 = cls._percentile_from_ordered(cleaned, 50.0)
        p90 = cls._percentile_from_ordered(cleaned, 90.0)
        return {
            "count": len(cleaned),
            "min": cleaned[0],
            "max": cleaned[-1],
            "mean": sum(cleaned) / len(cleaned),
            "p10": p10,
            "p50": p50,
            "p90": p90,
            "p90_minus_p50": None if p90 is None or p50 is None else p90 - p50,
            "p90_to_p50_ratio": None
            if p90 is None or p50 is None or p50 <= 0
            else p90 / p50,
        }

    @staticmethod
    def _coverage_from_times(observed_times, *, now: float) -> dict:
        cleaned = [float(value) for value in observed_times if value is not None]
        if not cleaned:
            return {
                "first_observed_at": None,
                "last_observed_at": None,
                "observation_span_seconds": None,
                "observation_span_hours": None,
                "last_observation_age_seconds": None,
                "last_observation_age_minutes": None,
            }
        first_observed_at = min(cleaned)
        last_observed_at = max(cleaned)
        span_seconds = last_observed_at - first_observed_at
        age_seconds = max(0.0, now - last_observed_at)
        return {
            "first_observed_at": first_observed_at,
            "last_observed_at": last_observed_at,
            "observation_span_seconds": span_seconds,
            "observation_span_hours": span_seconds / 3600.0,
            "last_observation_age_seconds": age_seconds,
            "last_observation_age_minutes": age_seconds / 60.0,
        }

    @staticmethod
    def evidence_sufficiency(
        *,
        observed_count: int,
        observation_span_hours: float | None,
        minimum_observed_samples: int = DEFAULT_MIN_OBSERVED_SAMPLES,
        minimum_span_hours: float = DEFAULT_MIN_SPAN_HOURS,
    ) -> dict:
        required_samples = max(1, int(minimum_observed_samples))
        required_span_hours = max(0.0, float(minimum_span_hours))
        sample_count = max(0, int(observed_count or 0))
        span_hours = (
            None
            if observation_span_hours is None
            else max(0.0, float(observation_span_hours))
        )
        if sample_count == 0:
            return {
                "status": EVIDENCE_NO_EVIDENCE,
                "observed_samples": 0,
                "observation_span_hours": span_hours,
                "minimum_observed_samples": required_samples,
                "minimum_span_hours": required_span_hours,
                "samples_requirement_met": False,
                "span_requirement_met": False,
                "missing_observed_samples": required_samples,
                "missing_span_hours": required_span_hours,
                "reasons": ["no successful execution observations"],
            }

        samples_requirement_met = sample_count >= required_samples
        span_requirement_met = (
            span_hours is not None and span_hours >= required_span_hours
        )
        reasons = []
        if not samples_requirement_met:
            reasons.append("minimum observed sample count not reached")
        if not span_requirement_met:
            reasons.append("minimum temporal span not reached")
        missing_observed_samples = max(0, required_samples - sample_count)
        missing_span_hours = (
            required_span_hours
            if span_hours is None
            else max(0.0, required_span_hours - span_hours)
        )
        sufficient = samples_requirement_met and span_requirement_met
        return {
            "status": EVIDENCE_SUFFICIENT if sufficient else EVIDENCE_INSUFFICIENT,
            "observed_samples": sample_count,
            "observation_span_hours": span_hours,
            "minimum_observed_samples": required_samples,
            "minimum_span_hours": required_span_hours,
            "samples_requirement_met": samples_requirement_met,
            "span_requirement_met": span_requirement_met,
            "missing_observed_samples": missing_observed_samples,
            "missing_span_hours": missing_span_hours,
            "reasons": reasons,
        }

    def _empty_summary(
        self,
        symbol: str,
        *,
        since: float | None,
        generated_at: float | None = None,
    ) -> dict:
        now = time.time() if generated_at is None else float(generated_at)
        return {
            "symbol": symbol,
            "since": since,
            "generated_at": now,
            "observation_count": 0,
            "observed_count": 0,
            "unavailable_count": 0,
            "availability_rate": None,
            "first_observed_at": None,
            "last_observed_at": None,
            "observation_span_seconds": None,
            "observation_span_hours": None,
            "last_observation_age_seconds": None,
            "last_observation_age_minutes": None,
            "evidence": self.evidence_sufficiency(
                observed_count=0,
                observation_span_hours=None,
            ),
            "metrics": {
                metric: self._metric_summary([]) for metric in EXECUTION_HISTORY_METRICS
            },
        }

    @staticmethod
    def _row_value(row: Mapping[str, Any] | sqlite3.Row, key: str) -> Any:
        return row[key]

    def _summary_from_rows(
        self,
        symbol: str,
        rows: Iterable[Mapping[str, Any] | sqlite3.Row],
        *,
        since: float | None,
        generated_at: float,
    ) -> dict:
        materialized = list(rows)
        if not materialized:
            return self._empty_summary(
                symbol,
                since=since,
                generated_at=generated_at,
            )

        observed_rows = [
            row
            for row in materialized
            if self._row_value(row, "observation_status") == "OBSERVED"
        ]
        observation_count = len(materialized)
        observed_count = len(observed_rows)
        unavailable_count = sum(
            1
            for row in materialized
            if self._row_value(row, "observation_status") == "UNAVAILABLE"
        )
        successful_observed_times = [
            number
            for row in observed_rows
            if (
                number := self._finite(self._row_value(row, "observed_at"))
            )
            is not None
        ]
        coverage = self._coverage_from_times(
            successful_observed_times,
            now=generated_at,
        )
        metrics = {
            metric: self._metric_summary(
                [self._row_value(row, metric) for row in observed_rows]
            )
            for metric in EXECUTION_HISTORY_METRICS
        }
        evidence = self.evidence_sufficiency(
            observed_count=observed_count,
            observation_span_hours=coverage["observation_span_hours"],
        )
        return {
            "symbol": symbol,
            "since": since,
            "generated_at": generated_at,
            "observation_count": observation_count,
            "observed_count": observed_count,
            "unavailable_count": unavailable_count,
            "availability_rate": (
                observed_count / observation_count if observation_count else None
            ),
            **coverage,
            "evidence": evidence,
            "metrics": metrics,
        }

    def _suitability_summary_from_rows(
        self,
        symbol: str,
        rows: Iterable[Mapping[str, Any] | sqlite3.Row],
        *,
        generated_at: float,
    ) -> dict:
        materialized = list(rows)
        if not materialized:
            return self._empty_summary(symbol, since=None, generated_at=generated_at)

        observed_rows = [
            row for row in materialized
            if self._row_value(row, "observation_status") == "OBSERVED"
        ]
        observation_count = len(materialized)
        observed_count = len(observed_rows)
        unavailable_count = sum(
            1 for row in materialized
            if self._row_value(row, "observation_status") == "UNAVAILABLE"
        )
        observed_times = [
            number for row in observed_rows
            if (number := self._finite(self._row_value(row, "observed_at"))) is not None
        ]
        coverage = self._coverage_from_times(observed_times, now=generated_at)
        evidence = self.evidence_sufficiency(
            observed_count=observed_count,
            observation_span_hours=coverage["observation_span_hours"],
        )
        metrics = {
            metric: self._metric_summary(
                [self._row_value(row, metric) for row in observed_rows]
            )
            for metric in ("spread_pct", "cost_100_pct", "depth_25bps_min_usdt")
        }
        return {
            "symbol": symbol,
            "since": None,
            "generated_at": generated_at,
            "observation_count": observation_count,
            "observed_count": observed_count,
            "unavailable_count": unavailable_count,
            "availability_rate": observed_count / observation_count if observation_count else None,
            **coverage,
            "evidence": evidence,
            "metrics": metrics,
        }

    def summarize_symbols_for_suitability(
        self, symbols: Iterable[str], *, per_symbol_limit: int = 10_000
    ) -> list[dict]:
        requested = [str(symbol) for symbol in symbols]
        if not requested:
            return []
        bounded_row_limit = max(1, min(int(per_symbol_limit), 100_000))
        generated_at = time.time()
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        unique_symbols = list(dict.fromkeys(symbol for symbol in requested if symbol))
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                for start in range(0, len(unique_symbols), 32):
                    batch = unique_symbols[start : start + 32]
                    placeholders = ",".join("?" for _ in batch)
                    rows = conn.execute(
                        f"""
                        WITH ranked_history AS (
                            SELECT id, symbol, observation_status, observed_at,
                                   spread_pct, cost_100_pct, depth_25bps_min_usdt,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY symbol
                                       ORDER BY observed_at DESC, id DESC
                                   ) AS row_rank
                            FROM lbank_execution_observation_history
                            WHERE symbol IN ({placeholders})
                        )
                        SELECT * FROM ranked_history
                        WHERE row_rank <= ?
                        ORDER BY symbol ASC, row_rank ASC
                        """,
                        (*batch, bounded_row_limit),
                    ).fetchall()
                    for row in rows:
                        grouped[str(row["symbol"])].append(row)
        except Exception:
            logger.exception("Failed reading suitability-specific LBank execution statistics")
            return [self._empty_summary(symbol, since=None, generated_at=generated_at) for symbol in requested]

        return [
            self._suitability_summary_from_rows(
                symbol, grouped.get(symbol, ()), generated_at=generated_at
            )
            for symbol in requested
        ]

    def summarize_symbol(
        self,
        symbol: str,
        *,
        since: float | None = None,
        limit: int = 10_000,
    ) -> dict:
        if not symbol:
            return self._empty_summary("", since=since)
        row_limit = max(1, min(int(limit), 100_000))
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                if since is None:
                    rows = conn.execute(
                        f"""
                        SELECT
                            {_HISTORY_SELECT}
                        FROM lbank_execution_observation_history
                        WHERE symbol = ?
                        ORDER BY observed_at DESC, id DESC
                        LIMIT ?
                        """,
                        (symbol, row_limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"""
                        SELECT
                            {_HISTORY_SELECT}
                        FROM lbank_execution_observation_history
                        WHERE symbol = ? AND observed_at >= ?
                        ORDER BY observed_at DESC, id DESC
                        LIMIT ?
                        """,
                        (symbol, float(since), row_limit),
                    ).fetchall()
        except Exception as exc:
            logger.error(
                "Failed reading LBank execution statistics for %s: %s",
                symbol,
                exc,
            )
            return self._empty_summary(symbol, since=since)

        return self._summary_from_rows(
            symbol,
            rows,
            since=since,
            generated_at=time.time(),
        )

    def summarize_symbols(
        self,
        symbols: Iterable[str],
        *,
        per_symbol_limit: int = 10_000,
    ) -> list[dict]:
        """Summarize an explicit symbol set with bounded set-based history reads."""
        requested = [str(symbol) for symbol in symbols]
        if not requested:
            return []

        bounded_row_limit = max(1, min(int(per_symbol_limit), 100_000))
        generated_at = time.time()
        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        unique_symbols = list(dict.fromkeys(symbol for symbol in requested if symbol))

        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                for start in range(0, len(unique_symbols), 32):
                    batch = unique_symbols[start : start + 32]
                    placeholders = ",".join("?" for _ in batch)
                    rows = conn.execute(
                        f"""
                        WITH ranked_history AS (
                            SELECT
                                history.{', history.'.join(_HISTORY_COLUMNS)},
                                ROW_NUMBER() OVER (
                                    PARTITION BY history.symbol
                                    ORDER BY history.observed_at DESC, history.id DESC
                                ) AS row_rank
                            FROM lbank_execution_observation_history AS history
                            WHERE history.symbol IN ({placeholders})
                        )
                        SELECT
                            {_HISTORY_SELECT},
                            row_rank
                        FROM ranked_history
                        WHERE row_rank <= ?
                        ORDER BY symbol ASC, row_rank ASC
                        """,
                        (*batch, bounded_row_limit),
                    ).fetchall()
                    for row in rows:
                        grouped[str(row["symbol"])].append(row)
        except Exception:
            logger.exception("Failed reading exact-symbol LBank execution statistics")
            return [
                self._empty_summary(
                    symbol,
                    since=None,
                    generated_at=generated_at,
                )
                for symbol in requested
            ]

        return [
            self._summary_from_rows(
                symbol,
                grouped.get(symbol, ()),
                since=None,
                generated_at=generated_at,
            )
            for symbol in requested
        ]


    def summarize_universe(
        self,
        *,
        symbol_limit: int = 10_000,
        per_symbol_limit: int = 10_000,
    ) -> list[dict]:
        """Return per-symbol summaries from one set-based history snapshot."""
        bounded_symbol_limit = max(1, min(int(symbol_limit), 100_000))
        bounded_row_limit = max(1, min(int(per_symbol_limit), 100_000))
        generated_at = time.time()

        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"""
                    WITH symbol_recency AS (
                        SELECT
                            symbol,
                            MAX(observed_at) AS latest_observed_at
                        FROM lbank_execution_observation_history
                        GROUP BY symbol
                    ),
                    selected_symbols AS (
                        SELECT
                            symbol,
                            ROW_NUMBER() OVER (
                                ORDER BY latest_observed_at DESC, symbol ASC
                            ) AS symbol_rank
                        FROM symbol_recency
                    ),
                    ranked_history AS (
                        SELECT
                            selected_symbols.symbol_rank,
                            history.{', history.'.join(_HISTORY_COLUMNS)},
                            ROW_NUMBER() OVER (
                                PARTITION BY history.symbol
                                ORDER BY history.observed_at DESC, history.id DESC
                            ) AS row_rank
                        FROM selected_symbols
                        JOIN lbank_execution_observation_history AS history
                            ON history.symbol = selected_symbols.symbol
                        WHERE selected_symbols.symbol_rank <= ?
                    )
                    SELECT
                        symbol_rank,
                        {_HISTORY_SELECT},
                        row_rank
                    FROM ranked_history
                    WHERE row_rank <= ?
                    ORDER BY symbol_rank ASC, row_rank ASC
                    """,
                    (bounded_symbol_limit, bounded_row_limit),
                ).fetchall()
        except Exception as exc:
            logger.error("Failed reading bulk LBank execution statistics: %s", exc)
            return []

        grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
        symbol_order: list[str] = []
        for row in rows:
            symbol = str(row["symbol"] or "")
            if not symbol:
                continue
            if symbol not in grouped:
                symbol_order.append(symbol)
            grouped[symbol].append(row)

        return [
            self._summary_from_rows(
                symbol,
                grouped[symbol],
                since=None,
                generated_at=generated_at,
            )
            for symbol in symbol_order
        ]

    def list_symbols(self, *, limit: int = 10_000) -> list[str]:
        symbol_limit = max(1, min(int(limit), 100_000))
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT symbol
                    FROM lbank_execution_observation_history
                    GROUP BY symbol
                    ORDER BY MAX(observed_at) DESC, symbol ASC
                    LIMIT ?
                    """,
                    (symbol_limit,),
                ).fetchall()
            return [str(row[0]) for row in rows if row and row[0]]
        except Exception as exc:
            logger.error("Failed listing LBank execution history symbols: %s", exc)
            return []

    @staticmethod
    def _median(values) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        midpoint = len(ordered) // 2
        if len(ordered) % 2:
            return float(ordered[midpoint])
        return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0

    def _empty_coverage_summary(
        self,
        generated_at: float,
        *,
        include_thresholds: bool,
    ) -> dict:
        return {
            "generated_at": generated_at,
            "history_rows": 0,
            "unique_symbols": 0,
            "observed_rows": 0,
            "unavailable_rows": 0,
            "availability_rate": None,
            "observation_count_distribution": {},
            "observed_count_distribution": {},
            "coverage_thresholds": (
                {str(value): 0 for value in _COVERAGE_THRESHOLDS}
                if include_thresholds
                else {}
            ),
            "evidence_status_counts": {
                EVIDENCE_NO_EVIDENCE: 0,
                EVIDENCE_INSUFFICIENT: 0,
                EVIDENCE_SUFFICIENT: 0,
            },
            "max_observations_per_symbol": 0,
            "max_observed_samples_per_symbol": 0,
            "median_observations_per_symbol": None,
            "median_observed_samples_per_symbol": None,
            "max_span_hours": None,
            "median_span_hours": None,
            "latest_observation_at": None,
            "latest_observation_age_seconds": None,
        }

    def coverage_summary(self, *, now: float | None = None) -> dict:
        generated_at = float(now if now is not None else time.time())
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        symbol,
                        COUNT(*) AS observation_count,
                        SUM(CASE WHEN observation_status = 'OBSERVED' THEN 1 ELSE 0 END)
                            AS observed_count,
                        SUM(CASE WHEN observation_status = 'UNAVAILABLE' THEN 1 ELSE 0 END)
                            AS unavailable_count,
                        MIN(CASE WHEN observation_status = 'OBSERVED' THEN observed_at END)
                            AS first_successful_observed_at,
                        MAX(CASE WHEN observation_status = 'OBSERVED' THEN observed_at END)
                            AS last_successful_observed_at
                    FROM lbank_execution_observation_history
                    GROUP BY symbol
                    """
                ).fetchall()
        except Exception as exc:
            logger.error("Failed reading LBank execution coverage summary: %s", exc)
            return self._empty_coverage_summary(
                generated_at,
                include_thresholds=False,
            )

        if not rows:
            return self._empty_coverage_summary(
                generated_at,
                include_thresholds=True,
            )

        attempt_counts = [int(row[1] or 0) for row in rows]
        observed_counts = [int(row[2] or 0) for row in rows]
        observed_rows = sum(observed_counts)
        unavailable_rows = sum(int(row[3] or 0) for row in rows)
        history_rows = observed_rows + unavailable_rows

        observation_count_distribution: dict[str, int] = {}
        for count in attempt_counts:
            key = str(count)
            observation_count_distribution[key] = (
                observation_count_distribution.get(key, 0) + 1
            )

        observed_count_distribution: dict[str, int] = {}
        for count in observed_counts:
            key = str(count)
            observed_count_distribution[key] = observed_count_distribution.get(key, 0) + 1

        coverage_thresholds = {
            str(threshold): sum(1 for count in observed_counts if count >= threshold)
            for threshold in _COVERAGE_THRESHOLDS
        }
        spans_hours: list[float] = []
        latest_observation_at: float | None = None
        evidence_status_counts = {
            EVIDENCE_NO_EVIDENCE: 0,
            EVIDENCE_INSUFFICIENT: 0,
            EVIDENCE_SUFFICIENT: 0,
        }

        for row in rows:
            observed_count = int(row[2] or 0)
            first_observed_at = self._finite(row[4])
            last_observed_at = self._finite(row[5])
            span_hours = None
            if first_observed_at is not None and last_observed_at is not None:
                span_hours = max(0.0, last_observed_at - first_observed_at) / 3600.0
                spans_hours.append(span_hours)
            if last_observed_at is not None and (
                latest_observation_at is None
                or last_observed_at > latest_observation_at
            ):
                latest_observation_at = last_observed_at
            evidence = self.evidence_sufficiency(
                observed_count=observed_count,
                observation_span_hours=span_hours,
            )
            evidence_status_counts[evidence["status"]] += 1

        latest_observation_age_seconds = (
            None
            if latest_observation_at is None
            else max(0.0, generated_at - latest_observation_at)
        )
        return {
            "generated_at": generated_at,
            "history_rows": history_rows,
            "unique_symbols": len(rows),
            "observed_rows": observed_rows,
            "unavailable_rows": unavailable_rows,
            "availability_rate": observed_rows / history_rows if history_rows else None,
            "observation_count_distribution": observation_count_distribution,
            "observed_count_distribution": observed_count_distribution,
            "coverage_thresholds": coverage_thresholds,
            "evidence_status_counts": evidence_status_counts,
            "max_observations_per_symbol": max(attempt_counts),
            "max_observed_samples_per_symbol": max(observed_counts),
            "median_observations_per_symbol": self._median(attempt_counts),
            "median_observed_samples_per_symbol": self._median(observed_counts),
            "max_span_hours": max(spans_hours) if spans_hours else None,
            "median_span_hours": self._median(spans_hours),
            "latest_observation_at": latest_observation_at,
            "latest_observation_age_seconds": latest_observation_age_seconds,
        }
