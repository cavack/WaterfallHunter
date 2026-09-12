import sqlite3
import time
from typing import Any

from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.schema_contract import require_managed_schema


class StageLifecycleStore:
    """Persist an ordered strategy-stage lifecycle for the strict gate."""

    VERSION = "stage_lifecycle_v1"
    HYPE_TTL_SECONDS = 72 * 60 * 60
    DAMAGE_TTL_SECONDS = 24 * 60 * 60
    SETUP_TTL_SECONDS = 8 * 60 * 60

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
                    {"lbank_stage_lifecycle", "lbank_catalog"}
                ),
            )

    @classmethod
    def contract(cls) -> dict[str, Any]:
        return {
            "version": cls.VERSION,
            "freshness_seconds": {
                "hype": cls.HYPE_TTL_SECONDS,
                "damage": cls.DAMAGE_TTL_SECONDS,
                "setup": cls.SETUP_TTL_SECONDS,
            },
            "observational_only": False,
            "hard_gating_allowed": True,
        }

    @classmethod
    def _unavailable(
        cls,
        *,
        lifecycle_id: int,
        snapshot: dict[str, Any],
        reason: str,
        stale: bool,
    ) -> dict[str, Any]:
        return {
            **cls.contract(),
            "available": False,
            "lifecycle_id": lifecycle_id,
            "snapshot": snapshot,
            "confirmed": {
                "hype": False,
                "damage": False,
                "setup": False,
                "trigger": False,
                "passed": False,
            },
            "timestamps": {},
            "ages_seconds": {},
            "setup_type": None,
            "stale": stale,
            "reason": reason,
        }

    @staticmethod
    def _snapshot(stages: Any) -> dict[str, Any]:
        packet = stages if isinstance(stages, dict) else {}
        return {
            "hype": packet.get("hype") is True,
            "damage": packet.get("damage") is True,
            "setup": packet.get("setup") is True,
            "setup_type": packet.get("setup_type"),
            "trigger": packet.get("trigger") is True,
            "passed": packet.get("passed") is True,
        }

    def advance(
        self,
        symbol: str,
        expected_lifecycle_id: int,
        snapshot_stages: dict[str, Any],
        *,
        observed_at: int | float | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = str(symbol or "").strip().upper()
        lifecycle_id = int(expected_lifecycle_id)
        now = int(time.time() if observed_at is None else observed_at)
        snapshot = self._snapshot(snapshot_stages)

        with connect_managed_sqlite(self.db_path, timeout=10.0) as conn:
            conn.execute("BEGIN IMMEDIATE")
            catalog = conn.execute(
                """
                SELECT lifecycle_id, scan_eligible, status
                FROM lbank_catalog
                WHERE symbol = ?
                """,
                (normalized_symbol,),
            ).fetchone()

            if catalog is None:
                return self._unavailable(
                    lifecycle_id=lifecycle_id,
                    snapshot=snapshot,
                    reason="catalogue_missing",
                    stale=True,
                )
            if int(catalog[0] or 0) != lifecycle_id:
                return self._unavailable(
                    lifecycle_id=lifecycle_id,
                    snapshot=snapshot,
                    reason="lifecycle_mismatch",
                    stale=True,
                )
            if not bool(catalog[1]) or str(catalog[2]) == "REMOVED":
                return self._unavailable(
                    lifecycle_id=lifecycle_id,
                    snapshot=snapshot,
                    reason="scan_ineligible",
                    stale=True,
                )

            row = conn.execute(
                """
                SELECT
                    hype_seen_at,
                    damage_seen_at,
                    setup_seen_at,
                    setup_type,
                    trigger_seen_at,
                    updated_at
                FROM lbank_stage_lifecycle
                WHERE symbol = ? AND lifecycle_id = ?
                """,
                (normalized_symbol, lifecycle_id),
            ).fetchone()

            if row is None:
                hype_at = damage_at = setup_at = trigger_at = None
                setup_type = None
                updated_at = None
            else:
                hype_at, damage_at, setup_at, setup_type, trigger_at, updated_at = row

            if updated_at is not None and now < int(updated_at):
                return self._unavailable(
                    lifecycle_id=lifecycle_id,
                    snapshot=snapshot,
                    reason="out_of_order_evaluation",
                    stale=True,
                )

            if hype_at is not None and now - int(hype_at) > self.HYPE_TTL_SECONDS:
                hype_at = damage_at = setup_at = trigger_at = None
                setup_type = None
            elif damage_at is not None and now - int(damage_at) > self.DAMAGE_TTL_SECONDS:
                damage_at = setup_at = trigger_at = None
                setup_type = None
            elif setup_at is not None and now - int(setup_at) > self.SETUP_TTL_SECONDS:
                setup_at = trigger_at = None
                setup_type = None

            if snapshot["hype"]:
                hype_at = now
            if snapshot["damage"] and hype_at is not None:
                damage_at = now
            if snapshot["setup"] and damage_at is not None:
                setup_at = now
                setup_type = snapshot.get("setup_type")

            current_trigger = bool(
                snapshot["trigger"]
                and hype_at is not None
                and damage_at is not None
                and setup_at is not None
            )
            if current_trigger:
                trigger_at = now

            conn.execute(
                """
                INSERT INTO lbank_stage_lifecycle (
                    symbol,
                    lifecycle_id,
                    hype_seen_at,
                    damage_seen_at,
                    setup_seen_at,
                    setup_type,
                    trigger_seen_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, lifecycle_id) DO UPDATE SET
                    hype_seen_at = excluded.hype_seen_at,
                    damage_seen_at = excluded.damage_seen_at,
                    setup_seen_at = excluded.setup_seen_at,
                    setup_type = excluded.setup_type,
                    trigger_seen_at = excluded.trigger_seen_at,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_symbol,
                    lifecycle_id,
                    hype_at,
                    damage_at,
                    setup_at,
                    setup_type,
                    trigger_at,
                    now,
                ),
            )

        timestamps = {
            "hype_seen_at": hype_at,
            "damage_seen_at": damage_at,
            "setup_seen_at": setup_at,
            "trigger_seen_at": trigger_at,
            "updated_at": now,
        }
        ages = {
            key.removesuffix("_seen_at"): (now - int(value) if value is not None else None)
            for key, value in timestamps.items()
            if key.endswith("_seen_at")
        }
        return {
            **self.contract(),
            "available": True,
            "lifecycle_id": lifecycle_id,
            "snapshot": snapshot,
            "confirmed": {
                "hype": hype_at is not None,
                "damage": damage_at is not None,
                "setup": setup_at is not None,
                "trigger": current_trigger,
                "passed": current_trigger,
            },
            "timestamps": timestamps,
            "ages_seconds": ages,
            "setup_type": setup_type,
            "stale": False,
            "reason": None,
        }
