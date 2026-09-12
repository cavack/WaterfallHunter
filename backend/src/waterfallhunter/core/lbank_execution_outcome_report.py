import sqlite3
import time
from collections import Counter
from enum import Enum
from typing import Any


EXECUTION_STATUSES = (
    "SUITABLE",
    "MARGINAL",
    "POOR",
    "UNKNOWN",
)

DECISIVE_EXCLUSIONS = {
    "AMBIGUOUS_INTRACANDLE_PATH",
    "DATA_INCOMPLETE",
    "UNRESOLVABLE_SIGNAL_LEVELS",
    "UNRESOLVABLE_SIGNAL_SOURCE",
    "UNRESOLVABLE_TRIGGER_MINUTE",
}

PROXY_EXECUTION_COMPARISONS = (
    "AGREE_ACCEPT",
    "AGREE_REJECT",
    "VOLUME_PASS_EXECUTION_REJECT",
    "VOLUME_REJECT_EXECUTION_ACCEPT",
    "UNKNOWN",
)


class ReportCohort(str, Enum):
    STRICT = "STRICT"
    EXPERIMENTAL = "EXPERIMENTAL"
    MIXED_RESEARCH = "MIXED_RESEARCH"


class LBankExecutionOutcomeReport:
    """Read-only evidence report linking suitability snapshots to outcomes."""

    def __init__(
        self,
        db_path: str = "/app/data/waterfall_registry.db",
        *,
        cohort: ReportCohort = ReportCohort.STRICT,
        minimum_decisive_outcomes: int = 100,
        minimum_outcomes_per_status: int = 30,
        minimum_span_days: float = 14.0,
        horizon_seconds: int = 86_400,
        close_delay_seconds: int = 180,
    ):
        self.db_path = db_path
        self.cohort = ReportCohort(cohort)
        self.minimum_decisive_outcomes = max(
            1,
            int(minimum_decisive_outcomes),
        )
        self.minimum_outcomes_per_status = max(
            1,
            int(minimum_outcomes_per_status),
        )
        self.minimum_span_days = max(
            0.0,
            float(minimum_span_days),
        )
        self.horizon_seconds = max(
            60,
            int(horizon_seconds),
        )
        self.close_delay_seconds = max(
            60,
            int(close_delay_seconds),
        )

    @property
    def signal_class_scope(self) -> list[str]:
        if self.cohort is ReportCohort.STRICT:
            return ["STRICT"]
        if self.cohort is ReportCohort.EXPERIMENTAL:
            return ["EXPERIMENTAL"]
        return ["STRICT", "EXPERIMENTAL"]

    @property
    def research_only(self) -> bool:
        return self.cohort is not ReportCohort.STRICT

    @staticmethod
    def _rate(
        numerator: int,
        denominator: int,
    ) -> float | None:
        if denominator <= 0:
            return None
        return round(
            numerator / denominator,
            6,
        )

    @staticmethod
    def _quantile(
        values: list[float],
        quantile: float,
    ) -> float | None:
        finite = sorted(
            float(value)
            for value in values
            if isinstance(value, (int, float))
            and not isinstance(value, bool)
            and float("-inf") < float(value) < float("inf")
        )
        if not finite:
            return None
        position = (len(finite) - 1) * quantile
        lower = int(position)
        upper = min(lower + 1, len(finite) - 1)
        weight = position - lower
        return round(
            finite[lower] * (1.0 - weight)
            + finite[upper] * weight,
            6,
        )

    @staticmethod
    def _is_decisive(row: dict) -> bool:
        return row.get("outcome_status") not in DECISIVE_EXCLUSIONS

    def _group(self, rows: list[dict]) -> dict:
        settled = [
            row
            for row in rows
            if row.get("outcome_status") is not None
        ]
        decisive = [
            row
            for row in settled
            if self._is_decisive(row)
        ]
        statuses = Counter(
            row["outcome_status"]
            for row in settled
        )
        tp1 = sum(
            row.get("first_tp1_at") is not None
            for row in decisive
        )
        tp2 = sum(
            row.get("first_tp2_at") is not None
            for row in decisive
        )
        stop = sum(
            row.get("first_stop_at") is not None
            for row in decisive
        )
        return {
            "signal_count": len(rows),
            "settled_outcome_count": len(settled),
            "decisive_outcome_count": len(decisive),
            "outcome_status_counts": dict(
                sorted(statuses.items())
            ),
            "tp1_observed_rate": self._rate(
                tp1,
                len(decisive),
            ),
            "tp2_observed_rate": self._rate(
                tp2,
                len(decisive),
            ),
            "stop_observed_rate": self._rate(
                stop,
                len(decisive),
            ),
            "mfe_pct": {
                "p50": self._quantile(
                    [row.get("mfe_pct") for row in decisive],
                    0.50,
                ),
                "p90": self._quantile(
                    [row.get("mfe_pct") for row in decisive],
                    0.90,
                ),
            },
            "mae_pct": {
                "p50": self._quantile(
                    [row.get("mae_pct") for row in decisive],
                    0.50,
                ),
                "p90": self._quantile(
                    [row.get("mae_pct") for row in decisive],
                    0.90,
                ),
            },
        }

    def _rows(self) -> list[dict]:
        try:
            with sqlite3.connect(
                self.db_path,
                timeout=10.0,
            ) as conn:
                objects = {
                    (row[0], row[1])
                    for row in conn.execute(
                        """
                        SELECT type, name
                        FROM sqlite_master
                        WHERE name IN ('canonical_signal_view', 'lbank_signal_outcomes')
                        """
                    )
                }
                if not {
                    ("view", "canonical_signal_view"),
                    ("table", "lbank_signal_outcomes"),
                }.issubset(objects):
                    return []
                conn.row_factory = sqlite3.Row
                scope = self.signal_class_scope
                if len(scope) == 1:
                    cohort_clause = "s.signal_class = ?"
                    cohort_params: tuple[str, ...] = scope
                else:
                    cohort_clause = "s.signal_class IN (?, ?)"
                    cohort_params = scope
                sql = f"""
                    SELECT
                        s.signal_id,
                        s.symbol,
                        s.triggered_at,
                        s.execution_status,
                        s.volume_gate_passed,
                        s.proxy_execution_disagreement,
                        s.signal_class,
                        s.strategy_profile,
                        o.outcome_status,
                        o.first_tp1_at,
                        o.first_tp2_at,
                        o.first_stop_at,
                        o.mfe_pct,
                        o.mae_pct,
                        o.resolved_at
                    FROM canonical_signal_view AS s
                    LEFT JOIN lbank_signal_outcomes AS o
                        ON o.signal_id = s.signal_id
                    WHERE {cohort_clause}
                    ORDER BY s.triggered_at, s.signal_id
                """
                return [
                    dict(row)
                    for row in conn.execute(sql, cohort_params).fetchall()
                ]
        except sqlite3.Error:
            return []

    def build_report(
        self,
        *,
        now: int | None = None,
    ) -> dict:
        current_time = int(
            time.time()
            if now is None
            else now
        )
        rows = self._rows()
        settled = [
            row
            for row in rows
            if row.get("outcome_status") is not None
        ]
        mature_cutoff = (
            current_time
            - self.horizon_seconds
            - self.close_delay_seconds
        )
        mature = [
            row
            for row in rows
            if int(row["triggered_at"]) <= mature_cutoff
        ]
        mature_pending = [
            row
            for row in mature
            if row.get("outcome_status") is None
        ]
        oldest_mature_pending_age = (
            max(
                current_time
                - int(row["triggered_at"])
                - self.horizon_seconds
                - self.close_delay_seconds
                for row in mature_pending
            )
            if mature_pending
            else None
        )
        decisive = [
            row
            for row in settled
            if self._is_decisive(row)
        ]
        groups = {
            status: self._group(
                [
                    row
                    for row in rows
                    if (
                        row.get("execution_status")
                        or "UNKNOWN"
                    ) == status
                ]
            )
            for status in EXECUTION_STATUSES
        }
        proxy_execution_groups = {
            comparison: self._group(
                [
                    row
                    for row in rows
                    if (
                        row.get("proxy_execution_disagreement")
                        or "UNKNOWN"
                    ) == comparison
                ]
            )
            for comparison in PROXY_EXECUTION_COMPARISONS
        }

        triggered_times = [
            int(row["triggered_at"])
            for row in decisive
        ]
        span_days = (
            (
                max(triggered_times)
                - min(triggered_times)
            ) / 86_400.0
            if len(triggered_times) >= 2
            else 0.0
        )

        failed_checks = []
        if len(decisive) < self.minimum_decisive_outcomes:
            failed_checks.append(
                "minimum_decisive_outcomes"
            )
        if span_days < self.minimum_span_days:
            failed_checks.append(
                "minimum_observation_span_days"
            )
        for status in (
            "SUITABLE",
            "MARGINAL",
            "POOR",
        ):
            if (
                groups[status]["decisive_outcome_count"]
                < self.minimum_outcomes_per_status
            ):
                failed_checks.append(
                    f"minimum_{status.lower()}_outcomes"
                )

        evidence_ready = not failed_checks
        coverage = self._rate(
            len(
                [
                    row
                    for row in mature
                    if row.get("outcome_status") is not None
                ]
            ),
            len(mature),
        )

        return {
            "generated_at": current_time,
            "observational_only": True,
            "trade_eligible": None,
            "signal_class_scope": self.signal_class_scope,
            "research_only": self.research_only,
            "threshold_calibration_allowed": False,
            "hard_gating_allowed": False,
            "outcome_price_source": (
                "closed_1m_trade_ohlcv_proxy"
            ),
            "settlement": {
                "signal_count": len(rows),
                "mature_signal_count": len(mature),
                "settled_outcome_count": len(settled),
                "mature_settlement_coverage_rate": coverage,
                "unsettled_mature_signal_count": len(mature_pending),
                "oldest_unsettled_mature_age_seconds": (
                    oldest_mature_pending_age
                ),
            },
            "evidence": {
                "status": (
                    "SUFFICIENT_FOR_OBSERVATIONAL_COMPARISON"
                    if evidence_ready
                    else "INSUFFICIENT_EVIDENCE"
                ),
                "ready": evidence_ready,
                "decisive_outcome_count": len(decisive),
                "observation_span_days": round(
                    span_days,
                    6,
                ),
                "failed_checks": failed_checks,
                "requirements": {
                    "minimum_decisive_outcomes": (
                        self.minimum_decisive_outcomes
                    ),
                    "minimum_outcomes_per_status": (
                        self.minimum_outcomes_per_status
                    ),
                    "required_statuses": [
                        "SUITABLE",
                        "MARGINAL",
                        "POOR",
                    ],
                    "minimum_observation_span_days": (
                        self.minimum_span_days
                    ),
                },
            },
            "by_execution_status": groups,
            "by_proxy_execution_comparison": proxy_execution_groups,
            "comparative_metrics": (
                groups
                if evidence_ready
                else None
            ),
            "proxy_execution_comparative_metrics": (
                proxy_execution_groups
                if evidence_ready
                else None
            ),
        }
