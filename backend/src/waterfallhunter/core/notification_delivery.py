"""Leased, crash-aware delivery for immutable domain outbox events."""

from __future__ import annotations

import asyncio
import logging
import math
import random
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import closing
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from waterfallhunter.core.entry_decision_store import EntryDecisionStore
from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.schema_contract import require_managed_schema


logger = logging.getLogger("WaterfallHunter.NotificationDelivery")


class DeliveryDisposition(str, Enum):
    DELIVERED = "DELIVERED"
    RATE_LIMITED = "RATE_LIMITED"
    DELIVERY_UNCERTAIN = "DELIVERY_UNCERTAIN"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    disposition: DeliveryDisposition
    error_code: str | None = None
    retry_after_seconds: int | None = None


class NotificationTransport(Protocol):
    async def deliver(self, event: dict[str, Any]) -> DeliveryResult: ...


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    event_id: str
    event_key: str
    event_type: str
    payload_contract_version: str
    payload_json: str
    payload_hash: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    event_id: str
    state: str
    attempt_count: int
    next_available_at: int | None
    error_code: str | None


class NotificationDeliveryError(RuntimeError):
    """Raised when durable delivery state cannot be updated safely."""


_ALLOWED_OUTBOX_TABLES = frozenset({"domain_outbox_events", "entry_notification_outbox"})


def _outbox_table(value: str) -> str:
    table = str(value or "").strip()
    if table not in _ALLOWED_OUTBOX_TABLES:
        raise ValueError("unsupported notification outbox table")
    return table


class DurableNotificationWorker:
    """At-least-once transport with leases and explicit uncertainty."""

    def __init__(
        self,
        db_path: str | Path,
        transport: NotificationTransport,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        max_attempts: int = 6,
        base_backoff_seconds: int = 5,
        max_backoff_seconds: int = 900,
        transport_timeout_seconds: float | None = None,
        advisory_wait_seconds: int = 0,
        jitter: Callable[[], float] = random.random,
        outbox_table: str = "domain_outbox_events",
        verify_schema: bool = True,
    ):
        if not worker_id.strip() or len(worker_id) > 128:
            raise ValueError("worker_id must be a bounded non-empty identifier")
        if lease_seconds < 1 or max_attempts < 1:
            raise ValueError("lease_seconds and max_attempts must be positive")
        if base_backoff_seconds < 1 or max_backoff_seconds < base_backoff_seconds:
            raise ValueError("invalid delivery backoff bounds")
        self.db_path = str(db_path)
        self.outbox_table = _outbox_table(outbox_table)
        self.transport = transport
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.transport_timeout_seconds = float(
            lease_seconds if transport_timeout_seconds is None else transport_timeout_seconds
        )
        if not math.isfinite(self.transport_timeout_seconds) or self.transport_timeout_seconds <= 0:
            raise ValueError("transport timeout must be positive and finite")
        if (
            isinstance(advisory_wait_seconds, bool)
            or not isinstance(advisory_wait_seconds, int)
            or advisory_wait_seconds < 0
        ):
            raise ValueError("advisory_wait_seconds must be a non-negative integer")
        self.advisory_wait_seconds = advisory_wait_seconds
        self.jitter = jitter
        if verify_schema:
            require_managed_schema(
                self.db_path,
                required_tables=frozenset({self.outbox_table}),
            )

    def recover_expired_leases(self, *, now: int) -> int:
        timestamp = self._timestamp(now)
        try:
            with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
                result = conn.execute(
                    f"""
                    UPDATE {self.outbox_table}
                    SET
                        status = 'DELIVERY_UNCERTAIN',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_error_code = 'LEASE_EXPIRED_AFTER_SEND_MAY_HAVE_STARTED',
                        updated_at = ?
                    WHERE
                        status = 'SENDING'
                        AND lease_expires_at IS NOT NULL
                        AND lease_expires_at <= ?
                    """,
                    (timestamp, timestamp),
                )
                return int(result.rowcount)
        except sqlite3.Error as exc:
            raise NotificationDeliveryError("DELIVERY_LEASE_RECOVERY_FAILED") from exc

    def _ensure_expired_entry_advisory_grace(self, *, now: int) -> None:
        if (
            self.outbox_table != "entry_notification_outbox"
            or self.advisory_wait_seconds <= 0
        ):
            return
        timestamp = self._timestamp(now)
        try:
            with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
                row = conn.execute(
                    "SELECT outbox.decision_event_id "
                    "FROM entry_notification_outbox outbox "
                    "WHERE outbox.status IN ('PENDING','RETRY_WAIT') "
                    "AND outbox.available_at <= ? "
                    "AND outbox.created_at + ? <= ? "
                    "AND NOT EXISTS ("
                    "SELECT 1 FROM entry_decision_advisories advisory "
                    "WHERE advisory.decision_event_id=outbox.decision_event_id"
                    ") "
                    "ORDER BY outbox.available_at,outbox.created_at,outbox.event_id "
                    "LIMIT 1",
                    (timestamp, self.advisory_wait_seconds, timestamp),
                ).fetchone()
            if row is None:
                return
            EntryDecisionStore(
                self.db_path, verify_schema=False
            ).ensure_unavailable_advisory(
                int(row[0]),
                advisory_at=timestamp,
                reason="AI advisory grace elapsed; canonical delivery continued without AI.",
            )
        except (sqlite3.Error, ValueError) as exc:
            logger.warning(
                "Advisory grace fallback failed without blocking canonical delivery: %s",
                type(exc).__name__,
            )

    def claim_next(self, *, now: int) -> ClaimedEvent | None:
        timestamp = self._timestamp(now)
        try:
            with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                advisory_clause = ""
                query_parameters: list[Any] = [timestamp, timestamp]
                if (
                    self.outbox_table == "entry_notification_outbox"
                    and self.advisory_wait_seconds > 0
                ):
                    advisory_clause = """
                        AND (
                            created_at + ? <= ?
                            OR EXISTS (
                                SELECT 1
                                FROM entry_decision_advisories advisory
                                WHERE advisory.decision_event_id =
                                      entry_notification_outbox.decision_event_id
                            )
                        )
                    """
                    query_parameters.extend(
                        [self.advisory_wait_seconds, timestamp]
                    )
                row = conn.execute(
                    f"""
                    SELECT
                        event_id, event_key, event_type,
                        payload_contract_version, payload_json, payload_hash,
                        attempt_count
                    FROM {self.outbox_table}
                    WHERE
                        status IN ('PENDING', 'RETRY_WAIT')
                        AND available_at <= ?
                        AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                        {advisory_clause}
                    ORDER BY available_at, created_at, event_id
                    LIMIT 1
                    """,
                    tuple(query_parameters),
                ).fetchone()
                if row is None:
                    conn.commit()
                    return None
                updated = conn.execute(
                    f"""
                    UPDATE {self.outbox_table}
                    SET
                        status = 'SENDING',
                        attempt_count = attempt_count + 1,
                        lease_owner = ?,
                        lease_expires_at = ?,
                        last_error_code = NULL,
                        updated_at = ?
                    WHERE
                        event_id = ?
                        AND status IN ('PENDING', 'RETRY_WAIT')
                        AND attempt_count = ?
                    """,
                    (
                        self.worker_id,
                        timestamp + self.lease_seconds,
                        timestamp,
                        str(row["event_id"]),
                        int(row["attempt_count"]),
                    ),
                )
                if updated.rowcount != 1:
                    conn.rollback()
                    return None
                conn.commit()
                return ClaimedEvent(
                    event_id=str(row["event_id"]),
                    event_key=str(row["event_key"]),
                    event_type=str(row["event_type"]),
                    payload_contract_version=str(row["payload_contract_version"]),
                    payload_json=str(row["payload_json"]),
                    payload_hash=str(row["payload_hash"]),
                    attempt_count=int(row["attempt_count"]) + 1,
                )
        except sqlite3.Error as exc:
            raise NotificationDeliveryError("DELIVERY_CLAIM_FAILED") from exc

    async def dispatch_once(self, *, now: int) -> DispatchOutcome | None:
        timestamp = self._timestamp(now)
        await asyncio.to_thread(self.recover_expired_leases, now=timestamp)
        await asyncio.to_thread(
            self._ensure_expired_entry_advisory_grace,
            now=timestamp,
        )
        event = await asyncio.to_thread(self.claim_next, now=timestamp)
        if event is None:
            return None
        try:
            result = await asyncio.wait_for(
                self.transport.deliver(
                    {
                        "event_id": event.event_id,
                        "idempotency_key": event.event_key,
                        "event_type": event.event_type,
                        "payload_contract_version": event.payload_contract_version,
                        "payload_json": event.payload_json,
                        "payload_hash": event.payload_hash,
                    }
                ),
                timeout=self.transport_timeout_seconds,
            )
        except (asyncio.TimeoutError, TimeoutError):
            result = DeliveryResult(
                DeliveryDisposition.DELIVERY_UNCERTAIN,
                error_code="TRANSPORT_TIMEOUT_AFTER_SEND_MAY_HAVE_STARTED",
            )
        except Exception:
            logger.exception(
                "Notification transport failed for event %s",
                event.event_id,
            )
            result = DeliveryResult(
                DeliveryDisposition.TRANSIENT_FAILURE,
                error_code="TRANSPORT_EXCEPTION",
            )
        return await asyncio.to_thread(
            self._complete,
            event,
            result=result,
            now=timestamp,
        )

    def _complete(
        self,
        event: ClaimedEvent,
        *,
        result: DeliveryResult,
        now: int,
    ) -> DispatchOutcome:
        state, next_available, error_code = self._next_state(event, result, now=now)
        try:
            with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
                updated = conn.execute(
                    f"""
                    UPDATE {self.outbox_table}
                    SET
                        status = ?,
                        available_at = ?,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        last_error_code = ?,
                        updated_at = ?
                    WHERE
                        event_id = ?
                        AND status = 'SENDING'
                        AND lease_owner = ?
                        AND attempt_count = ?
                    """,
                    (
                        state,
                        next_available if next_available is not None else now,
                        error_code,
                        now,
                        event.event_id,
                        self.worker_id,
                        event.attempt_count,
                    ),
                )
                if updated.rowcount != 1:
                    raise NotificationDeliveryError("DELIVERY_COMPLETION_CAS_FAILED")
        except sqlite3.Error as exc:
            raise NotificationDeliveryError("DELIVERY_COMPLETION_FAILED") from exc
        return DispatchOutcome(
            event_id=event.event_id,
            state=state,
            attempt_count=event.attempt_count,
            next_available_at=next_available,
            error_code=error_code,
        )

    def _next_state(
        self,
        event: ClaimedEvent,
        result: DeliveryResult,
        *,
        now: int,
    ) -> tuple[str, int | None, str | None]:
        error_code = self._safe_error_code(result.error_code)
        if result.disposition is DeliveryDisposition.DELIVERED:
            return "DELIVERED", None, None
        if result.disposition is DeliveryDisposition.DELIVERY_UNCERTAIN:
            return (
                "DELIVERY_UNCERTAIN",
                None,
                error_code or "DELIVERY_OUTCOME_UNCERTAIN",
            )
        if result.disposition is DeliveryDisposition.PERMANENT_FAILURE:
            return "DEAD_LETTER", None, error_code or "PERMANENT_FAILURE"
        if event.attempt_count >= self.max_attempts:
            return "DEAD_LETTER", None, error_code or "MAX_ATTEMPTS_EXCEEDED"
        if result.disposition is DeliveryDisposition.RATE_LIMITED:
            retry_after = result.retry_after_seconds
            if isinstance(retry_after, bool) or not isinstance(retry_after, int):
                retry_after = self.base_backoff_seconds
            delay = min(max(1, retry_after), self.max_backoff_seconds)
            return "RETRY_WAIT", now + delay, error_code or "HTTP_429"
        delay = self._backoff(event.attempt_count)
        return "RETRY_WAIT", now + delay, error_code or "TRANSIENT_FAILURE"

    def _backoff(self, attempt_count: int) -> int:
        base = min(
            self.max_backoff_seconds,
            self.base_backoff_seconds * (2 ** max(0, attempt_count - 1)),
        )
        jitter_value = self.jitter()
        if not isinstance(jitter_value, (int, float)) or not math.isfinite(jitter_value):
            jitter_value = 0.0
        bounded_jitter = min(max(float(jitter_value), 0.0), 1.0)
        return max(1, math.ceil(base * (1.0 + 0.2 * bounded_jitter)))

    @staticmethod
    def _safe_error_code(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = "".join(
            character if character.isalnum() or character in "_-" else "_"
            for character in str(value).upper()
        )[:128]
        return normalized or "UNKNOWN_ERROR"

    @staticmethod
    def _timestamp(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("delivery clock must be a non-negative integer timestamp")
        return value


def notification_delivery_health(
    db_path: str | Path,
    *,
    now: int,
    outbox_table: str = "domain_outbox_events",
) -> dict[str, Any]:
    timestamp = DurableNotificationWorker._timestamp(now)
    table = _outbox_table(outbox_table)
    path = Path(db_path)
    if not path.is_file():
        raise NotificationDeliveryError("DELIVERY_HEALTH_DATABASE_UNAVAILABLE")
    try:
        with closing(
            sqlite3.connect(
                f"{path.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=5.0,
            )
        ) as conn:
            with conn:
                conn.execute("PRAGMA query_only=ON")
                rows = conn.execute(
                    f"SELECT status, COUNT(*), MIN(created_at) FROM {table} "
                    "GROUP BY status ORDER BY status"
                ).fetchall()
    except sqlite3.Error as exc:
        raise NotificationDeliveryError("DELIVERY_HEALTH_QUERY_FAILED") from exc
    counts = {str(row[0]): int(row[1]) for row in rows}
    pending_created = [
        int(row[2])
        for row in rows
        if row[0] in {"PENDING", "RETRY_WAIT", "SENDING"} and row[2] is not None
    ]
    oldest_pending_age = (
        max(0, timestamp - min(pending_created)) if pending_created else None
    )
    alerts = []
    if counts.get("DEAD_LETTER", 0):
        alerts.append("NOTIFICATION_DEAD_LETTER_PRESENT")
    if counts.get("DELIVERY_UNCERTAIN", 0):
        alerts.append("NOTIFICATION_DELIVERY_UNCERTAIN_PRESENT")
    if oldest_pending_age is not None and oldest_pending_age > 300:
        alerts.append("NOTIFICATION_QUEUE_LAG_HIGH")
    return {
        "contract_version": "notification_delivery_health_v1",
        "generated_at": timestamp,
        "counts": counts,
        "oldest_pending_age_seconds": oldest_pending_age,
        "alerts": alerts,
        "healthy": not alerts,
    }
