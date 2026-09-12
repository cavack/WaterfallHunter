import math
from typing import Any

from waterfallhunter.core.lbank_execution_stats import (
    EVIDENCE_SUFFICIENT,
)


SUITABILITY_UNKNOWN = "UNKNOWN"
SUITABILITY_POOR = "POOR"
SUITABILITY_MARGINAL = "MARGINAL"
SUITABILITY_SUITABLE = "SUITABLE"


VALID_SUITABILITY_STATUSES = frozenset(
    {
        SUITABILITY_UNKNOWN,
        SUITABILITY_POOR,
        SUITABILITY_MARGINAL,
        SUITABILITY_SUITABLE,
    }
)


DEFAULT_SUITABLE_MIN_AVAILABILITY = 0.95
DEFAULT_SUITABLE_MAX_COST100_P90_PCT = 0.1225
DEFAULT_SUITABLE_MAX_SPREAD_P90_PCT = 0.1123
DEFAULT_SUITABLE_MIN_DEPTH25_P50_USDT = 3590.0

DEFAULT_MARGINAL_MIN_AVAILABILITY = 0.90
DEFAULT_MARGINAL_MAX_COST100_P90_PCT = 0.305
DEFAULT_MARGINAL_MAX_SPREAD_P90_PCT = 0.220
DEFAULT_MARGINAL_MIN_DEPTH25_P50_USDT = 1190.0


class LBankExecutionSuitability:
    """
    Read-only, observational execution-suitability classification.

    This layer intentionally does NOT:
    - mutate scan_eligible
    - mutate catalogue state
    - mutate hunter state
    - mutate score
    - place orders
    - send alerts
    - determine whether a trade should be entered

    The classification describes historical LBank execution quality only.

    Threshold provenance:
    - SUITABLE boundaries are approximately conservative cross-symbol
      quartile boundaries from the observed LBank execution universe.
    - MARGINAL boundaries approximately represent the wider observed
      execution envelope before the severe high-friction tail.
    - These thresholds are observational research thresholds, not live
      trading thresholds.
    """

    def __init__(
        self,
        *,
        suitable_min_availability: float = (
            DEFAULT_SUITABLE_MIN_AVAILABILITY
        ),
        suitable_max_cost100_p90_pct: float = (
            DEFAULT_SUITABLE_MAX_COST100_P90_PCT
        ),
        suitable_max_spread_p90_pct: float = (
            DEFAULT_SUITABLE_MAX_SPREAD_P90_PCT
        ),
        suitable_min_depth25_p50_usdt: float = (
            DEFAULT_SUITABLE_MIN_DEPTH25_P50_USDT
        ),
        marginal_min_availability: float = (
            DEFAULT_MARGINAL_MIN_AVAILABILITY
        ),
        marginal_max_cost100_p90_pct: float = (
            DEFAULT_MARGINAL_MAX_COST100_P90_PCT
        ),
        marginal_max_spread_p90_pct: float = (
            DEFAULT_MARGINAL_MAX_SPREAD_P90_PCT
        ),
        marginal_min_depth25_p50_usdt: float = (
            DEFAULT_MARGINAL_MIN_DEPTH25_P50_USDT
        ),
    ):
        self.suitable_min_availability = float(
            suitable_min_availability
        )

        self.suitable_max_cost100_p90_pct = float(
            suitable_max_cost100_p90_pct
        )

        self.suitable_max_spread_p90_pct = float(
            suitable_max_spread_p90_pct
        )

        self.suitable_min_depth25_p50_usdt = float(
            suitable_min_depth25_p50_usdt
        )

        self.marginal_min_availability = float(
            marginal_min_availability
        )

        self.marginal_max_cost100_p90_pct = float(
            marginal_max_cost100_p90_pct
        )

        self.marginal_max_spread_p90_pct = float(
            marginal_max_spread_p90_pct
        )

        self.marginal_min_depth25_p50_usdt = float(
            marginal_min_depth25_p50_usdt
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
            (int, float),
        ):
            return None

        number = float(
            value
        )

        if not math.isfinite(
            number
        ):
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

    def classify_summary(
        self,
        summary: dict | None,
    ) -> dict:
        if not isinstance(
            summary,
            dict,
        ):
            return self._unknown(
                symbol="",
                reason=(
                    "execution statistics summary missing"
                ),
            )

        symbol = str(
            summary.get(
                "symbol"
            )
            or ""
        )

        evidence = (
            summary.get(
                "evidence"
            )
            if isinstance(
                summary.get(
                    "evidence"
                ),
                dict,
            )
            else {}
        )

        evidence_status = str(
            evidence.get(
                "status"
            )
            or ""
        )

        if (
            evidence_status
            != EVIDENCE_SUFFICIENT
        ):
            return self._unknown(
                symbol=symbol,
                reason=(
                    "execution evidence is not sufficient"
                ),
                evidence_status=evidence_status,
            )

        availability = self._finite(
            summary.get(
                "availability_rate"
            )
        )

        cost100_p90 = self._metric(
            summary,
            "cost_100_pct",
            "p90",
        )

        spread_p90 = self._metric(
            summary,
            "spread_pct",
            "p90",
        )

        depth25_p50 = self._metric(
            summary,
            "depth_25bps_min_usdt",
            "p50",
        )

        required = {
            "availability_rate": availability,
            "cost_100_pct_p90": cost100_p90,
            "spread_pct_p90": spread_p90,
            "depth_25bps_min_usdt_p50": (
                depth25_p50
            ),
        }

        missing = [
            name
            for name, value
            in required.items()
            if value is None
        ]

        if missing:
            return self._unknown(
                symbol=symbol,
                reason=(
                    "required execution metrics missing"
                ),
                evidence_status=evidence_status,
                metrics=required,
                failed_checks=missing,
            )

        suitable_checks = {
            "availability": (
                availability
                >= self.suitable_min_availability
            ),
            "cost100_p90": (
                cost100_p90
                <= self.suitable_max_cost100_p90_pct
            ),
            "spread_p90": (
                spread_p90
                <= self.suitable_max_spread_p90_pct
            ),
            "depth25_p50": (
                depth25_p50
                >= self.suitable_min_depth25_p50_usdt
            ),
        }

        if all(
            suitable_checks.values()
        ):
            return self._result(
                symbol=symbol,
                status=SUITABILITY_SUITABLE,
                reason=(
                    "execution quality is within "
                    "the observational suitable envelope"
                ),
                evidence_status=evidence_status,
                metrics=required,
                checks=suitable_checks,
            )

        marginal_checks = {
            "availability": (
                availability
                >= self.marginal_min_availability
            ),
            "cost100_p90": (
                cost100_p90
                <= self.marginal_max_cost100_p90_pct
            ),
            "spread_p90": (
                spread_p90
                <= self.marginal_max_spread_p90_pct
            ),
            "depth25_p50": (
                depth25_p50
                >= self.marginal_min_depth25_p50_usdt
            ),
        }

        if all(
            marginal_checks.values()
        ):
            return self._result(
                symbol=symbol,
                status=SUITABILITY_MARGINAL,
                reason=(
                    "execution quality is usable but "
                    "outside the preferred observational envelope"
                ),
                evidence_status=evidence_status,
                metrics=required,
                checks=marginal_checks,
            )

        failed_checks = [
            name
            for name, passed
            in marginal_checks.items()
            if not passed
        ]

        return self._result(
            symbol=symbol,
            status=SUITABILITY_POOR,
            reason=(
                "execution quality is outside "
                "the marginal observational envelope"
            ),
            evidence_status=evidence_status,
            metrics=required,
            checks=marginal_checks,
            failed_checks=failed_checks,
        )

    def thresholds(
        self,
    ) -> dict:
        return {
            "suitable": {
                "minimum_availability_rate": (
                    self.suitable_min_availability
                ),
                "maximum_cost_100_p90_pct": (
                    self.suitable_max_cost100_p90_pct
                ),
                "maximum_spread_p90_pct": (
                    self.suitable_max_spread_p90_pct
                ),
                "minimum_depth_25bps_p50_usdt": (
                    self.suitable_min_depth25_p50_usdt
                ),
            },
            "marginal": {
                "minimum_availability_rate": (
                    self.marginal_min_availability
                ),
                "maximum_cost_100_p90_pct": (
                    self.marginal_max_cost100_p90_pct
                ),
                "maximum_spread_p90_pct": (
                    self.marginal_max_spread_p90_pct
                ),
                "minimum_depth_25bps_p50_usdt": (
                    self.marginal_min_depth25_p50_usdt
                ),
            },
        }

    def _unknown(
        self,
        *,
        symbol: str,
        reason: str,
        evidence_status: str | None = None,
        metrics: dict | None = None,
        failed_checks: list[str] | None = None,
    ) -> dict:
        return self._result(
            symbol=symbol,
            status=SUITABILITY_UNKNOWN,
            reason=reason,
            evidence_status=evidence_status,
            metrics=metrics,
            checks={},
            failed_checks=failed_checks,
        )

    def _result(
        self,
        *,
        symbol: str,
        status: str,
        reason: str,
        evidence_status: str | None,
        metrics: dict | None,
        checks: dict,
        failed_checks: list[str] | None = None,
    ) -> dict:
        return {
            "symbol": symbol,
            "status": status,
            "reason": reason,
            "evidence_status": (
                evidence_status
            ),
            "metrics": (
                metrics
                if isinstance(
                    metrics,
                    dict,
                )
                else {}
            ),
            "checks": dict(
                checks
            ),
            "failed_checks": (
                list(
                    failed_checks
                )
                if failed_checks
                else []
            ),
            "thresholds": self.thresholds(),
            "observational_only": True,
            "trade_eligible": None,
        }
