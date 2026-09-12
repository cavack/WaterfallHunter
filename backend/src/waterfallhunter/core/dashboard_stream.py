"""Versioned dashboard snapshots and bounded SSE replay."""

from __future__ import annotations

import math
import threading
from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waterfallhunter.core.signal_metadata import canonical_sha256


DASHBOARD_SCHEMA_VERSION = "2.0"
DASHBOARD_SNAPSHOT_CONTRACT = "dashboard_snapshot_v2"
DASHBOARD_EVENT_CONTRACT = "dashboard_stream_event_v2"
_VOLATILE_CANDIDATE_AGE_KEYS = frozenset(
    {
        "age_seconds",
        "analysis_age_seconds",
        "reference_age_seconds",
    }
)


def _normalize_dashboard_json(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        return [_normalize_dashboard_json(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _normalize_dashboard_json(item)
            for key, item in value.items()
        }
    return value


def _stable_entry_decision_clocks(value: Any) -> Any:
    if isinstance(value, list):
        return [_stable_entry_decision_clocks(item) for item in value]
    if not isinstance(value, dict):
        return value
    stable: dict[str, Any] = {}
    for key, item in value.items():
        if key == "entry_decision" and isinstance(item, dict):
            stable[key] = {
                nested_key: _stable_entry_decision_clocks(nested_value)
                for nested_key, nested_value in item.items()
                if nested_key != "evaluated_at"
            }
        else:
            stable[key] = _stable_entry_decision_clocks(item)
    return stable


def _snapshot_content_material(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the business-state projection used for SSE change detection.

    Candidate ages are display-time derivatives of absolute observation timestamps.
    Including them in the change hash turns every one-second broadcaster tick into a
    multi-megabyte snapshot even when the underlying market state is unchanged.
    Keep the public payload intact and exclude only these known top-level derived
    candidate fields from the deduplication material.
    """

    candidates = payload.get("candidates")
    if not isinstance(candidates, dict):
        return payload

    stable_candidates: dict[str, Any] = {}
    changed = False
    for symbol, candidate in candidates.items():
        if not isinstance(candidate, dict):
            stable_candidates[symbol] = candidate
            continue
        stable_candidate = _stable_entry_decision_clocks({
            key: value
            for key, value in candidate.items()
            if key not in _VOLATILE_CANDIDATE_AGE_KEYS
        })
        stable_candidates[symbol] = stable_candidate
        changed = changed or stable_candidate != candidate

    if not changed:
        return payload
    return {
        **payload,
        "candidates": stable_candidates,
    }


class DecisionDiagnosticReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: str = Field(min_length=1)
    count: int = Field(ge=0)
    share_pct: float = Field(ge=0, le=100, allow_inf_nan=False)


class ZeroEntryReadyDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entry_ready_zero: bool
    evaluated_candidates: int = Field(ge=0)
    top_reasons: list[DecisionDiagnosticReason]
    pipeline_degraded: bool = False
    systemic_unavailable_reasons: list[DecisionDiagnosticReason] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_pipeline_health(self) -> "ZeroEntryReadyDiagnostics":
        if self.pipeline_degraded != bool(self.systemic_unavailable_reasons):
            raise ValueError("pipeline degraded flag disagrees with systemic unavailable reasons")
        for row in self.systemic_unavailable_reasons:
            if row.count != self.evaluated_candidates or row.share_pct < 100.0:
                raise ValueError("systemic unavailable reason must cover the evaluated universe")
        return self


class DecisionTerminalCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ENTRY_READY: int = Field(ge=0)
    FORMING: int = Field(ge=0)
    ACTIVE: int = Field(ge=0)
    LATE: int = Field(ge=0)
    INVALIDATED: int = Field(ge=0)
    EXPIRED: int = Field(ge=0)
    NO_TRADE: int = Field(ge=0)
    UNAVAILABLE: int = Field(ge=0)


class DecisionTerminal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["decision_terminal_v1"]
    counts: DecisionTerminalCounts
    entry_ready: list[str]
    forming: list[str]
    active: list[str]
    late: list[str]
    zero_entry_ready_diagnostics: ZeroEntryReadyDiagnostics
    recent_changes: list[dict[str, Any]]

    @model_validator(mode="after")
    def _validate_groups(self) -> "DecisionTerminal":
        expected_lengths = {
            "entry_ready": min(self.counts.ENTRY_READY, 3),
            "forming": min(self.counts.FORMING, 6),
            "active": min(self.counts.ACTIVE, 6),
            "late": min(self.counts.LATE, 6),
        }
        for field, expected in expected_lengths.items():
            values = getattr(self, field)
            if len(values) != expected or any(not value for value in values):
                raise ValueError(f"decision terminal {field} does not match counts")
        if self.zero_entry_ready_diagnostics.entry_ready_zero != (self.counts.ENTRY_READY == 0):
            raise ValueError("decision terminal zero-entry diagnostic disagrees with counts")
        if len(self.recent_changes) > 10:
            raise ValueError("decision terminal recent changes exceed bounded contract")
        return self


class DashboardSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["dashboard_snapshot_v2"]
    schema_version: Literal["2.0"]
    snapshot_version: int = Field(ge=1)
    generated_at: float = Field(ge=0, allow_inf_nan=False)
    state: Literal["READY"]
    total: int = Field(ge=0)
    candidates: dict[str, dict[str, Any]]
    decision_terminal: DecisionTerminal
    final_ranking: dict[str, Any]
    signal_funnel: dict[str, Any]

    @model_validator(mode="after")
    def _validate_total(self) -> "DashboardSnapshot":
        if self.total != len(self.candidates):
            raise ValueError("dashboard total must equal candidate count")
        decision_total = sum(self.decision_terminal.counts.model_dump().values())
        if decision_total != self.total:
            raise ValueError("decision terminal counts must equal candidate count")
        if self.decision_terminal.zero_entry_ready_diagnostics.evaluated_candidates != self.total:
            raise ValueError("decision terminal diagnostic count must equal candidate count")
        return self


class DashboardStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["dashboard_stream_event_v2"]
    event_id: str = Field(pattern=r"^[1-9][0-9]*$")
    event_type: Literal["snapshot", "heartbeat"]
    snapshot_version: int = Field(ge=0)
    schema_version: Literal["2.0"]
    generated_at: float = Field(ge=0, allow_inf_nan=False)
    last_event_id: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: DashboardSnapshot | None
    replayed: bool
    full_snapshot: bool

    @model_validator(mode="after")
    def _validate_event_shape(self) -> "DashboardStreamEvent":
        if self.event_type == "snapshot" and self.payload is None:
            raise ValueError("snapshot events require a payload")
        if self.event_type == "heartbeat" and self.payload is not None:
            raise ValueError("heartbeat events cannot contain a snapshot")
        if self.payload is not None and self.payload.snapshot_version != self.snapshot_version:
            raise ValueError("event and payload snapshot versions must match")
        return self


def serialize_sse_event(event: DashboardStreamEvent) -> str:
    data = event.model_dump_json(exclude_none=False)
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {data}\n\n"


class DashboardEventBuffer:
    """Thread-safe monotonic event sequencer with bounded in-memory replay."""

    def __init__(self, *, replay_limit: int = 100):
        if replay_limit < 1:
            raise ValueError("replay_limit must be positive")
        self._events: deque[DashboardStreamEvent] = deque(maxlen=replay_limit)
        self._event_sequence = 0
        self._snapshot_sequence = 0
        self._last_snapshot_content_hash: str | None = None
        self._lock = threading.Lock()

    def publish_snapshot(
        self,
        payload: dict[str, Any],
        *,
        generated_at: float,
        full_snapshot: bool,
    ) -> DashboardStreamEvent:
        normalized_payload = _normalize_dashboard_json(payload)
        content_hash = canonical_sha256(
            _snapshot_content_material(normalized_payload)
        )
        with self._lock:
            return self._publish_snapshot_locked(
                normalized_payload,
                generated_at=generated_at,
                full_snapshot=full_snapshot,
                content_hash=content_hash,
            )

    def publish_snapshot_if_changed(
        self,
        payload: dict[str, Any],
        *,
        generated_at: float,
    ) -> DashboardStreamEvent | None:
        """Retain a periodic snapshot only when its business payload changed."""
        normalized_payload = _normalize_dashboard_json(payload)
        content_hash = canonical_sha256(
            _snapshot_content_material(normalized_payload)
        )
        with self._lock:
            if content_hash == self._last_snapshot_content_hash:
                return None
            return self._publish_snapshot_locked(
                normalized_payload,
                generated_at=generated_at,
                full_snapshot=False,
                content_hash=content_hash,
            )

    def _publish_snapshot_locked(
        self,
        payload: dict[str, Any],
        *,
        generated_at: float,
        full_snapshot: bool,
        content_hash: str | None = None,
    ) -> DashboardStreamEvent:
        next_snapshot_version = self._snapshot_sequence + 1
        snapshot = DashboardSnapshot.model_validate(
            {
                "contract_version": DASHBOARD_SNAPSHOT_CONTRACT,
                "schema_version": DASHBOARD_SCHEMA_VERSION,
                "snapshot_version": next_snapshot_version,
                "generated_at": generated_at,
                "state": "READY",
                **payload,
            }
        )
        self._snapshot_sequence = next_snapshot_version
        self._last_snapshot_content_hash = content_hash or canonical_sha256(
            _snapshot_content_material(payload)
        )
        return self._append(
            event_type="snapshot",
            snapshot_version=snapshot.snapshot_version,
            generated_at=generated_at,
            payload=snapshot,
            payload_hash=canonical_sha256(snapshot.model_dump(mode="json")),
            full_snapshot=full_snapshot,
        )

    def publish_heartbeat(self, *, generated_at: float) -> DashboardStreamEvent:
        with self._lock:
            heartbeat_material = {
                "contract_version": DASHBOARD_EVENT_CONTRACT,
                "event_type": "heartbeat",
                "snapshot_version": self._snapshot_sequence,
                "schema_version": DASHBOARD_SCHEMA_VERSION,
                "generated_at": generated_at,
            }
            return self._append(
                event_type="heartbeat",
                snapshot_version=self._snapshot_sequence,
                generated_at=generated_at,
                payload=None,
                payload_hash=canonical_sha256(heartbeat_material),
                full_snapshot=False,
            )

    def _append(
        self,
        *,
        event_type: Literal["snapshot", "heartbeat"],
        snapshot_version: int,
        generated_at: float,
        payload: DashboardSnapshot | None,
        payload_hash: str,
        full_snapshot: bool,
    ) -> DashboardStreamEvent:
        # Snapshot events carry the complete dashboard state. Retaining older
        # full payloads multiplies memory without adding replay correctness.
        # Keep replay contiguous from the newest full-state boundary forward;
        # older reconnects can replay from that complete state or fail closed to
        # a fresh snapshot after the boundary itself expires.
        if payload is not None:
            self._events.clear()

        previous = str(self._event_sequence) if self._event_sequence else None
        self._event_sequence += 1
        event = DashboardStreamEvent(
            contract_version=DASHBOARD_EVENT_CONTRACT,
            event_id=str(self._event_sequence),
            event_type=event_type,
            snapshot_version=snapshot_version,
            schema_version=DASHBOARD_SCHEMA_VERSION,
            generated_at=generated_at,
            last_event_id=previous,
            payload_hash=payload_hash,
            payload=payload,
            replayed=False,
            full_snapshot=full_snapshot,
        )
        self._events.append(event)
        return event

    def replay_after(self, last_event_id: str | None) -> list[DashboardStreamEvent] | None:
        """Return replay events, or None when a full snapshot is required."""

        if last_event_id is None or not last_event_id.isdigit() or int(last_event_id) < 1:
            return None
        requested = int(last_event_id)
        with self._lock:
            if requested == self._event_sequence:
                return []

            events = list(self._events)
            event_ids = [int(event.event_id) for event in events]
            if requested in event_ids:
                replay_events = [
                    event for event in events if int(event.event_id) > requested
                ]
            else:
                # Older full snapshots are intentionally superseded to bound
                # memory. If the requested event predates the retained full
                # state boundary, replay from that complete snapshot instead
                # of forcing another multi-megabyte snapshot allocation.
                replay_start = next(
                    (
                        index
                        for index, event in enumerate(events)
                        if event.payload is not None
                        and int(event.event_id) > requested
                    ),
                    None,
                )
                if replay_start is None:
                    return None
                replay_events = events[replay_start:]

            return [
                event.model_copy(update={"replayed": True, "full_snapshot": False})
                for event in replay_events
            ]

    def latest_snapshot(self) -> DashboardSnapshot | None:
        """Return the latest retained snapshot without advancing either sequence."""
        with self._lock:
            for event in reversed(self._events):
                if event.payload is not None:
                    return event.payload
        return None

    def preview_snapshot(
        self,
        payload: dict[str, Any],
        *,
        generated_at: float,
    ) -> DashboardSnapshot:
        """Build a read-only snapshot for initial polling without retaining it."""
        normalized_payload = _normalize_dashboard_json(payload)
        with self._lock:
            snapshot_version = max(1, self._snapshot_sequence)
        return DashboardSnapshot.model_validate(
            {
                "contract_version": DASHBOARD_SNAPSHOT_CONTRACT,
                "schema_version": DASHBOARD_SCHEMA_VERSION,
                "snapshot_version": snapshot_version,
                "generated_at": generated_at,
                "state": "READY",
                **normalized_payload,
            }
        )

    @property
    def snapshot_version(self) -> int:
        with self._lock:
            return self._snapshot_sequence
