import logging
import math
import sqlite3
import threading
import time
from collections import Counter
from typing import Any

from waterfallhunter.core.lbank_execution_candidate import (
    LBankExecutionCandidateEnricher,
)
from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.schema_contract import require_managed_schema


logger = logging.getLogger(
    "WaterfallHunter.LBankExecutionDecision"
)


SOURCE_HUNTER_EVALUATION = "HUNTER_EVALUATION"
SOURCE_CATALOGUE_SNAPSHOT = "CATALOGUE_SNAPSHOT"

AGREE_ACCEPT = "AGREE_ACCEPT"
AGREE_REJECT = "AGREE_REJECT"
VOLUME_PASS_EXECUTION_REJECT = (
    "VOLUME_PASS_EXECUTION_REJECT"
)
VOLUME_REJECT_EXECUTION_ACCEPT = (
    "VOLUME_REJECT_EXECUTION_ACCEPT"
)
COMPARISON_UNKNOWN = "UNKNOWN"


class LBankExecutionDecisionLogger:
    """Persistent, observational comparison of volume and execution signals.

    The logger never changes catalogue eligibility, hunter state, scores,
    alerts, or orders. Hunter evaluations are aggregated in memory and flushed
    once per hunter cycle. A throttled catalogue snapshot also covers symbols
    rejected by the current volume gate, which makes false-negative proxy
    analysis possible without admitting those symbols to the hunter.
    """

    def __init__(
        self,
        db_path: str,
        *,
        enricher: LBankExecutionCandidateEnricher,
        evaluation_bucket_seconds: int = 3600,
        snapshot_interval_seconds: int = 3600,
        retention_days: int = 30,
        volume_gate_min_usdt: float = 2_000_000.0,
        verify_schema: bool = True,
    ):
        self.db_path = db_path
        self.enricher = enricher
        self.evaluation_bucket_seconds = max(60, int(evaluation_bucket_seconds))
        self.snapshot_interval_seconds = max(300, int(snapshot_interval_seconds))
        self.retention_seconds = max(86_400, int(retention_days) * 86_400)
        self.volume_gate_min_usdt = max(0.0, float(volume_gate_min_usdt))
        self._pending_lock = threading.Lock()
        self._pending: dict[tuple[int, str], dict] = {}
        self.total_evaluations_observed = 0
        self.total_evaluations_flushed = 0
        self.last_flush_at: float | None = None
        self.last_snapshot_at: float | None = None
        if verify_schema:
            require_managed_schema(
                self.db_path,
                required_tables=frozenset(
                    {"lbank_execution_decision_log", "lbank_catalog"}
                ),
            )

    def _connect(self):
        return connect_managed_sqlite(self.db_path, timeout=20.0)

    @staticmethod
    def _finite(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    @staticmethod
    def _shadow_admission(status: str) -> bool | None:
        if status in {"SUITABLE", "MARGINAL"}:
            return True
        if status == "POOR":
            return False
        return None

    def volume_gate_passes(self, quote_volume: Any) -> bool:
        volume = self._finite(quote_volume)
        return (
            volume is not None
            and volume >= self.volume_gate_min_usdt
        )

    @classmethod
    def comparison_kind(
        cls,
        volume_gate_passed: bool,
        suitability_status: str,
    ) -> str:
        shadow_admission = cls._shadow_admission(
            suitability_status
        )
        if shadow_admission is None:
            return COMPARISON_UNKNOWN
        if volume_gate_passed and shadow_admission:
            return AGREE_ACCEPT
        if not volume_gate_passed and not shadow_admission:
            return AGREE_REJECT
        if volume_gate_passed:
            return VOLUME_PASS_EXECUTION_REJECT
        return VOLUME_REJECT_EXECUTION_ACCEPT

    @classmethod
    def _row(
        cls,
        *,
        bucket_started_at: int,
        source: str,
        symbol: str,
        observed_at: float,
        evaluation_count: int,
        volume_gate_passed: bool,
        scan_eligible: bool,
        packet: dict,
        candidate_state: str | None,
        score: Any,
        quote_volume: Any,
        last_price: Any,
    ) -> dict:
        status = str(
            packet.get("status")
            or "UNKNOWN"
        )
        shadow_admission = cls._shadow_admission(
            status
        )
        evidence_status = packet.get(
            "evidence_status"
        )
        return {
            "bucket_started_at": bucket_started_at,
            "source": source,
            "symbol": str(symbol),
            "first_observed_at": observed_at,
            "last_observed_at": observed_at,
            "evaluation_count": max(1, int(evaluation_count)),
            "volume_gate_passed": int(volume_gate_passed),
            "suitability_status": status,
            "suitability_would_admit": (
                None
                if shadow_admission is None
                else int(shadow_admission)
            ),
            "disagreement_kind": cls.comparison_kind(
                volume_gate_passed,
                status,
            ),
            "evidence_status": (
                str(evidence_status)
                if evidence_status is not None
                else None
            ),
            "candidate_state": (
                str(candidate_state)
                if candidate_state is not None
                else None
            ),
            "score": cls._finite(score),
            "scan_eligible": int(scan_eligible),
            "quote_volume": cls._finite(quote_volume),
            "last_price": cls._finite(last_price),
        }

    def observe_evaluation(
        self,
        symbol: str,
        *,
        volume_gate_passed: bool,
        scan_eligible: bool = True,
        candidate_state: str | None,
        score: Any = None,
        quote_volume: Any = None,
        last_price: Any = None,
        observed_at: float | None = None,
        packet: dict | None = None,
    ) -> bool:
        try:
            timestamp = float(
                observed_at
                if observed_at is not None
                else time.time()
            )
            bucket = (
                int(timestamp)
                // self.evaluation_bucket_seconds
                * self.evaluation_bucket_seconds
            )
            suitability_packet = (
                dict(packet)
                if isinstance(packet, dict)
                else self.enricher.for_symbol(symbol)
            )
            row = self._row(
                bucket_started_at=bucket,
                source=SOURCE_HUNTER_EVALUATION,
                symbol=symbol,
                observed_at=timestamp,
                evaluation_count=1,
                volume_gate_passed=bool(volume_gate_passed),
                scan_eligible=bool(scan_eligible),
                packet=suitability_packet,
                candidate_state=candidate_state,
                score=score,
                quote_volume=quote_volume,
                last_price=last_price,
            )
            key = (bucket, str(symbol))
            with self._pending_lock:
                existing = self._pending.get(key)
                if existing is None:
                    self._pending[key] = row
                else:
                    row["first_observed_at"] = existing[
                        "first_observed_at"
                    ]
                    row["evaluation_count"] = (
                        int(existing["evaluation_count"])
                        + 1
                    )
                    self._pending[key] = row
                self.total_evaluations_observed += 1
            return True
        except Exception as exc:
            logger.warning(
                "Decision observation failed for %s: %s",
                symbol,
                exc,
            )
            return False

    @staticmethod
    def _upsert(conn, row: dict) -> None:
        conn.execute(
            """
            INSERT INTO lbank_execution_decision_log (
                bucket_started_at,
                source,
                symbol,
                first_observed_at,
                last_observed_at,
                evaluation_count,
                volume_gate_passed,
                suitability_status,
                suitability_would_admit,
                disagreement_kind,
                evidence_status,
                candidate_state,
                score,
                scan_eligible,
                quote_volume,
                last_price,
                observational_only,
                trade_eligible
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL
            )
            ON CONFLICT (
                bucket_started_at,
                source,
                symbol
            ) DO UPDATE SET
                last_observed_at = excluded.last_observed_at,
                evaluation_count = (
                    lbank_execution_decision_log.evaluation_count
                    + excluded.evaluation_count
                ),
                volume_gate_passed = excluded.volume_gate_passed,
                suitability_status = excluded.suitability_status,
                suitability_would_admit = excluded.suitability_would_admit,
                disagreement_kind = excluded.disagreement_kind,
                evidence_status = excluded.evidence_status,
                candidate_state = excluded.candidate_state,
                score = excluded.score,
                scan_eligible = excluded.scan_eligible,
                quote_volume = excluded.quote_volume,
                last_price = excluded.last_price,
                observational_only = 1,
                trade_eligible = NULL
            """,
            (
                row["bucket_started_at"],
                row["source"],
                row["symbol"],
                row["first_observed_at"],
                row["last_observed_at"],
                row["evaluation_count"],
                row["volume_gate_passed"],
                row["suitability_status"],
                row["suitability_would_admit"],
                row["disagreement_kind"],
                row["evidence_status"],
                row["candidate_state"],
                row["score"],
                row["scan_eligible"],
                row["quote_volume"],
                row["last_price"],
            ),
        )

    def flush_evaluations(self) -> dict:
        with self._pending_lock:
            rows = list(self._pending.values())
            self._pending.clear()

        if not rows:
            return {
                "persisted": True,
                "rows": 0,
                "evaluations": 0,
            }

        try:
            with self._connect() as conn:
                for row in rows:
                    self._upsert(conn, row)
            evaluations = sum(
                int(row["evaluation_count"])
                for row in rows
            )
            self.total_evaluations_flushed += evaluations
            self.last_flush_at = time.time()
            return {
                "persisted": True,
                "rows": len(rows),
                "evaluations": evaluations,
            }
        except Exception as exc:
            with self._pending_lock:
                for row in rows:
                    key = (
                        int(row["bucket_started_at"]),
                        str(row["symbol"]),
                    )
                    existing = self._pending.get(key)
                    if existing is not None:
                        row["evaluation_count"] += int(
                            existing["evaluation_count"]
                        )
                        row["last_observed_at"] = max(
                            row["last_observed_at"],
                            existing["last_observed_at"],
                        )
                    self._pending[key] = row
            logger.error(
                "Decision evaluation flush failed: %s",
                exc,
            )
            return {
                "persisted": False,
                "rows": 0,
                "evaluations": 0,
            }

    def record_universe_snapshot(
        self,
        *,
        now: float | None = None,
        force: bool = False,
    ) -> dict:
        timestamp = float(
            now
            if now is not None
            else time.time()
        )
        bucket = (
            int(timestamp)
            // self.snapshot_interval_seconds
            * self.snapshot_interval_seconds
        )
        try:
            with self._connect() as conn:
                if not force:
                    existing = conn.execute(
                        """
                        SELECT 1
                        FROM lbank_execution_decision_log
                        WHERE
                            source = ?
                            AND bucket_started_at = ?
                        LIMIT 1
                        """,
                        (
                            SOURCE_CATALOGUE_SNAPSHOT,
                            bucket,
                        ),
                    ).fetchone()
                    if existing is not None:
                        return {
                            "persisted": True,
                            "skipped": True,
                            "rows": 0,
                        }

                conn.row_factory = sqlite3.Row
                catalogue = conn.execute(
                    """
                    SELECT
                        symbol,
                        scan_eligible,
                        status,
                        quote_volume,
                        last_price
                    FROM lbank_catalog
                    WHERE status != 'REMOVED'
                    ORDER BY symbol
                    """
                ).fetchall()

            rows = []
            for item in catalogue:
                symbol = str(item["symbol"])
                packet = self.enricher.for_symbol(symbol)
                rows.append(
                    self._row(
                        bucket_started_at=bucket,
                        source=SOURCE_CATALOGUE_SNAPSHOT,
                        symbol=symbol,
                        observed_at=timestamp,
                        evaluation_count=1,
                        volume_gate_passed=bool(
                            self.volume_gate_passes(
                                item["quote_volume"]
                            )
                        ),
                        scan_eligible=bool(
                            item["scan_eligible"]
                        ),
                        packet=packet,
                        candidate_state=str(
                            item["status"]
                            or ""
                        ),
                        score=None,
                        quote_volume=item["quote_volume"],
                        last_price=item["last_price"],
                    )
                )

            with self._connect() as conn:
                for row in rows:
                    self._upsert(conn, row)
                conn.execute(
                    """
                    DELETE FROM lbank_execution_decision_log
                    WHERE last_observed_at < ?
                    """,
                    (
                        timestamp
                        - self.retention_seconds,
                    ),
                )

            self.last_snapshot_at = timestamp
            return {
                "persisted": True,
                "skipped": False,
                "rows": len(rows),
            }
        except Exception as exc:
            logger.error(
                "Decision universe snapshot failed: %s",
                exc,
            )
            return {
                "persisted": False,
                "skipped": False,
                "rows": 0,
            }

    def summary(self, *, hours: float = 24.0) -> dict:
        since = time.time() - max(1.0, float(hours)) * 3600.0
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        source,
                        disagreement_kind,
                        SUM(evaluation_count),
                        COUNT(DISTINCT symbol)
                    FROM lbank_execution_decision_log
                    WHERE last_observed_at >= ?
                    GROUP BY source, disagreement_kind
                    """,
                    (since,),
                ).fetchall()
            counts = Counter()
            symbols = Counter()
            for source, kind, count, symbol_count in rows:
                counts[(str(source), str(kind))] = int(count or 0)
                symbols[(str(source), str(kind))] = int(
                    symbol_count or 0
                )
            return {
                "observational_only": True,
                "trade_eligible": None,
                "hours": float(hours),
                "sources": {
                    source: {
                        kind: {
                            "evaluations": counts[(source, kind)],
                            "symbols": symbols[(source, kind)],
                        }
                        for kind in (
                            AGREE_ACCEPT,
                            AGREE_REJECT,
                            VOLUME_PASS_EXECUTION_REJECT,
                            VOLUME_REJECT_EXECUTION_ACCEPT,
                            COMPARISON_UNKNOWN,
                        )
                    }
                    for source in (
                        SOURCE_HUNTER_EVALUATION,
                        SOURCE_CATALOGUE_SNAPSHOT,
                    )
                },
            }
        except Exception as exc:
            logger.error(
                "Decision summary failed: %s",
                exc,
            )
            return {
                "observational_only": True,
                "trade_eligible": None,
                "hours": float(hours),
                "sources": {},
                "error": "decision summary unavailable",
            }
