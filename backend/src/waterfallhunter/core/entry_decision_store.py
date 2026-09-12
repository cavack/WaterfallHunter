"""Append-only persistence for canonical entry-decision transitions."""

from __future__ import annotations

import json
import inspect
import threading
from collections import deque
import logging
import math
import sqlite3
import time
import asyncio
from enum import Enum
from pathlib import Path
from typing import Any

from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.schema_contract import require_managed_schema
from waterfallhunter.core.signal_metadata import canonical_sha256


logger = logging.getLogger("WaterfallHunter.EntryDecisionStore")

_OUTCOME_CAPTURE_VERSION = "decision_outcome_capture_v1"
_OUTCOME_RESOLUTION_VERSION = "decision_outcome_resolution_v1"
_OUTCOME_UNAVAILABLE = "UNAVAILABLE"
_OUTCOME_UNOBSERVED = "UNOBSERVED"
_OBSERVATIONAL_ONLY = "observational_only"
_DECISION_MUTATED = "decision_mutated"
_DECISION_EVENT_ID_INVALID = "decision event id invalid"
_RESOLUTION_OBSERVATIONAL_ONLY = "resolution must be observational only"
_PROVIDER_READY_CAPTURE_PREDICATE = """
    json_extract(c.capture_json, '$.contract.available') = 1
    AND lower(json_extract(c.capture_json, '$.contract.exchange')) = 'lbank'
    AND json_extract(c.capture_json, '$.contract.market_type') = 'linear_usdt_perpetual'
    AND length(trim(json_extract(c.capture_json, '$.contract.mapped_symbol'))) > 0
    AND json_extract(c.capture_json, '$.outcome_contract.closed_candles_only') = 1
    AND json_extract(c.capture_json, '$.outcome_contract.complete_window_required') = 1
    AND json_extract(c.capture_json, '$.trade_plan.entry_price') IS NOT NULL
    AND json_extract(c.capture_json, '$.trade_plan.stop_loss') IS NOT NULL
    AND json_extract(c.capture_json, '$.trade_plan.take_profit_1') IS NOT NULL
    AND json_extract(c.capture_json, '$.trade_plan.take_profit_2') IS NOT NULL
"""

_ALLOWED = {
    "NO_TRADE",
    "FORMING",
    "ENTRY_READY",
    "ACTIVE",
    "LATE",
    "INVALIDATED",
    "EXPIRED",
}


class OutcomeFailureDisposition(str, Enum):
    """Safe handling class for failures while resolving research outcomes."""

    RETRYABLE = "RETRYABLE"
    TERMINAL_UNAVAILABLE = "TERMINAL_UNAVAILABLE"


class StaleCandidateLifecycleError(RuntimeError):
    """Raised when an in-flight candidate no longer owns the catalogue lifecycle."""


class EntryDecisionStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        verify_schema: bool = True,
        source_revision: str | None = None,
    ):
        self.db_path = str(db_path)
        revision = str(source_revision or "").strip()
        self.source_revision = (
            revision
            if len(revision) == 40
            and all(character in "0123456789abcdef" for character in revision)
            else None
        )
        if verify_schema:
            require_managed_schema(
                self.db_path,
                required_tables=frozenset({"entry_decision_events"}),
            )
        self._transition_cache_lock = threading.RLock()
        self._transition_cache_initialized = False
        self._transition_cache_max_id = 0
        self._transition_cache_latest_by_symbol: dict[str, str] = {}
        self._transition_cache_recent: deque[tuple[int, str]] = deque(maxlen=200)

    @staticmethod
    def _validate_packet(packet: dict[str, Any]) -> tuple[str, int, float, float, str]:
        if packet.get("contract_version") != "entry_decision_v1":
            raise ValueError("entry decision contract version unsupported")
        decision = str(packet.get("decision") or "")
        if decision not in _ALLOWED:
            raise ValueError("entry decision state unsupported")
        event_at = packet.get("evaluated_at")
        readiness = packet.get("entry_readiness")
        coverage = packet.get("evidence_coverage_pct")
        policy_version = str(packet.get("policy_version") or "")
        if isinstance(event_at, bool) or not isinstance(event_at, int) or event_at < 0:
            raise ValueError("entry decision evaluated_at invalid")
        for name, value in (("entry_readiness", readiness), ("evidence_coverage_pct", coverage)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} invalid")
            if not 0 <= float(value) <= 100:
                raise ValueError(f"{name} outside 0..100")
        if not policy_version:
            raise ValueError("entry decision policy_version missing")
        return decision, event_at, float(readiness), float(coverage), policy_version

    @staticmethod
    def _encode(packet: dict[str, Any]) -> tuple[str, str]:
        payload = json.dumps(
            packet,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return payload, canonical_sha256(packet)

    @staticmethod
    def _normalize_outcome_cost_component(
        name: str, item: dict[str, Any], *, captured_at: int
    ) -> dict[str, Any]:
        classification = str(item.get("classification") or _OUTCOME_UNAVAILABLE)
        allowed = {
            "OBSERVED_COST",
            "RECONSTRUCTED_COST",
            "MODELED_COST",
            _OUTCOME_UNAVAILABLE,
        }
        if classification not in allowed:
            raise ValueError(f"{name} cost classification invalid")
        if classification == _OUTCOME_UNAVAILABLE:
            return {
                "value": None,
                "source": None,
                "classification": _OUTCOME_UNAVAILABLE,
                "observed_at": None,
                "interval": None,
                "available": False,
                "reason": str(item.get("reason") or f"{name} unavailable"),
            }
        value = item.get("value")
        available_value = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0
        )
        if not available_value or not str(item.get("source") or "").strip():
            raise ValueError(f"{name} available cost provenance invalid")
        return {
            **item,
            "value": float(value),
            "classification": classification,
            "observed_at": item.get("observed_at", captured_at),
            "interval": item.get("interval") or "decision_time_estimate",
            "available": True,
            "reason": None,
        }

    @classmethod
    def _normalized_outcome_costs(
        cls, costs: Any, *, captured_at: int
    ) -> dict[str, dict[str, Any]]:
        """Return four explicit cost records; unavailable never means zero."""
        source = costs if isinstance(costs, dict) else {}
        return {
            name: cls._normalize_outcome_cost_component(
                name,
                source.get(name) if isinstance(source.get(name), dict) else {},
                captured_at=captured_at,
            )
            for name in ("fees", "entry_slippage", "exit_slippage", "funding")
        }

    @staticmethod
    def _material_projection(packet: dict[str, Any]) -> dict[str, Any]:
        advisory = packet.get("leverage_advisory")
        leverage_advisory = advisory if isinstance(advisory, dict) else None
        execution_input = (
            leverage_advisory.get("execution_suitability_input")
            if leverage_advisory is not None
            and isinstance(leverage_advisory.get("execution_suitability_input"), dict)
            else {}
        )
        material_execution_input = {
            "available": execution_input.get("available"),
            "status": execution_input.get("status"),
            "maximum_leverage": execution_input.get("maximum_leverage"),
        }
        plan = packet.get("trade_plan")
        plan_leverage = plan.get("leverage") if isinstance(plan, dict) else None
        return {
            "decision": packet.get("decision"),
            "lifecycle_id": packet.get("lifecycle_id"),
            "lifecycle_state": packet.get("lifecycle_state"),
            "late_origin": packet.get("late_origin"),
            "block_reasons": sorted(
                str(reason) for reason in (packet.get("block_reasons") or [])
            ),
            "policy_version": packet.get("policy_version"),
            "leverage_advisory": None if leverage_advisory is None else {
                "status": leverage_advisory.get("status"),
                "leverage": leverage_advisory.get("leverage"),
                "policy_version": leverage_advisory.get("policy_version"),
                "reason": leverage_advisory.get("reason"),
                "execution_suitability_input": material_execution_input,
                "causal_input": (
                    leverage_advisory.get("causal_input")
                    if isinstance(leverage_advisory.get("causal_input"), dict)
                    else None
                ),
            },
            "trade_plan_leverage": plan_leverage,
        }

    def append_if_changed(
        self,
        symbol: str,
        packet: dict[str, Any],
        *,
        expected_lifecycle_id: int | None = None,
    ) -> int | None:
        symbol = str(symbol or "").strip()
        if not symbol:
            raise ValueError("symbol missing")
        if expected_lifecycle_id is not None and (
            isinstance(expected_lifecycle_id, bool)
            or not isinstance(expected_lifecycle_id, int)
            or expected_lifecycle_id < 1
        ):
            raise ValueError("expected_lifecycle_id must be a positive integer")
        decision, event_at, readiness, coverage, policy_version = self._validate_packet(packet)
        payload, payload_hash = self._encode(packet)
        lifecycle_state = str(packet.get("lifecycle_state") or "WATCH")
        created_at = int(time.time())
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            conn.execute("BEGIN IMMEDIATE")
            if expected_lifecycle_id is not None:
                catalog = conn.execute(
                    "SELECT lifecycle_id,scan_eligible,status FROM lbank_catalog WHERE symbol=?",
                    (symbol,),
                ).fetchone()
                if (
                    catalog is None
                    or int(catalog[0] or 0) != expected_lifecycle_id
                    or not bool(catalog[1])
                    or str(catalog[2] or "") == "REMOVED"
                ):
                    raise StaleCandidateLifecycleError(
                        "candidate lifecycle is no longer current"
                    )
            row = conn.execute(
                "SELECT decision,packet_json,packet_hash FROM entry_decision_events "
                "WHERE symbol=? ORDER BY id DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            previous_packet: dict[str, Any] | None = None
            if row is not None:
                previous_packet = self._verified_record(
                    row[1], row[2], label="previous entry decision packet"
                )
                if (
                    str(row[0]) == decision
                    and canonical_sha256(self._material_projection(previous_packet))
                    == canonical_sha256(self._material_projection(packet))
                ):
                    return None
            cursor = conn.execute(
                "INSERT INTO entry_decision_events ("
                "symbol,event_at,decision,lifecycle_state,entry_readiness,"
                "evidence_coverage_pct,policy_version,packet_json,packet_hash,created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    symbol,
                    event_at,
                    decision,
                    lifecycle_state,
                    readiness,
                    coverage,
                    policy_version,
                    payload,
                    payload_hash,
                    created_at,
                ),
            )
            decision_event_id = int(cursor.lastrowid)
            previous_decision = str(previous_packet.get("decision") or "") if previous_packet else ""
            previous_lifecycle_id = previous_packet.get("lifecycle_id") if previous_packet else None
            current_lifecycle_id = packet.get("lifecycle_id")
            entry_ready_transition = bool(
                decision == "ENTRY_READY"
                and (
                    previous_decision != "ENTRY_READY"
                    or previous_lifecycle_id != current_lifecycle_id
                )
            )
            if entry_ready_transition:
                event_id = f"entry:{decision_event_id}:ready"
                event_payload = {
                    "contract_version": "entry_ready_notification_v1",
                    "event_id": event_id,
                    "event_type": "ENTRY_READY",
                    "decision_event_id": decision_event_id,
                    "symbol": symbol,
                    "decision_packet": packet,
                }
                notification_json, notification_hash = self._encode(event_payload)
                conn.execute(
                    "INSERT INTO entry_notification_outbox ("
                    "event_id,decision_event_id,event_key,event_type,"
                    "payload_contract_version,payload_json,payload_hash,status,"
                    "attempt_count,available_at,lease_owner,lease_expires_at,"
                    "last_error_code,created_at,updated_at"
                    ") VALUES (?,?,?,'ENTRY_READY','entry_ready_notification_v1',"
                    "?,?,'PENDING',0,?,NULL,NULL,NULL,?,?)",
                    (
                        event_id,
                        decision_event_id,
                        event_id,
                        notification_json,
                        notification_hash,
                        event_at,
                        created_at,
                        created_at,
                    ),
                )
            return decision_event_id

    @staticmethod
    def _verified_record(raw_json: Any, expected_hash: Any, *, label: str) -> dict[str, Any]:
        try:
            decoded = json.loads(str(raw_json))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"{label} JSON invalid") from exc
        if not isinstance(decoded, dict):
            raise ValueError(f"{label} must be a JSON object")
        if canonical_sha256(decoded) != str(expected_hash or ""):
            raise ValueError(f"{label} hash mismatch")
        return decoded

    @classmethod
    def _row_packet(cls, row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
        if len(row) < 8:
            raise ValueError("entry decision row missing packet hash")
        packet = cls._verified_record(row[6], row[7], label="packet")
        packet["event_id"] = int(row[0])
        packet["symbol"] = str(row[1])
        packet["event_at"] = int(row[2])
        packet["decision"] = str(row[3])
        packet["entry_readiness"] = float(row[4])
        packet["evidence_coverage_pct"] = float(row[5])
        if len(row) > 8 and row[8] is not None:
            if len(row) < 10:
                raise ValueError("entry decision advisory row missing advisory hash")
            advisory = cls._verified_record(row[8], row[9], label="advisory")
            packet["ai_advisory"] = advisory
        if len(row) > 10:
            packet["previous_decision"] = None if row[10] is None else str(row[10])
        return packet

    def append_advisory(
        self,
        decision_event_id: int,
        advisory: dict[str, Any],
        *,
        advisory_at: int,
    ) -> int:
        if (
            isinstance(decision_event_id, bool)
            or not isinstance(decision_event_id, int)
            or decision_event_id <= 0
        ):
            raise ValueError(_DECISION_EVENT_ID_INVALID)
        if isinstance(advisory_at, bool) or not isinstance(advisory_at, int) or advisory_at < 0:
            raise ValueError("advisory timestamp invalid")
        if advisory.get(_OBSERVATIONAL_ONLY) is not True or advisory.get(_DECISION_MUTATED) is not False:
            raise ValueError("advisory must be observational only")
        provider = str(advisory.get("ai_provider") or "none")
        model = str(advisory.get("ai_model") or "none")
        status = str(advisory.get("ai_status") or _OUTCOME_UNAVAILABLE)
        if status not in {"AVAILABLE", _OUTCOME_UNAVAILABLE}:
            raise ValueError("advisory status invalid")
        persisted = {**advisory, "advisory_at": advisory_at}
        payload, payload_hash = self._encode(persisted)
        created_at = int(time.time())
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            cursor = conn.execute(
                "INSERT INTO entry_decision_advisories ("
                "decision_event_id,advisory_at,provider,model,status,"
                "advisory_json,advisory_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (decision_event_id, advisory_at, provider, model, status, payload, payload_hash, created_at),
            )
            return int(cursor.lastrowid)

    def ensure_unavailable_advisory(
        self,
        decision_event_id: int,
        *,
        advisory_at: int,
        reason: str,
    ) -> int:
        if (
            isinstance(decision_event_id, bool)
            or not isinstance(decision_event_id, int)
            or decision_event_id <= 0
        ):
            raise ValueError(_DECISION_EVENT_ID_INVALID)
        if (
            isinstance(advisory_at, bool)
            or not isinstance(advisory_at, int)
            or advisory_at < 0
        ):
            raise ValueError("advisory timestamp invalid")
        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            raise ValueError("advisory fallback reason missing")
        advisory = {
            _OBSERVATIONAL_ONLY: True,
            _DECISION_MUTATED: False,
            "ai_advice": _OUTCOME_UNAVAILABLE,
            "ai_confidence": 0,
            "ai_reasoning": normalized_reason,
            "ai_provider": "none",
            "ai_model": "none",
            "ai_status": _OUTCOME_UNAVAILABLE,
            "advisory_at": advisory_at,
        }
        payload, payload_hash = self._encode(advisory)
        created_at = int(time.time())
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM entry_decision_advisories "
                "WHERE decision_event_id=? ORDER BY id DESC LIMIT 1",
                (decision_event_id,),
            ).fetchone()
            if existing is not None:
                return int(existing[0])
            decision = conn.execute(
                "SELECT id FROM entry_decision_events WHERE id=?",
                (decision_event_id,),
            ).fetchone()
            if decision is None:
                raise ValueError("decision event missing")
            cursor = conn.execute(
                "INSERT INTO entry_decision_advisories ("
                "decision_event_id,advisory_at,provider,model,status,"
                "advisory_json,advisory_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    decision_event_id,
                    advisory_at,
                    "none",
                    "none",
                    "UNAVAILABLE",
                    payload,
                    payload_hash,
                    created_at,
                ),
            )
            return int(cursor.lastrowid)

    @staticmethod
    def _validate_outcome_capture_request(
        decision_event_id: int, capture: Any, *, captured_at: int
    ) -> str:
        if (
            isinstance(decision_event_id, bool)
            or not isinstance(decision_event_id, int)
            or decision_event_id <= 0
        ):
            raise ValueError(_DECISION_EVENT_ID_INVALID)
        if isinstance(captured_at, bool) or not isinstance(captured_at, int) or captured_at < 0:
            raise ValueError("capture timestamp invalid")
        if not isinstance(capture, dict):
            raise ValueError("capture must be an object")
        policy_safe = (
            capture.get(_OBSERVATIONAL_ONLY) is True
            and capture.get(_DECISION_MUTATED) is False
            and not any(
                capture.get(field) is True
                for field in ("trade_eligible", "eligibility", "promotion_allowed")
            )
        )
        if not policy_safe:
            raise ValueError("capture must be observational only")
        outcome_status = str(capture.get("outcome_status") or _OUTCOME_UNOBSERVED)
        if outcome_status not in {_OUTCOME_UNOBSERVED, "OBSERVED"}:
            raise ValueError("outcome status invalid")
        return outcome_status

    def append_outcome_capture(
        self,
        decision_event_id: int,
        capture: dict[str, Any],
        *,
        captured_at: int,
    ) -> int:
        """Persist one observational outcome capture for a canonical decision."""
        outcome_status = self._validate_outcome_capture_request(
            decision_event_id, capture, captured_at=captured_at
        )
        created_at = int(time.time())
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            decision = conn.execute(
                "SELECT id,event_at,packet_hash FROM entry_decision_events WHERE id=?",
                (decision_event_id,),
            ).fetchone()
            if decision is None:
                raise ValueError("decision event missing")
            if captured_at < int(decision[1]):
                raise ValueError("capture timestamp precedes decision event")
            parent_hash = str(decision[2])
            supplied_parent_hash = capture.get("decision_packet_sha256")
            if supplied_parent_hash is not None and supplied_parent_hash != parent_hash:
                raise ValueError("capture parent decision packet hash mismatch")
            persisted = {
                **capture,
                "capture_version": _OUTCOME_CAPTURE_VERSION,
                "captured_at": captured_at,
                "decision_event_at": int(decision[1]),
                "decision_packet_sha256": parent_hash,
                "outcome_status": outcome_status,
            }
            persisted["costs"] = self._normalized_outcome_costs(
                capture.get("costs"), captured_at=captured_at
            )
            # Research evidence is never an eligibility or promotion authority.
            persisted["trade_eligible"] = False
            persisted["eligibility"] = None
            persisted["promotion_allowed"] = False
            payload, payload_hash = self._encode(persisted)
            try:
                cursor = conn.execute(
                    "INSERT INTO decision_outcome_capture ("
                    "decision_event_id,capture_version,captured_at,outcome_status,"
                    "capture_json,capture_hash,created_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        decision_event_id,
                        _OUTCOME_CAPTURE_VERSION,
                        captured_at,
                        outcome_status,
                        payload,
                        payload_hash,
                        created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("decision outcome capture already exists") from exc
            return int(cursor.lastrowid)

    def _repair_capture_target(
        self, symbol: str, packet: dict[str, Any]
    ) -> tuple[int, dict[str, Any]] | None:
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            row = conn.execute(
                "SELECT id,decision,packet_json,packet_hash "
                "FROM entry_decision_events WHERE symbol=? "
                "ORDER BY id DESC LIMIT 1",
                (str(symbol),),
            ).fetchone()
        if row is None:
            return None
        try:
            existing_packet = self._verified_record(
                row[2], row[3], label="unchanged entry decision packet"
            )
        except ValueError:
            logger.exception(
                "Unable to repair outcome capture for invalid canonical packet",
                extra={"symbol": str(symbol), "capture_failure_type": "persistence"},
            )
            return None
        same_transition = (
            str(row[1]) == str(packet.get("decision"))
            and canonical_sha256(self._material_projection(existing_packet))
            == canonical_sha256(self._material_projection(packet))
        )
        return (int(row[0]), existing_packet) if same_transition else None

    def _build_outcome_capture_payload(
        self,
        symbol: str,
        capture_packet: dict[str, Any],
        *,
        new_event: bool,
    ) -> dict[str, Any]:
        provenance = (
            capture_packet.get("research_provenance")
            if isinstance(capture_packet.get("research_provenance"), dict)
            else {}
        )
        embedded_revision = provenance.get("source_revision")
        revision = self.source_revision if new_event else embedded_revision
        decision_contract_sha256 = provenance.get("decision_contract_sha256")
        if new_event and embedded_revision != self.source_revision:
            decision_contract_sha256 = None
        revision = str(revision) if revision is not None else None
        revision_verified = bool(
            revision
            and len(revision) == 40
            and all(character in "0123456789abcdef" for character in revision)
        )
        return {
            _OBSERVATIONAL_ONLY: True,
            _DECISION_MUTATED: False,
            "decision": capture_packet.get("decision"),
            "symbol": str(symbol),
            "lifecycle_id": capture_packet.get("lifecycle_id"),
            "outcome_status": _OUTCOME_UNOBSERVED,
            "decision_packet_sha256": canonical_sha256(capture_packet),
            "source_revision": revision if revision_verified else None,
            "source_revision_status": (
                "VERIFIED_GIT_REVISION" if revision_verified else _OUTCOME_UNAVAILABLE
            ),
            "decision_contract_sha256": decision_contract_sha256,
            "contract": provenance.get("contract"),
            "trade_plan": capture_packet.get("trade_plan"),
            "costs": provenance.get("costs"),
            "outcome_contract": provenance.get("outcome_contract"),
        }

    def _persist_outcome_capture_best_effort(
        self,
        symbol: str,
        decision_event_id: int,
        capture: dict[str, Any],
        *,
        captured_at: int,
    ) -> None:
        try:
            self.append_outcome_capture(decision_event_id, capture, captured_at=captured_at)
        except ValueError as exc:
            if "already exists" not in str(exc):
                logger.exception(
                    "Research outcome capture persistence failed",
                    extra={
                        "symbol": str(symbol),
                        "decision_event_id": decision_event_id,
                        "capture_failure_type": "persistence",
                    },
                )
        except Exception:
            logger.exception(
                "Research outcome capture persistence failed",
                extra={
                    "symbol": str(symbol),
                    "decision_event_id": decision_event_id,
                    "capture_failure_type": "persistence",
                },
            )

    def append_if_changed_with_capture(
        self,
        symbol: str,
        packet: dict[str, Any],
        *,
        captured_at: int,
        expected_lifecycle_id: int | None = None,
    ) -> int | None:
        """Append a canonical decision and best-effort observational capture."""
        event_id = self.append_if_changed(
            symbol, packet, expected_lifecycle_id=expected_lifecycle_id
        )
        if event_id is None:
            repair_target = self._repair_capture_target(symbol, packet)
            if repair_target is None:
                return None
            capture_event_id, capture_packet = repair_target
        else:
            capture_event_id, capture_packet = event_id, packet
        capture = self._build_outcome_capture_payload(
            symbol, capture_packet, new_event=event_id is not None
        )
        self._persist_outcome_capture_best_effort(
            symbol, capture_event_id, capture, captured_at=captured_at
        )
        return event_id

    def scan_pending_outcome_captures(
        self,
        *,
        mature_before: int,
        limit: int = 100,
        after_id: int = 0,
        resolution_lane: str = "all",
    ) -> tuple[list[dict[str, Any]], int]:
        """Return verified captures plus the highest raw capture id scanned."""
        if isinstance(mature_before, bool) or not isinstance(mature_before, int):
            raise ValueError("maturity timestamp invalid")
        if resolution_lane not in {"all", "provider", "local"}:
            raise ValueError("outcome resolution lane invalid")
        bounded_after = max(0, int(after_id))
        lane_mode = {"all": -1, "provider": 1, "local": 0}[resolution_lane]
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT c.id, c.decision_event_id, c.captured_at,
                       c.capture_json, c.capture_hash,
                       e.packet_hash AS decision_packet_hash
                FROM decision_outcome_capture c
                INNER JOIN entry_decision_events e
                  ON e.id = c.decision_event_id
                LEFT JOIN decision_outcome_resolution r
                  ON r.decision_event_id = c.decision_event_id
                WHERE r.id IS NULL AND c.outcome_status = 'UNOBSERVED'
                  AND c.captured_at <= ?
                  AND c.id > ?
                  AND (
                    ? = -1
                    OR CASE WHEN ({_PROVIDER_READY_CAPTURE_PREDICATE})
                            THEN 1 ELSE 0 END = ?
                  )
                ORDER BY c.id LIMIT ?
                """,
                (
                    mature_before,
                    bounded_after,
                    lane_mode,
                    lane_mode,
                    max(1, min(int(limit), 1000)),
                ),
            ).fetchall()
        scanned_through = int(rows[-1]["id"]) if rows else bounded_after
        valid: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                decoded = self._verified_record(
                    item["capture_json"], item["capture_hash"],
                    label="outcome capture",
                )
                if decoded.get("decision_packet_sha256") != item["decision_packet_hash"]:
                    raise ValueError("parent decision packet hash mismatch")
            except ValueError as exc:
                logger.exception(
                    "Rejecting tampered outcome capture: %s",
                    exc,
                    extra={
                        "decision_event_id": item.get("decision_event_id"),
                        "capture_id": item.get("id"),
                        "failure_disposition": OutcomeFailureDisposition.RETRYABLE.value,
                        "error": str(exc),
                    },
                )
                continue
            valid.append({**decoded, **item})
        return valid, scanned_through

    def scan_pending_provider_outcome_captures(
        self, *, mature_before: int, limit: int = 100, after_id: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """Return captures that may call the exact-contract candle provider."""
        return self.scan_pending_outcome_captures(
            mature_before=mature_before,
            limit=limit,
            after_id=after_id,
            resolution_lane="provider",
        )

    def scan_pending_local_outcome_captures(
        self, *, mature_before: int, limit: int = 100, after_id: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        """Return captures whose unavailable outcome is known without provider I/O."""
        return self.scan_pending_outcome_captures(
            mature_before=mature_before,
            limit=limit,
            after_id=after_id,
            resolution_lane="local",
        )

    def pending_outcome_captures(
        self, *, mature_before: int, limit: int = 100, after_id: int = 0
    ) -> list[dict[str, Any]]:
        """Return verified immutable captures whose horizon has matured."""
        captures, _ = self.scan_pending_outcome_captures(
            mature_before=mature_before, limit=limit, after_id=after_id
        )
        return captures

    matured_outcome_captures = pending_outcome_captures

    def resolve_outcome_capture(
        self,
        decision_event_id: int,
        resolution: dict[str, Any],
        *,
        resolved_at: int,
    ) -> int:
        """Append the final observation without mutating the initial capture."""
        if isinstance(decision_event_id, bool) or not isinstance(decision_event_id, int) or decision_event_id <= 0:
            raise ValueError(_DECISION_EVENT_ID_INVALID)
        if isinstance(resolved_at, bool) or not isinstance(resolved_at, int) or resolved_at < 0:
            raise ValueError("resolution timestamp invalid")
        if not isinstance(resolution, dict):
            raise ValueError("resolution must be an object")
        self._validate_observational_resolution(resolution)
        status = str(resolution.get("outcome_status") or "")
        if status not in {"OBSERVED", "UNOBSERVABLE", _OUTCOME_UNAVAILABLE}:
            raise ValueError("outcome status invalid")
        # Explicitly preserve unavailable cost/provenance rather than inventing values.
        persisted = {
            **resolution,
            _OBSERVATIONAL_ONLY: True,
            _DECISION_MUTATED: False,
            "resolution_version": _OUTCOME_RESOLUTION_VERSION,
            "resolved_at": resolved_at,
            "outcome_status": status,
            "cost": resolution.get("cost"),
            "provenance": resolution.get("provenance"),
        }
        # Outcome resolution is evidence only and can never promote a decision.
        persisted["trade_eligible"] = False
        persisted["eligibility"] = None
        persisted["promotion_allowed"] = False
        persisted["promotion_decision"] = "DO_NOT_PROMOTE"
        payload, payload_hash = self._encode(persisted)
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            capture = conn.execute(
                "SELECT captured_at FROM decision_outcome_capture WHERE decision_event_id=?",
                (decision_event_id,),
            ).fetchone()
            if capture is None:
                raise ValueError("initial outcome capture missing")
            if resolved_at < int(capture[0]):
                raise ValueError("resolution timestamp precedes initial capture")
            try:
                cursor = conn.execute(
                    """INSERT INTO decision_outcome_resolution
                    (decision_event_id,resolution_version,resolved_at,outcome_status,
                     resolution_json,resolution_hash,created_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (decision_event_id, _OUTCOME_RESOLUTION_VERSION, resolved_at,
                     status, payload, payload_hash, int(time.time())),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("decision outcome resolution already exists") from exc
        return int(cursor.lastrowid)

    @staticmethod
    def _validate_observational_resolution(resolution: dict[str, Any]) -> None:
        invalid_flags = (
            (_OBSERVATIONAL_ONLY in resolution
             and resolution.get(_OBSERVATIONAL_ONLY) is not True)
            or (_DECISION_MUTATED in resolution
                and resolution.get(_DECISION_MUTATED) is not False)
            or any(
                field in resolution and resolution[field] is True
                for field in ("trade_eligible", "eligibility", "promotion_allowed")
            )
        )
        if invalid_flags:
            raise ValueError(_RESOLUTION_OBSERVATIONAL_ONLY)

    @staticmethod
    def classify_outcome_failure(exc: BaseException) -> OutcomeFailureDisposition:
        """Classify resolver failures conservatively.

        Only errors that clearly identify a permanently unavailable scientific
        source are terminal.  Unknown errors remain retryable so a transient
        provider regression cannot permanently discard evidence.
        """
        if getattr(exc, "terminal", False) is True:
            return OutcomeFailureDisposition.TERMINAL_UNAVAILABLE
        return OutcomeFailureDisposition.RETRYABLE

    def _resolve_one_matured_outcome(
        self, resolver: Any, capture: dict[str, Any], *, attempt: int, batch_limit: int
    ) -> bool:
        try:
            result = resolver(capture)
            if result is None:
                return False
        except Exception as exc:
            disposition = self.classify_outcome_failure(exc)
            decision_event_id = int(capture["decision_event_id"])
            logger.warning(
                "Matured outcome resolver failed",
                extra={
                    "decision_event_id": decision_event_id,
                    "failure_type": type(exc).__name__,
                    "error": str(exc),
                    "failure_disposition": disposition.value,
                    "retryable": disposition is OutcomeFailureDisposition.RETRYABLE,
                    "retry_after_seconds": (
                        min(300, 2 ** min(attempt - 1, 8))
                        if disposition is OutcomeFailureDisposition.RETRYABLE
                        else None
                    ),
                    "batch_attempt": attempt,
                    "batch_limit": batch_limit,
                },
                exc_info=disposition is OutcomeFailureDisposition.TERMINAL_UNAVAILABLE,
            )
            if disposition is OutcomeFailureDisposition.TERMINAL_UNAVAILABLE:
                self._persist_unavailable_resolution(decision_event_id, exc)
            return False
        # Persistence/validation/storage failures are never resolver-source
        # failures: keep the observation pending so a later cycle can retry it.
        try:
            self.resolve_outcome_capture(
                int(capture["decision_event_id"]), result, resolved_at=int(time.time())
            )
            return True
        except Exception as exc:
            logger.warning(
                "Matured outcome persistence failed; leaving capture pending",
                extra={
                    "decision_event_id": int(capture["decision_event_id"]),
                    "failure_type": type(exc).__name__,
                    "error": str(exc),
                    "failure_disposition": OutcomeFailureDisposition.RETRYABLE.value,
                    "retryable": True,
                    "batch_attempt": attempt,
                    "batch_limit": batch_limit,
                },
            )
            return False

    def _persist_unavailable_resolution(
        self, decision_event_id: int, exc: BaseException
    ) -> None:
        try:
            self.resolve_outcome_capture(
                decision_event_id,
                {
                    "outcome_status": _OUTCOME_UNAVAILABLE,
                    "cost": None,
                    "provenance": None,
                    "reason": f"resolver unavailable: {type(exc).__name__}",
                },
                resolved_at=int(time.time()),
            )
        except Exception:
            logger.exception(
                "Unable to persist terminal unavailable outcome resolution",
                extra={
                    "decision_event_id": decision_event_id,
                    "failure_disposition": OutcomeFailureDisposition.TERMINAL_UNAVAILABLE.value,
                },
            )

    def resolve_matured_outcomes(
        self, resolver: Any, *, mature_before: int, limit: int = 100
    ) -> int:
        """Resolve a bounded batch while leaving transient failures pending."""
        captures = self.pending_outcome_captures(mature_before=mature_before, limit=limit)
        return sum(
            self._resolve_one_matured_outcome(
                resolver, capture, attempt=attempt, batch_limit=len(captures)
            )
            for attempt, capture in enumerate(captures, start=1)
        )

    @staticmethod
    def _history_select(where: str = "") -> str:
        return (
            "SELECT e.id,e.symbol,e.event_at,e.decision,e.entry_readiness,"
            "e.evidence_coverage_pct,e.packet_json,e.packet_hash,"
            "(SELECT a.advisory_json FROM entry_decision_advisories a "
            " WHERE a.decision_event_id=e.id ORDER BY a.id DESC LIMIT 1),"
            "(SELECT a.advisory_hash FROM entry_decision_advisories a "
            " WHERE a.decision_event_id=e.id ORDER BY a.id DESC LIMIT 1),"
            "(SELECT p.decision FROM entry_decision_events p "
            " WHERE p.symbol=e.symbol AND p.id<e.id ORDER BY p.id DESC LIMIT 1) "
            "FROM entry_decision_events e " + where
        )

    def latest_for_symbol(self, symbol: str) -> dict[str, Any] | None:
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            row = conn.execute(
                self._history_select("WHERE e.symbol=? ORDER BY e.id DESC LIMIT 1"),
                (str(symbol),),
            ).fetchone()
        return None if row is None else self._row_packet(row)

    def history_for_symbol(self, symbol: str, *, limit: int = 20) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            rows = conn.execute(
                self._history_select("WHERE e.symbol=? ORDER BY e.id DESC LIMIT ?"),
                (str(symbol), bounded),
            ).fetchall()
        return [self._row_packet(row) for row in rows]

    def latest_map(self) -> dict[str, dict[str, Any]]:
        query = """
        SELECT e.id,e.symbol,e.event_at,e.decision,e.entry_readiness,
               e.evidence_coverage_pct,e.packet_json,e.packet_hash
        FROM entry_decision_events e
        INNER JOIN (
            SELECT symbol, MAX(id) AS max_id
            FROM entry_decision_events
            GROUP BY symbol
        ) latest ON latest.max_id = e.id
        """
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            rows = conn.execute(query).fetchall()
        return {str(row[1]): self._row_packet(row) for row in rows}

    def recent_changes(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent immutable canonical decision events."""
        bounded = max(1, min(int(limit), 200))
        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            rows = conn.execute(
                self._history_select("ORDER BY e.id DESC LIMIT ?"),
                (bounded,),
            ).fetchall()
        return [self._row_packet(row) for row in rows]

    def _reset_transition_cache(self) -> None:
        self._transition_cache_initialized = False
        self._transition_cache_max_id = 0
        self._transition_cache_latest_by_symbol.clear()
        self._transition_cache_recent.clear()

    def _refresh_transition_cache(self, conn: sqlite3.Connection) -> None:
        """Incrementally index transitions from the immutable decision ledger."""
        while True:
            row = conn.execute(
                "SELECT COALESCE(MAX(id),0) FROM entry_decision_events"
            ).fetchone()
            current_max = int(row[0] or 0) if row is not None else 0
            if (
                not self._transition_cache_initialized
                or current_max < self._transition_cache_max_id
            ):
                self._reset_transition_cache()

            start_id = self._transition_cache_max_id
            if current_max <= start_id:
                self._transition_cache_initialized = True
                return

            rows = conn.execute(
                "SELECT id,symbol,decision FROM entry_decision_events "
                "WHERE id>? AND id<=? ORDER BY id ASC",
                (start_id, current_max),
            )
            for event_id, symbol, decision in rows:
                event_id_int = int(event_id)
                symbol_text = str(symbol)
                decision_text = str(decision)
                previous = self._transition_cache_latest_by_symbol.get(symbol_text)
                if previous is not None and previous != decision_text:
                    self._transition_cache_recent.append((event_id_int, previous))
                self._transition_cache_latest_by_symbol[symbol_text] = decision_text
                self._transition_cache_max_id = event_id_int
            self._transition_cache_initialized = True

    def _transition_packets(
        self,
        conn: sqlite3.Connection,
        transitions: dict[int, str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        event_ids = sorted(transitions, reverse=True)[:limit]
        if not event_ids:
            return []
        placeholders = ",".join("?" for _ in event_ids)
        query = f"""
        SELECT e.id,e.symbol,e.event_at,e.decision,e.entry_readiness,
               e.evidence_coverage_pct,e.packet_json,e.packet_hash,
               (SELECT a.advisory_json FROM entry_decision_advisories a
                WHERE a.decision_event_id=e.id ORDER BY a.id DESC LIMIT 1),
               (SELECT a.advisory_hash FROM entry_decision_advisories a
                WHERE a.decision_event_id=e.id ORDER BY a.id DESC LIMIT 1)
        FROM entry_decision_events e
        WHERE e.id IN ({placeholders})
        ORDER BY e.id DESC
        """
        packets = []
        for row in conn.execute(query, event_ids).fetchall():
            packet = self._row_packet(row)
            packet["previous_decision"] = transitions[int(row[0])]
            packets.append(packet)
        return packets

    def recent_transitions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent transitions from a thread-safe incremental ledger index."""
        bounded = max(1, min(int(limit), 200))
        with self._transition_cache_lock:
            with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
                self._refresh_transition_cache(conn)
                transitions = dict(list(self._transition_cache_recent)[-bounded:])
                return self._transition_packets(conn, transitions, limit=bounded)


class EntryOutcomeResolutionWorker:
    """Small, bounded lifecycle wrapper for forward outcome resolution."""

    def __init__(
        self,
        store: EntryDecisionStore,
        resolver: Any,
        *,
        batch_size: int = 3,
        local_batch_size: int | None = None,
        interval_seconds: float = 900.0,
        close_delay_seconds: int = 120,
    ):
        self.store = store
        self.resolver = resolver
        self.batch_size = max(1, min(int(batch_size), 100))
        self.local_batch_size = max(
            1,
            min(
                int(local_batch_size if local_batch_size is not None else batch_size),
                1000,
            ),
        )
        self.interval_seconds = max(60.0, float(interval_seconds))
        self.close_delay_seconds = max(60, int(close_delay_seconds))
        self._running = False
        self._scan_after_id = 0
        self._provider_scan_after_id = 0
        self._local_scan_after_id = 0

    async def _load_pending_batch(self, *, mature_before: int) -> list[dict[str, Any]]:
        if not hasattr(self.store, "scan_pending_outcome_captures"):
            return await asyncio.to_thread(
                self.store.pending_outcome_captures,
                mature_before=mature_before,
                limit=self.batch_size,
                after_id=self._scan_after_id,
            )
        captures, scanned_through = await asyncio.to_thread(
            self.store.scan_pending_outcome_captures,
            mature_before=mature_before,
            limit=self.batch_size,
            after_id=self._scan_after_id,
        )
        if scanned_through > self._scan_after_id:
            self._scan_after_id = scanned_through
            return captures
        if not self._scan_after_id:
            return captures
        self._scan_after_id = 0
        captures, scanned_through = await asyncio.to_thread(
            self.store.scan_pending_outcome_captures,
            mature_before=mature_before,
            limit=self.batch_size,
            after_id=0,
        )
        self._scan_after_id = scanned_through
        return captures

    async def _load_lane_batch(
        self,
        *,
        scanner_name: str,
        cursor_name: str,
        mature_before: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        scanner = getattr(self.store, scanner_name)
        cursor = int(getattr(self, cursor_name))
        captures, scanned_through = await asyncio.to_thread(
            scanner,
            mature_before=mature_before,
            limit=limit,
            after_id=cursor,
        )
        if scanned_through > cursor:
            setattr(self, cursor_name, scanned_through)
            return captures
        if not cursor:
            return captures
        setattr(self, cursor_name, 0)
        captures, scanned_through = await asyncio.to_thread(
            scanner,
            mature_before=mature_before,
            limit=limit,
            after_id=0,
        )
        setattr(self, cursor_name, scanned_through)
        return captures

    async def _resolve_pending_capture(
        self,
        capture: dict[str, Any],
        *,
        attempt: int,
        batch_limit: int,
        resolved_at: int,
    ) -> bool:
        decision_event_id = int(capture["decision_event_id"])
        try:
            result = self.resolver(capture)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            disposition = self.store.classify_outcome_failure(exc)
            logger.warning(
                "Matured outcome resolver failed",
                extra={
                    "decision_event_id": decision_event_id,
                    "failure_type": type(exc).__name__,
                    "failure_disposition": disposition.value,
                    "retryable": disposition is OutcomeFailureDisposition.RETRYABLE,
                    "batch_attempt": attempt,
                    "batch_limit": batch_limit,
                },
            )
            if disposition is OutcomeFailureDisposition.TERMINAL_UNAVAILABLE:
                await asyncio.to_thread(
                    self.store._persist_unavailable_resolution, decision_event_id, exc
                )
            return False
        if result is None:
            return False
        try:
            await asyncio.to_thread(
                self.store.resolve_outcome_capture,
                decision_event_id,
                result,
                resolved_at=resolved_at,
            )
            return True
        except Exception as exc:
            logger.warning(
                "Matured outcome persistence failed; leaving capture pending",
                extra={
                    "decision_event_id": decision_event_id,
                    "failure_type": type(exc).__name__,
                    "failure_disposition": OutcomeFailureDisposition.RETRYABLE.value,
                    "retryable": True,
                },
            )
            return False

    async def run_once(self, *, now: int | None = None) -> int:
        """Resolve one sequential bounded batch without blocking the event loop."""
        current = int(time.time() if now is None else now)
        mature_before = current - 86_400 - self.close_delay_seconds
        if not hasattr(self.store, "pending_outcome_captures"):
            return await asyncio.to_thread(
                self.store.resolve_matured_outcomes,
                self.resolver,
                mature_before=mature_before,
                limit=self.batch_size,
            )
        lane_scanners_available = all(
            hasattr(self.store, name)
            for name in (
                "scan_pending_provider_outcome_captures",
                "scan_pending_local_outcome_captures",
            )
        )
        if lane_scanners_available:
            provider_captures = await self._load_lane_batch(
                scanner_name="scan_pending_provider_outcome_captures",
                cursor_name="_provider_scan_after_id",
                mature_before=mature_before,
                limit=self.batch_size,
            )
            local_captures = await self._load_lane_batch(
                scanner_name="scan_pending_local_outcome_captures",
                cursor_name="_local_scan_after_id",
                mature_before=mature_before,
                limit=self.local_batch_size,
            )
            batches = (provider_captures, local_captures)
        else:
            batches = (await self._load_pending_batch(mature_before=mature_before),)
        resolved = 0
        for captures in batches:
            for attempt, capture in enumerate(captures, start=1):
                if not lane_scanners_available:
                    self._scan_after_id = int(capture["id"])
                resolved += int(
                    await self._resolve_pending_capture(
                        capture,
                        attempt=attempt,
                        batch_limit=len(captures),
                        resolved_at=current,
                    )
                )
        return resolved

    async def run_forever(self) -> None:
        self._running = True
        try:
            while self._running:
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Entry outcome resolution cycle failed")
                await asyncio.sleep(self.interval_seconds)
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False
