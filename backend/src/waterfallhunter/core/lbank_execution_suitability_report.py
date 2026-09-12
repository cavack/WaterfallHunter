from collections import Counter
from typing import Any

from waterfallhunter.core.lbank_execution_stats import LBankExecutionStats
from waterfallhunter.core.lbank_execution_suitability import (
    LBankExecutionSuitability,
    SUITABILITY_MARGINAL,
    SUITABILITY_POOR,
    SUITABILITY_SUITABLE,
    SUITABILITY_UNKNOWN,
)

SUITABILITY_ORDER = (
    SUITABILITY_SUITABLE,
    SUITABILITY_MARGINAL,
    SUITABILITY_POOR,
    SUITABILITY_UNKNOWN,
)


class LBankExecutionSuitabilityReport:
    """Read-only universe reporting over historical LBank execution evidence."""

    def __init__(
        self,
        stats: LBankExecutionStats,
        classifier: LBankExecutionSuitability | None = None,
    ):
        self.stats = stats
        self.classifier = classifier if classifier is not None else LBankExecutionSuitability()

    @staticmethod
    def _sort_value(value: Any, *, default: float) -> float:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        return default

    @classmethod
    def _sort_key(cls, row: dict):
        status = row.get("status")
        metrics = row.get("metrics") or {}
        cost = metrics.get("cost_100_pct_p90")
        depth = metrics.get("depth_25bps_min_usdt_p50")

        if status in {SUITABILITY_SUITABLE, SUITABILITY_MARGINAL}:
            return (
                cls._sort_value(cost, default=float("inf")),
                -cls._sort_value(depth, default=0.0),
                str(row.get("symbol") or ""),
            )
        if status == SUITABILITY_POOR:
            return (
                -len(row.get("failed_checks") or []),
                -cls._sort_value(cost, default=0.0),
                str(row.get("symbol") or ""),
            )
        return (str(row.get("symbol") or ""),)

    def _classify_summary(self, summary: dict) -> dict:
        result = dict(self.classifier.classify_summary(summary))
        evidence = summary.get("evidence") if isinstance(summary, dict) else {}
        evidence = evidence or {}
        result["observed_samples"] = evidence.get("observed_samples")
        result["observation_span_hours"] = evidence.get("observation_span_hours")
        result["availability_rate"] = (
            summary.get("availability_rate") if isinstance(summary, dict) else None
        )
        return result

    def classify_symbol(self, symbol: str) -> dict:
        return self._classify_summary(self.stats.summarize_symbol(symbol))

    def build_report(
        self,
        *,
        symbol_limit: int = 10_000,
        examples_per_status: int = 20,
    ) -> dict:
        limit = max(1, min(int(symbol_limit), 100_000))
        example_limit = max(0, min(int(examples_per_status), 1_000))

        summaries = self.stats.summarize_universe(
            symbol_limit=limit,
            per_symbol_limit=10_000,
        )
        classified = [self._classify_summary(summary) for summary in summaries]

        status_counts = Counter(row.get("status") for row in classified)
        failed_check_counts = Counter()
        poor_failed_check_counts = Counter()
        for row in classified:
            for check in row.get("failed_checks") or []:
                check_name = str(check)
                failed_check_counts[check_name] += 1
                if row.get("status") == SUITABILITY_POOR:
                    poor_failed_check_counts[check_name] += 1

        examples = {}
        for status in SUITABILITY_ORDER:
            rows = [row for row in classified if row.get("status") == status]
            rows.sort(key=self._sort_key)
            examples[status] = rows[:example_limit]

        coverage = self.stats.coverage_summary()
        known_count = sum(
            status_counts.get(status, 0)
            for status in (
                SUITABILITY_SUITABLE,
                SUITABILITY_MARGINAL,
                SUITABILITY_POOR,
            )
        )
        total = len(classified)

        return {
            "observational_only": True,
            "trade_eligible": None,
            "symbol_count": total,
            "known_classification_count": known_count,
            "unknown_classification_count": status_counts.get(SUITABILITY_UNKNOWN, 0),
            "classification_rate": known_count / total if total else None,
            "status_counts": {
                status: status_counts.get(status, 0) for status in SUITABILITY_ORDER
            },
            "failed_check_counts": dict(sorted(failed_check_counts.items())),
            "poor_failed_check_counts": dict(sorted(poor_failed_check_counts.items())),
            "thresholds": self.classifier.thresholds(),
            "coverage": {
                "history_rows": coverage.get("history_rows"),
                "unique_symbols": coverage.get("unique_symbols"),
                "observed_rows": coverage.get("observed_rows"),
                "unavailable_rows": coverage.get("unavailable_rows"),
                "availability_rate": coverage.get("availability_rate"),
                "evidence_status_counts": coverage.get("evidence_status_counts"),
                "median_observed_samples_per_symbol": coverage.get(
                    "median_observed_samples_per_symbol"
                ),
                "median_span_hours": coverage.get("median_span_hours"),
                "latest_observation_age_seconds": coverage.get(
                    "latest_observation_age_seconds"
                ),
            },
            "examples": examples,
        }
