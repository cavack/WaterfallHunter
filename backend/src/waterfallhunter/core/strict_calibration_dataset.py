"""Read-only, STRICT-only calibration dataset construction."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.schema_contract import CURRENT_RUNTIME_SCHEMA_VERSION


DATASET_CONTRACT_VERSION = "strict_calibration_dataset_v1"
MANIFEST_CONTRACT_VERSION = "strict_calibration_dataset_manifest_v1"
DEFAULT_TARGET_HORIZON_SECONDS = 86_400
_UNUSABLE_OUTCOMES = (
    "DATA_INCOMPLETE",
    "UNRESOLVABLE_SIGNAL_LEVELS",
    "UNRESOLVABLE_TRIGGER_MINUTE",
)


class StrictCalibrationDatasetError(RuntimeError):
    """Raised when a calibration dataset cannot be built safely."""


@dataclass(frozen=True, slots=True)
class StrictCalibrationDataset:
    rows: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _timestamp(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StrictCalibrationDatasetError(
            f"{field} must be a non-negative integer UTC timestamp"
        )
    return value


def _open_read_only(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise StrictCalibrationDatasetError("CALIBRATION_DATABASE_UNAVAILABLE")
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    except (OSError, sqlite3.Error) as exc:
        if conn is not None:
            conn.close()
        raise StrictCalibrationDatasetError(
            "CALIBRATION_DATABASE_UNREADABLE"
        ) from exc


class StrictCalibrationDatasetBuilder:
    """Build an immutable manifest over a bounded, outcome-complete cohort."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def build(
        self,
        *,
        signal_window_start: int,
        signal_window_end: int,
        outcome_as_of: int,
        generated_at: int,
        source_revision: str,
        target_horizon_seconds: int = DEFAULT_TARGET_HORIZON_SECONDS,
    ) -> StrictCalibrationDataset:
        start = _timestamp(signal_window_start, "signal_window_start")
        end = _timestamp(signal_window_end, "signal_window_end")
        as_of = _timestamp(outcome_as_of, "outcome_as_of")
        generated = _timestamp(generated_at, "generated_at")
        horizon = _timestamp(target_horizon_seconds, "target_horizon_seconds")
        if not start < end <= as_of <= generated:
            raise StrictCalibrationDatasetError(
                "timestamps must satisfy start < end <= outcome_as_of <= generated_at"
            )
        if horizon == 0:
            raise StrictCalibrationDatasetError(
                "target_horizon_seconds must be greater than zero"
            )
        revision = str(source_revision).strip()
        if not revision or len(revision) > 128:
            raise StrictCalibrationDatasetError(
                "source_revision must be a stable non-empty identifier"
            )

        conn = _open_read_only(self.db_path)
        try:
            conn.execute("BEGIN")
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if schema_version != CURRENT_RUNTIME_SCHEMA_VERSION:
                raise StrictCalibrationDatasetError(
                    "CALIBRATION_SCHEMA_VERSION_MISMATCH"
                )
            rows = self._select_rows(
                conn,
                start=start,
                end=end,
                as_of=as_of,
                horizon=horizon,
            )
        except sqlite3.Error as exc:
            raise StrictCalibrationDatasetError(
                "CALIBRATION_DATASET_QUERY_FAILED"
            ) from exc
        finally:
            conn.close()

        row_hashes = [_sha256(row) for row in rows]
        manifest_body = {
            "contract_version": MANIFEST_CONTRACT_VERSION,
            "dataset_contract_version": DATASET_CONTRACT_VERSION,
            "cohort": {
                "signal_class": "STRICT",
                "join": "canonical_signal_view INNER JOIN lbank_signal_outcomes",
                "outcome_completeness": "observed_candles=expected_candles",
            },
            "signal_window": {
                "start": start,
                "end": end,
                "boundary": "[start,end)",
            },
            "outcome_cutoff": {
                "as_of": as_of,
                "boundary": "resolved_at<=as_of AND observation_ended_at<=as_of",
            },
            "target": {
                "name": "tp2_hit_within_horizon",
                "horizon_seconds": horizon,
                "positive_rule": "first_tp2_at IS NOT NULL",
            },
            "source": {
                "revision": revision,
                "database_schema_version": schema_version,
                "canonical_view": "canonical_signal_view",
            },
            "generated_at": generated,
            "row_count": len(rows),
            "signal_ids": [row["signal_id"] for row in rows],
            "row_hashes": row_hashes,
            "dataset_rows_sha256": _sha256(rows),
        }
        manifest = {
            **manifest_body,
            "dataset_manifest_sha256": _sha256(manifest_body),
        }
        return StrictCalibrationDataset(rows=tuple(rows), manifest=manifest)

    @staticmethod
    def _select_rows(
        conn: sqlite3.Connection,
        *,
        start: int,
        end: int,
        as_of: int,
        horizon: int,
    ) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in _UNUSABLE_OUTCOMES)
        sql = f"""
            SELECT
                s.signal_id,
                s.symbol,
                s.triggered_at AS signal_triggered_at,
                s.score AS predictive_evidence_score,
                s.strategy_profile,
                s.score_version,
                s.model_generation,
                s.decision_contract_hash,
                s.analysis_observed_at,
                s.reference_observed_at,
                o.outcome_status,
                o.horizon_seconds,
                o.first_tp1_at,
                o.first_tp2_at,
                o.first_stop_at,
                o.mfe_pct,
                o.mae_pct,
                o.observation_ended_at,
                o.resolved_at
            FROM canonical_signal_view AS s
            INNER JOIN lbank_signal_outcomes AS o
                ON o.signal_id = s.signal_id
            WHERE
                s.signal_class = 'STRICT'
                AND s.triggered_at >= ?
                AND s.triggered_at < ?
                AND s.analysis_observed_at <= s.triggered_at
                AND (
                    s.reference_observed_at IS NULL
                    OR s.reference_observed_at <= s.triggered_at
                )
                AND o.horizon_seconds = ?
                AND o.symbol = s.symbol
                AND o.signal_triggered_at = s.triggered_at
                AND o.observed_candles = o.expected_candles
                AND o.outcome_status NOT IN ({placeholders})
                AND o.resolved_at <= ?
                AND o.observation_ended_at IS NOT NULL
                AND o.observation_ended_at >= s.triggered_at
                AND o.observation_ended_at <= ?
                AND o.resolved_at >= o.observation_ended_at
                AND (
                    o.first_tp1_at IS NULL
                    OR o.first_tp1_at BETWEEN s.triggered_at AND o.observation_ended_at
                )
                AND (
                    o.first_tp2_at IS NULL
                    OR o.first_tp2_at BETWEEN s.triggered_at AND o.observation_ended_at
                )
                AND (
                    o.first_stop_at IS NULL
                    OR o.first_stop_at BETWEEN s.triggered_at AND o.observation_ended_at
                )
            ORDER BY s.triggered_at, s.signal_id
        """
        params = (start, end, horizon, *_UNUSABLE_OUTCOMES, as_of, as_of)
        selected: list[dict[str, Any]] = []
        for raw in conn.execute(sql, params).fetchall():
            row = dict(raw)
            row["target_tp2_hit_within_horizon"] = (
                row["first_tp2_at"] is not None
            )
            selected.append(row)
        return selected
