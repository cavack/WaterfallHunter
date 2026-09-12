from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from waterfallhunter.core.contracts import SignalClass
from waterfallhunter.core.managed_sqlite import ManagedSQLiteError, connect_managed_sqlite
from waterfallhunter.core.schema_contract import CURRENT_RUNTIME_SCHEMA_VERSION
from waterfallhunter.core.signal_metadata import (
    ClassificationMethod,
    EXPERIMENTAL_STRATEGY_PROFILE,
    METADATA_CONTRACT_VERSION,
    STRICT_STRATEGY_PROFILE,
    SignalMetadataInput,
    canonical_sha256,
)


_MINIMUM_SCHEMA_VERSION = 4
_EVIDENCE_CONTRACT_VERSION = "legacy_signal_classification_evidence_v1"
_REPORT_CONTRACT_VERSION = "legacy_signal_classification_report_v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class LegacyClassificationError(RuntimeError):
    """Raised when legacy classification cannot be safely previewed or applied."""


class LegacyClassificationStatus(str, Enum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class LegacyClassificationDecision:
    signal_id: int
    status: LegacyClassificationStatus
    reason_codes: tuple[str, ...]
    evidence_hash: str | None
    metadata: SignalMetadataInput | None


@dataclass(frozen=True, slots=True)
class LegacyClassificationReport:
    total_count: int
    resolved_count: int
    unresolved_count: int
    conflict_count: int
    resolved_ids: tuple[int, ...]
    unresolved_ids: tuple[int, ...]
    conflict_ids: tuple[int, ...]
    report_hash: str
    decisions: tuple[LegacyClassificationDecision, ...]


def _signal_id(signal_row: Mapping[str, Any]) -> int:
    value = signal_row.get("id", signal_row.get("signal_id"))
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LegacyClassificationError("INVALID_SIGNAL_ROW_ID")
    return value


def _parse_metrics(signal_row: Mapping[str, Any]) -> dict[str, Any] | None:
    raw = signal_row.get("trigger_metrics_json")
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _is_timestamp(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _unresolved(
    signal_id: int,
    *reasons: str,
) -> LegacyClassificationDecision:
    return LegacyClassificationDecision(
        signal_id=signal_id,
        status=LegacyClassificationStatus.UNRESOLVED,
        reason_codes=tuple(reasons),
        evidence_hash=None,
        metadata=None,
    )


def _conflict(
    signal_id: int,
    evidence_hash: str | None,
    *reasons: str,
) -> LegacyClassificationDecision:
    return LegacyClassificationDecision(
        signal_id=signal_id,
        status=LegacyClassificationStatus.CONFLICT,
        reason_codes=tuple(reasons),
        evidence_hash=evidence_hash,
        metadata=None,
    )


def classify_legacy_evidence(
    signal_row: Mapping[str, Any],
) -> LegacyClassificationDecision:
    """Classify one legacy ledger row from persisted evidence only.

    No application defaults, validator state, settings, or wall clock values are
    used to reconstruct historical lineage. Missing mandatory evidence remains
    unresolved rather than defaulting to STRICT or EXPERIMENTAL.
    """

    signal_id = _signal_id(signal_row)
    metrics = _parse_metrics(signal_row)
    if metrics is None:
        return _unresolved(
            signal_id,
            "MALFORMED_TRIGGER_METRICS",
            "MISSING_MANDATORY_EVIDENCE",
        )

    strategy_profile = metrics.get("strategy_profile")
    score_version = metrics.get("score_version")
    model_generation = metrics.get("model_generation")
    decision_contract_hash = metrics.get("decision_contract_hash")
    analysis_observed_at = metrics.get("analysis_observed_at")
    reference_observed_at = metrics.get("reference_observed_at")

    mandatory_valid = (
        _is_nonempty_string(strategy_profile)
        and _is_nonempty_string(score_version)
        and _is_nonempty_string(model_generation)
        and isinstance(decision_contract_hash, str)
        and _SHA256_RE.fullmatch(decision_contract_hash) is not None
        and _is_timestamp(analysis_observed_at)
    )
    reference_valid = reference_observed_at is None or _is_timestamp(
        reference_observed_at
    )
    if not mandatory_valid or not reference_valid:
        return _unresolved(signal_id, "MISSING_MANDATORY_EVIDENCE")

    lineage = {
        STRICT_STRATEGY_PROFILE: (SignalClass.STRICT, "score_v2"),
        EXPERIMENTAL_STRATEGY_PROFILE: (
            SignalClass.EXPERIMENTAL,
            "score_v2_watch_v1",
        ),
    }.get(strategy_profile)
    if lineage is None:
        return _unresolved(signal_id, "UNKNOWN_STRATEGY_PROFILE")

    signal_class, expected_score_version = lineage
    explicit_signal_class = metrics.get("signal_class")
    evidence_envelope = {
        "contract_version": _EVIDENCE_CONTRACT_VERSION,
        "signal_id": signal_id,
        "strategy_profile": strategy_profile,
        "score_version": score_version,
        "model_generation": model_generation,
        "decision_contract_hash": decision_contract_hash,
        "analysis_observed_at": analysis_observed_at,
        "reference_observed_at": reference_observed_at,
        "explicit_signal_class": explicit_signal_class,
    }
    evidence_hash = canonical_sha256(evidence_envelope)

    if score_version != expected_score_version:
        return _conflict(signal_id, evidence_hash, "SCORE_VERSION_CONFLICT")

    if explicit_signal_class is not None and (
        not isinstance(explicit_signal_class, str)
        or explicit_signal_class != signal_class.value
    ):
        return _conflict(signal_id, evidence_hash, "SIGNAL_CLASS_CONFLICT")

    metadata = SignalMetadataInput(
        signal_class=signal_class,
        strategy_profile=strategy_profile,
        score_version=score_version,
        model_generation=model_generation,
        decision_contract_hash=decision_contract_hash,
        analysis_observed_at=analysis_observed_at,
        reference_observed_at=reference_observed_at,
        metadata_contract_version=METADATA_CONTRACT_VERSION,
        classification_method=ClassificationMethod.LEGACY_PROFILE_EXACT_MATCH,
        classification_evidence_hash=evidence_hash,
    )
    return LegacyClassificationDecision(
        signal_id=signal_id,
        status=LegacyClassificationStatus.RESOLVED,
        reason_codes=(),
        evidence_hash=evidence_hash,
        metadata=metadata,
    )


def _require_schema(conn: sqlite3.Connection) -> None:
    version_row = conn.execute("PRAGMA user_version").fetchone()
    version = int(version_row[0]) if version_row is not None else -1
    if version < _MINIMUM_SCHEMA_VERSION:
        raise LegacyClassificationError("LEGACY_CLASSIFICATION_REQUIRES_SCHEMA_V4_OR_LATER")
    if version > CURRENT_RUNTIME_SCHEMA_VERSION:
        raise LegacyClassificationError("LEGACY_CLASSIFICATION_FUTURE_SCHEMA_UNSUPPORTED")

    rows = conn.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE name IN ('lbank_signal_ledger','signal_metadata','canonical_signal_view')"
    ).fetchall()
    objects = {(str(row[0]), str(row[1])) for row in rows}
    required = {
        ("table", "lbank_signal_ledger"),
        ("table", "signal_metadata"),
        ("view", "canonical_signal_view"),
    }
    if not required.issubset(objects):
        raise LegacyClassificationError("LEGACY_CLASSIFICATION_SCHEMA_UNAVAILABLE")


def _classify_all(conn: sqlite3.Connection) -> tuple[LegacyClassificationDecision, ...]:
    rows = conn.execute(
        "SELECT id, trigger_metrics_json FROM lbank_signal_ledger ORDER BY id"
    ).fetchall()
    return tuple(
        classify_legacy_evidence(
            {
                "id": int(row[0]),
                "trigger_metrics_json": row[1],
            }
        )
        for row in rows
    )


def _decision_payload(decision: LegacyClassificationDecision) -> dict[str, Any]:
    metadata_payload = (
        decision.metadata.model_dump(mode="json")
        if decision.metadata is not None
        else None
    )
    return {
        "signal_id": decision.signal_id,
        "status": decision.status.value,
        "reason_codes": list(decision.reason_codes),
        "evidence_hash": decision.evidence_hash,
        "metadata": metadata_payload,
    }


def _build_report(
    decisions: tuple[LegacyClassificationDecision, ...],
) -> LegacyClassificationReport:
    resolved_ids = tuple(
        item.signal_id
        for item in decisions
        if item.status is LegacyClassificationStatus.RESOLVED
    )
    unresolved_ids = tuple(
        item.signal_id
        for item in decisions
        if item.status is LegacyClassificationStatus.UNRESOLVED
    )
    conflict_ids = tuple(
        item.signal_id
        for item in decisions
        if item.status is LegacyClassificationStatus.CONFLICT
    )
    payload = {
        "contract_version": _REPORT_CONTRACT_VERSION,
        "total_count": len(decisions),
        "resolved_ids": list(resolved_ids),
        "unresolved_ids": list(unresolved_ids),
        "conflict_ids": list(conflict_ids),
        "decisions": [_decision_payload(item) for item in decisions],
    }
    return LegacyClassificationReport(
        total_count=len(decisions),
        resolved_count=len(resolved_ids),
        unresolved_count=len(unresolved_ids),
        conflict_count=len(conflict_ids),
        resolved_ids=resolved_ids,
        unresolved_ids=unresolved_ids,
        conflict_ids=conflict_ids,
        report_hash=canonical_sha256(payload),
        decisions=decisions,
    )


def _open_read_only(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise LegacyClassificationError("LEGACY_CLASSIFICATION_DATABASE_UNAVAILABLE")
    conn: sqlite3.Connection | None = None
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(
            uri,
            uri=True,
            timeout=5.0,
            isolation_level=None,
        )
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn
    except (OSError, sqlite3.Error) as exc:
        if conn is not None:
            conn.close()
        raise LegacyClassificationError(
            "LEGACY_CLASSIFICATION_DATABASE_UNREADABLE"
        ) from exc


def preview_legacy_classification(
    db_path: str | Path,
) -> LegacyClassificationReport:
    """Classify all ledger rows through a strictly read-only connection."""

    conn = _open_read_only(db_path)
    try:
        conn.execute("BEGIN")
        _require_schema(conn)
        return _build_report(_classify_all(conn))
    except sqlite3.Error as exc:
        raise LegacyClassificationError("LEGACY_CLASSIFICATION_PREVIEW_FAILED") from exc
    finally:
        try:
            if conn.in_transaction:
                conn.rollback()
        finally:
            conn.close()


def _metadata_tuple(
    metadata: SignalMetadataInput,
    created_at: int,
) -> tuple[Any, ...]:
    return (
        metadata.signal_class.value,
        metadata.strategy_profile,
        metadata.score_version,
        metadata.model_generation,
        metadata.decision_contract_hash,
        metadata.analysis_observed_at,
        metadata.reference_observed_at,
        metadata.metadata_contract_version,
        metadata.classification_method.value,
        metadata.classification_evidence_hash,
        created_at,
    )


def _persist_resolved_decision(
    conn: sqlite3.Connection,
    decision: LegacyClassificationDecision,
    *,
    operation_created_at: int,
    explicit_created_at: int | None,
) -> None:
    if decision.status is not LegacyClassificationStatus.RESOLVED:
        return
    metadata = decision.metadata
    if metadata is None:
        raise LegacyClassificationError("RESOLVED_METADATA_MISSING")

    existing = conn.execute(
        """
        SELECT
            signal_class,
            strategy_profile,
            score_version,
            model_generation,
            decision_contract_hash,
            analysis_observed_at,
            reference_observed_at,
            metadata_contract_version,
            classification_method,
            classification_evidence_hash,
            created_at
        FROM signal_metadata
        WHERE signal_id = ?
        """,
        (decision.signal_id,),
    ).fetchone()
    expected_created_at = (
        int(existing[-1])
        if existing is not None and explicit_created_at is None
        else operation_created_at
    )
    expected = _metadata_tuple(metadata, expected_created_at)
    if existing is not None:
        # created_at records when the immutable row was first materialized. A
        # later idempotent replay may supply a different operation timestamp;
        # that must neither rewrite the original value nor create a conflict.
        if tuple(existing[:-1]) != expected[:-1]:
            raise LegacyClassificationError("EXISTING_METADATA_CONFLICT")
        return

    conn.execute(
        """
        INSERT INTO signal_metadata (
            signal_id,
            signal_class,
            strategy_profile,
            score_version,
            model_generation,
            decision_contract_hash,
            analysis_observed_at,
            reference_observed_at,
            metadata_contract_version,
            classification_method,
            classification_evidence_hash,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (decision.signal_id, *expected),
    )


def apply_legacy_classification(
    db_path: str | Path,
    *,
    expected_report_hash: str,
    created_at: int | None = None,
) -> LegacyClassificationReport:
    """Append resolved legacy metadata after exact preview-hash verification."""

    path = Path(db_path)
    if not path.is_file():
        raise LegacyClassificationError("LEGACY_CLASSIFICATION_DATABASE_UNAVAILABLE")
    if created_at is not None and not _is_timestamp(created_at):
        raise LegacyClassificationError("INVALID_CLASSIFICATION_CREATED_AT")

    try:
        conn = connect_managed_sqlite(
            path,
            timeout=10.0,
            isolation_level=None,
        )
    except ManagedSQLiteError as exc:
        raise LegacyClassificationError(
            "LEGACY_CLASSIFICATION_DATABASE_UNSAFE"
        ) from exc
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("BEGIN IMMEDIATE")
        _require_schema(conn)
        report = _build_report(_classify_all(conn))
        if report.report_hash != expected_report_hash:
            raise LegacyClassificationError("REPORT_HASH_MISMATCH")

        operation_created_at = int(time.time()) if created_at is None else created_at
        for decision in report.decisions:
            _persist_resolved_decision(
                conn,
                decision,
                operation_created_at=operation_created_at,
                explicit_created_at=created_at,
            )

        conn.commit()
        return report
    except LegacyClassificationError:
        if conn.in_transaction:
            conn.rollback()
        raise
    except sqlite3.Error as exc:
        if conn.in_transaction:
            conn.rollback()
        raise LegacyClassificationError("LEGACY_CLASSIFICATION_APPLY_FAILED") from exc
    finally:
        conn.close()
