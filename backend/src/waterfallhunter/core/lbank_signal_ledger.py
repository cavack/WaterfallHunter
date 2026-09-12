import json
import logging
import math
import sqlite3
import time
from typing import Any

from waterfallhunter.core.canonical_json import canonical_json_bytes
from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.schema_contract import require_managed_schema
from waterfallhunter.core.signal_metadata import SignalMetadataInput, canonical_sha256


logger = logging.getLogger(
    "WaterfallHunter.LBankSignalLedger"
)


class LBankSignalLedger:
    """Append-only production signal snapshots.

    A signal row and its catalogue transition are committed in one SQLite
    transaction. Execution suitability is captured for later observation only;
    it does not determine signal eligibility.
    """

    def __init__(
        self,
        db_path: str = "/app/data/waterfall_registry.db",
        *,
        verify_schema: bool = True,
    ):
        self.db_path = db_path
        if verify_schema:
            require_managed_schema(
                self.db_path,
                required_tables=frozenset(
                    {"lbank_signal_ledger", "lbank_catalog"}
                ),
            )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(
            value,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _finite(
        value: Any,
    ) -> float | None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    @staticmethod
    def _metadata_created_at(value: int | None) -> int:
        timestamp = int(time.time()) if value is None else value
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or timestamp < 0
        ):
            raise ValueError("invalid signal identity, score, or metadata time")
        return timestamp

    @staticmethod
    def _decision_payload(
        *,
        signal_id: int,
        symbol: str,
        score: float,
        metadata: SignalMetadataInput,
        execution: dict[str, Any],
        created_at: int,
    ) -> dict[str, Any]:
        failed_checks = [str(item) for item in (execution.get("failed_checks") or [])]
        return {
            "contract_version": "signal_decision_persistence_v1",
            "decision_id": f"signal:{signal_id}:decision:1",
            "decision_version": 1,
            "signal_id": signal_id,
            "symbol": symbol,
            "signal_class": metadata.signal_class.value,
            "strategy_profile": metadata.strategy_profile,
            "lifecycle_state": "TRIGGERED",
            "decision_status": "CONFIRMED",
            "predictive_evidence_score": score,
            "calibrated_probability": None,
            "analysis_observed_at": metadata.analysis_observed_at,
            "reference_observed_at": metadata.reference_observed_at,
            "decision_contract_hash": metadata.decision_contract_hash,
            "eligibility_gates": {
                "catalog_compare_and_set": "PASSED",
                "canonical_metadata": "PASSED",
                "signal_only": True,
            },
            "anti_chase_risk": "NOT_EVALUATED",
            "execution_risk": str(execution.get("status") or "UNKNOWN"),
            "reason_codes": failed_checks,
            "execution_mode": "SIGNAL_ONLY",
            "created_at": created_at,
        }

    @staticmethod
    def _canonical_payload(value: dict[str, Any]) -> tuple[str, str]:
        payload_json = canonical_json_bytes(value).decode("utf-8")
        return payload_json, canonical_sha256(value)

    @classmethod
    def _persist_decision_and_outbox(
        cls,
        conn: sqlite3.Connection,
        *,
        signal_id: int,
        symbol: str,
        score: float,
        metadata: SignalMetadataInput,
        execution: dict[str, Any],
        created_at: int,
    ) -> None:
        decision = cls._decision_payload(
            signal_id=signal_id,
            symbol=symbol,
            score=score,
            metadata=metadata,
            execution=execution,
            created_at=created_at,
        )
        decision_json, decision_hash = cls._canonical_payload(decision)
        decision_id = str(decision["decision_id"])
        conn.execute(
            """
            INSERT INTO signal_decisions (
                signal_id, decision_id, decision_version, decision_status,
                lifecycle_state, predictive_evidence_score,
                calibrated_probability, analysis_observed_at,
                reference_observed_at, decision_contract_hash, payload_json,
                payload_hash, created_at
            ) VALUES (?, ?, 1, 'CONFIRMED', 'TRIGGERED', ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                decision_id,
                score,
                metadata.analysis_observed_at,
                metadata.reference_observed_at,
                metadata.decision_contract_hash,
                decision_json,
                decision_hash,
                created_at,
            ),
        )

        event_id = f"signal:{signal_id}:confirmed:1"
        event_payload = {
            "contract_version": "signal_confirmed_event_v1",
            "event_id": event_id,
            "event_type": "SIGNAL_CONFIRMED",
            "aggregate_type": "signal",
            "aggregate_id": str(signal_id),
            "aggregate_version": 1,
            "event_sequence": 1,
            "signal_id": signal_id,
            "symbol": symbol,
            "signal_class": metadata.signal_class.value,
            "strategy_profile": metadata.strategy_profile,
            "decision_id": decision_id,
            "decision_payload_hash": decision_hash,
            "created_at": created_at,
        }
        event_json, event_hash = cls._canonical_payload(event_payload)
        conn.execute(
            """
            INSERT INTO domain_outbox_events (
                event_id, signal_id, aggregate_type, aggregate_id,
                aggregate_version, event_sequence, event_type, event_key,
                payload_contract_version, payload_json, payload_hash, status,
                attempt_count, available_at, lease_owner, lease_expires_at,
                last_error_code, created_at, updated_at
            ) VALUES (?, ?, 'signal', ?, 1, 1, 'SIGNAL_CONFIRMED', ?,
                      'signal_confirmed_event_v1', ?, ?, 'PENDING', 0, ?,
                      NULL, NULL, NULL, ?, ?)
            """,
            (
                event_id,
                signal_id,
                str(signal_id),
                event_id,
                event_json,
                event_hash,
                created_at,
                created_at,
                created_at,
            ),
        )

    def persist_trigger(
        self,
        symbol: str,
        expected_state: str,
        *,
        score: float,
        trigger_metrics: dict,
        execution_suitability: dict,
        metadata: SignalMetadataInput,
        metadata_created_at: int | None = None,
        quote_volume: float | None = None,
        volume_gate_passed: bool | None = None,
        proxy_execution_disagreement: str | None = None,
        triggered_at: int | None = None,
    ) -> int | None:
        """Compare-and-set the live state and append its signal atomically."""
        try:
            symbol = str(symbol).strip().upper()
            expected_state = str(expected_state).strip().upper()
            score_value = self._finite(score)
            metadata_value = SignalMetadataInput.model_validate(metadata)
            metadata_time = self._metadata_created_at(metadata_created_at)
            metrics = (
                trigger_metrics
                if isinstance(trigger_metrics, dict)
                else {}
            )
            execution = (
                execution_suitability
                if isinstance(execution_suitability, dict)
                else {}
            )
            position = (
                metrics.get("position_setup")
                if isinstance(metrics.get("position_setup"), dict)
                else {}
            )
            event_time = int(
                time.time()
                if triggered_at is None
                else triggered_at
            )

            if (
                not symbol
                or not expected_state
                or score_value is None
            ):
                raise ValueError("invalid signal identity or score")

            metrics_json = self._json(metrics)
            position_json = self._json(position)
            execution_json = self._json(execution)
            failed_checks_json = self._json(
                list(execution.get("failed_checks") or [])
            )
            volume_gate_snapshot = (
                None
                if volume_gate_passed is None
                else int(bool(volume_gate_passed))
            )
            comparison_snapshot = (
                str(proxy_execution_disagreement)
                if proxy_execution_disagreement is not None
                else None
            )

            with connect_managed_sqlite(
                self.db_path,
                timeout=10.0,
            ) as conn:
                cursor = conn.execute(
                    """
                    UPDATE lbank_catalog
                    SET
                        status = 'TRIGGERED',
                        trigger_data = ?
                    WHERE
                        symbol = ?
                        AND scan_eligible = 1
                        AND status = ?
                    """,
                    (
                        metrics_json,
                        symbol,
                        expected_state,
                    ),
                )

                if cursor.rowcount != 1:
                    logger.warning(
                        "Signal ledger rejected stale or ineligible "
                        "transition for %s from %s",
                        symbol,
                        expected_state,
                    )
                    return None

                inserted = conn.execute(
                    """
                    INSERT INTO lbank_signal_ledger (
                        symbol,
                        triggered_at,
                        state_before,
                        score,
                        entry_price,
                        stop_loss,
                        take_profit_1,
                        take_profit_2,
                        position_setup_json,
                        trigger_metrics_json,
                        execution_status,
                        execution_evidence_status,
                        execution_observed_samples,
                        execution_observation_span_hours,
                        execution_availability_rate,
                        execution_cost_100_p90_pct,
                        execution_spread_p90_pct,
                        execution_depth_25bps_p50_usdt,
                        execution_failed_checks_json,
                        execution_suitability_json,
                        quote_volume_at_trigger,
                        volume_gate_passed,
                        proxy_execution_disagreement,
                        observational_only,
                        trade_eligible,
                        created_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?
                    )
                    """,
                    (
                        symbol,
                        event_time,
                        expected_state,
                        score_value,
                        self._finite(position.get("entry_price")),
                        self._finite(position.get("stop_loss")),
                        self._finite(position.get("take_profit_1")),
                        self._finite(position.get("take_profit_2")),
                        position_json,
                        metrics_json,
                        str(execution.get("status") or "UNKNOWN"),
                        execution.get("evidence_status"),
                        execution.get("observed_samples"),
                        self._finite(
                            execution.get("observation_span_hours")
                        ),
                        self._finite(execution.get("availability_rate")),
                        self._finite(execution.get("cost_100_p90_pct")),
                        self._finite(execution.get("spread_p90_pct")),
                        self._finite(
                            execution.get("depth_25bps_p50_usdt")
                        ),
                        failed_checks_json,
                        execution_json,
                        self._finite(quote_volume),
                        volume_gate_snapshot,
                        comparison_snapshot,
                        int(time.time()),
                    ),
                )
                raw_signal_id = inserted.lastrowid
                if raw_signal_id is None:
                    raise RuntimeError("signal ledger insert did not return a row id")
                signal_id = int(raw_signal_id)

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
                    (
                        signal_id,
                        metadata_value.signal_class.value,
                        metadata_value.strategy_profile,
                        metadata_value.score_version,
                        metadata_value.model_generation,
                        metadata_value.decision_contract_hash,
                        metadata_value.analysis_observed_at,
                        metadata_value.reference_observed_at,
                        metadata_value.metadata_contract_version,
                        metadata_value.classification_method.value,
                        metadata_value.classification_evidence_hash,
                        metadata_time,
                    ),
                )

                self._persist_decision_and_outbox(
                    conn,
                    signal_id=signal_id,
                    symbol=symbol,
                    score=score_value,
                    metadata=metadata_value,
                    execution=execution,
                    created_at=metadata_time,
                )

                return signal_id

        except Exception as exc:
            logger.error(
                "Atomic signal ledger persistence failed for %s: %s",
                symbol,
                exc,
            )
            return None
