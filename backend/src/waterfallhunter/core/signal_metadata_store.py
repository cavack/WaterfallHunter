from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from waterfallhunter.core.signal_metadata import SignalMetadataInput


class SignalMetadataError(RuntimeError):
    """Raised when canonical signal metadata cannot be read or is incomplete."""


@dataclass(frozen=True, slots=True)
class MetadataCompletenessResult:
    """Read-only completeness summary for ledger-to-metadata coverage."""

    complete: bool
    ledger_count: int
    metadata_count: int
    canonical_count: int
    missing_metadata_count: int
    orphan_metadata_count: int
    invalid_metadata_count: int
    reason_codes: tuple[str, ...]


_METADATA_INPUT_COLUMNS = (
    "signal_class",
    "strategy_profile",
    "score_version",
    "model_generation",
    "decision_contract_hash",
    "analysis_observed_at",
    "reference_observed_at",
    "metadata_contract_version",
    "classification_method",
    "classification_evidence_hash",
)
_METADATA_MIGRATION_VERSION = 3


def _open_read_only(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_file():
        raise SignalMetadataError("SIGNAL_METADATA_DATABASE_UNAVAILABLE")

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
        raise SignalMetadataError("SIGNAL_METADATA_DATABASE_UNREADABLE") from exc


def _require_schema(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT type, name FROM sqlite_master "
        "WHERE name IN ("
        "'lbank_signal_ledger','signal_metadata','canonical_signal_view',"
        "'schema_migrations'"
        ")"
    ).fetchall()
    objects = {(str(row[0]), str(row[1])) for row in rows}
    required = {
        ("table", "lbank_signal_ledger"),
        ("table", "signal_metadata"),
        ("table", "schema_migrations"),
        ("view", "canonical_signal_view"),
    }
    if not required.issubset(objects):
        raise SignalMetadataError("SIGNAL_METADATA_SCHEMA_UNAVAILABLE")


def _metadata_cutover_created_at(conn: sqlite3.Connection) -> int:
    """Return the immutable time at which canonical metadata became mandatory.

    Migration version 3 creates ``signal_metadata`` and its canonical view. The
    migration-history row is append-only/immutable, while normal signal ledger
    persistence stamps ``created_at`` from the process clock. Therefore rows
    created before this boundary are historical legacy evidence and may remain
    unclassified; rows created at or after it must have canonical metadata.
    """

    row = conn.execute(
        "SELECT applied_at FROM schema_migrations WHERE version = ?",
        (_METADATA_MIGRATION_VERSION,),
    ).fetchone()
    if row is None or type(row[0]) is not int or int(row[0]) < 0:
        raise SignalMetadataError("SIGNAL_METADATA_CUTOVER_UNAVAILABLE")
    return int(row[0])


def _scalar_count(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    if row is None:
        raise SignalMetadataError("SIGNAL_METADATA_QUERY_FAILED")
    return int(row[0])


def _invalid_metadata_count(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
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
        ORDER BY signal_id
        """
    )

    invalid = 0
    for row in rows:
        payload = dict(zip(_METADATA_INPUT_COLUMNS, row[:-1], strict=True))
        created_at = row[-1]
        try:
            SignalMetadataInput.model_validate(payload)
            if type(created_at) is not int or created_at < 0:
                raise ValueError("created_at must be a non-negative integer")
        except (ValidationError, ValueError, TypeError):
            invalid += 1
    return invalid


class SignalMetadataStore:
    """Read-only canonical metadata inspection helper.

    This class never creates, migrates, repairs, classifies, or backfills rows.
    Historical ledger rows that pre-date migration v3 may remain entirely
    unclassified and therefore outside the canonical view. If legacy metadata
    classification has begun, however, partial historical coverage remains a
    fail-closed condition. Every post-cutover signal always requires metadata.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)

    def verify_completeness(self) -> MetadataCompletenessResult:
        conn = _open_read_only(self._db_path)
        try:
            conn.execute("BEGIN")
            _require_schema(conn)
            cutover_created_at = _metadata_cutover_created_at(conn)
            cutoff = str(cutover_created_at)

            ledger_count = _scalar_count(
                conn,
                "SELECT COUNT(*) FROM lbank_signal_ledger",
            )
            metadata_count = _scalar_count(
                conn,
                "SELECT COUNT(*) FROM signal_metadata",
            )
            canonical_count = _scalar_count(
                conn,
                "SELECT COUNT(*) FROM canonical_signal_view",
            )
            post_cutover_missing_count = _scalar_count(
                conn,
                "SELECT COUNT(*) FROM lbank_signal_ledger AS s "
                "LEFT JOIN signal_metadata AS m ON m.signal_id = s.id "
                "WHERE m.signal_id IS NULL AND s.created_at >= " + cutoff,
            )
            legacy_ledger_count = _scalar_count(
                conn,
                "SELECT COUNT(*) FROM lbank_signal_ledger "
                "WHERE created_at < " + cutoff,
            )
            legacy_metadata_count = _scalar_count(
                conn,
                "SELECT COUNT(*) FROM signal_metadata AS m "
                "INNER JOIN lbank_signal_ledger AS s ON s.id = m.signal_id "
                "WHERE s.created_at < " + cutoff,
            )
            legacy_missing_count = (
                legacy_ledger_count - legacy_metadata_count
                if legacy_metadata_count > 0
                else 0
            )
            missing_metadata_count = (
                post_cutover_missing_count + max(0, legacy_missing_count)
            )
            orphan_metadata_count = _scalar_count(
                conn,
                "SELECT COUNT(*) FROM signal_metadata AS m "
                "LEFT JOIN lbank_signal_ledger AS s ON s.id = m.signal_id "
                "WHERE s.id IS NULL",
            )
            invalid_metadata_count = _invalid_metadata_count(conn)
        except SignalMetadataError:
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            raise SignalMetadataError("SIGNAL_METADATA_QUERY_FAILED") from exc
        finally:
            try:
                if conn.in_transaction:
                    conn.rollback()
            finally:
                conn.close()

        reasons: list[str] = []
        if missing_metadata_count:
            reasons.append("MISSING_METADATA")
        if orphan_metadata_count:
            reasons.append("ORPHAN_METADATA")
        if invalid_metadata_count:
            reasons.append("INVALID_METADATA")
        if canonical_count != metadata_count:
            reasons.append("CANONICAL_VIEW_COUNT_MISMATCH")

        return MetadataCompletenessResult(
            complete=not reasons,
            ledger_count=ledger_count,
            metadata_count=metadata_count,
            canonical_count=canonical_count,
            missing_metadata_count=missing_metadata_count,
            orphan_metadata_count=orphan_metadata_count,
            invalid_metadata_count=invalid_metadata_count,
            reason_codes=tuple(reasons),
        )


def verify_signal_metadata_completeness(
    db_path: str | Path,
) -> MetadataCompletenessResult:
    """Return a read-only completeness result for one migrated registry DB."""

    return SignalMetadataStore(db_path).verify_completeness()


def require_signal_metadata_completeness(
    db_path: str | Path,
) -> MetadataCompletenessResult:
    """Fail closed unless the canonical post-cutover metadata contract holds."""

    result = verify_signal_metadata_completeness(db_path)
    if result.complete:
        return result
    reasons = ",".join(result.reason_codes) or "UNKNOWN"
    raise SignalMetadataError(f"SIGNAL_METADATA_INCOMPLETE:{reasons}")
