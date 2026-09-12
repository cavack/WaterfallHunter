import json
import logging
import sqlite3
import time
from typing import Any, Dict, Iterable

from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.schema_contract import require_managed_schema

logger = logging.getLogger("WaterfallHunter.Database")


ACTIVE_STATES = (
    "WATCH",
    "FUEL-RICH",
    "PRE-TRIGGER",
    "ARMED",
    "TRIGGERED",
)


class DBAdapter:
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
                required_tables=frozenset({"lbank_catalog", "catalog_events"}),
            )

    def log_event(
        self,
        symbol: str,
        event_type: str,
    ):
        try:
            with connect_managed_sqlite(
                self.db_path,
                timeout=10.0,
            ) as conn:
                conn.execute(
                    """
                    INSERT INTO catalog_events (
                        symbol,
                        event_type,
                        timestamp
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        symbol,
                        event_type,
                        int(time.time()),
                    ),
                )

        except Exception as exc:
            logger.error(
                "Error logging event %s for %s: %s",
                event_type,
                symbol,
                exc,
            )

    def mark_removed(
        self,
        symbol: str,
    ):
        current_time = int(time.time())

        try:
            with connect_managed_sqlite(
                self.db_path,
                timeout=10.0,
            ) as conn:
                existing = conn.execute(
                    """
                    SELECT status
                    FROM lbank_catalog
                    WHERE symbol = ?
                    """,
                    (symbol,),
                ).fetchone()

                if not existing:
                    return

                if existing[0] == "REMOVED":
                    return

                conn.execute(
                    """
                    UPDATE lbank_catalog
                    SET
                        status = 'REMOVED',
                        scan_eligible = 0,
                        removed_at = ?,
                        trigger_data = '{}'
                    WHERE symbol = ?
                    """,
                    (
                        current_time,
                        symbol,
                    ),
                )

                conn.execute(
                    """
                    INSERT INTO catalog_events (
                        symbol,
                        event_type,
                        timestamp
                    )
                    VALUES (?, 'REMOVED', ?)
                    """,
                    (
                        symbol,
                        current_time,
                    ),
                )

        except Exception as exc:
            logger.error(
                "Error marking %s as removed: %s",
                symbol,
                exc,
            )

    def get_catalog_symbols(
        self,
    ) -> set[str]:
        """
        Return every currently known non-removed LBank catalogue symbol.

        This intentionally includes scan-ineligible contracts because catalogue
        membership and scanner eligibility are separate concepts.
        """
        try:
            with connect_managed_sqlite(
                self.db_path,
                timeout=10.0,
            ) as conn:
                rows = conn.execute(
                    """
                    SELECT symbol
                    FROM lbank_catalog
                    WHERE status != 'REMOVED'
                    """
                ).fetchall()

                return {
                    row[0]
                    for row in rows
                }

        except Exception as exc:
            logger.error(
                "Error reading LBank catalogue symbols: %s",
                exc,
            )
            return set()

    def get_tracked_symbols(
        self,
    ) -> set[str]:
        """
        Backward-compatible scanner-facing symbol getter.

        Only contracts that remain scan eligible are returned.
        """
        try:
            with connect_managed_sqlite(
                self.db_path,
                timeout=10.0,
            ) as conn:
                rows = conn.execute(
                    """
                    SELECT symbol
                    FROM lbank_catalog
                    WHERE
                        scan_eligible = 1
                        AND status IN (
                            'WATCH',
                            'FUEL-RICH',
                            'PRE-TRIGGER',
                            'ARMED',
                            'TRIGGERED'
                        )
                    """
                ).fetchall()

                return {
                    row[0]
                    for row in rows
                }

        except Exception as exc:
            logger.error(
                "Error reading tracked symbols: %s",
                exc,
            )
            return set()

    def update_candidates(
        self,
        candidates_map: Dict[str, Any],
    ):
        """
        Persist one successful LBank catalogue snapshot.

        Every symbol present in this map is known to be present in the current
        successful snapshot, therefore its consecutive-missing counter resets.

        `scan_eligible` controls whether the hunter is allowed to evaluate the
        contract. Catalogue membership itself does not imply scan eligibility.
        """
        current_time = int(time.time())

        try:
            with connect_managed_sqlite(
                self.db_path,
                timeout=20.0,
            ) as conn:
                for symbol, data in candidates_map.items():
                    price = float(
                        data.get(
                            "last_price",
                            0.0,
                        )
                        or 0.0
                    )

                    volume = float(
                        data.get(
                            "quote_volume",
                            0.0,
                        )
                        or 0.0
                    )

                    is_meme = (
                        1
                        if data.get("is_meme")
                        else 0
                    )

                    scan_eligible = (
                        1
                        if data.get(
                            "scan_eligible"
                        )
                        else 0
                    )

                    existing = conn.execute(
                        """
                        SELECT
                            status,
                            scan_eligible
                        FROM lbank_catalog
                        WHERE symbol = ?
                        """,
                        (symbol,),
                    ).fetchone()

                    if not existing:
                        conn.execute(
                            """
                            INSERT INTO lbank_catalog (
                                symbol,
                                last_price,
                                quote_volume,
                                is_meme,
                                scan_eligible,
                                status,
                                first_seen_at,
                                last_added_at,
                                last_seen_at,
                                removed_at,
                                consecutive_missing_snapshots,
                                lifecycle_id,
                                trigger_data
                            )
                            VALUES (
                                ?, ?, ?, ?, ?,
                                'WATCH',
                                ?, ?, ?,
                                NULL,
                                0,
                                1,
                                '{}'
                            )
                            """,
                            (
                                symbol,
                                price,
                                volume,
                                is_meme,
                                scan_eligible,
                                current_time,
                                current_time,
                                current_time,
                            ),
                        )

                        conn.execute(
                            """
                            INSERT INTO catalog_events (
                                symbol,
                                event_type,
                                timestamp
                            )
                            VALUES (?, 'ADDED', ?)
                            """,
                            (
                                symbol,
                                current_time,
                            ),
                        )

                        continue

                    old_status = existing[0]
                    old_scan_eligible = bool(
                        existing[1]
                    )

                    if old_status == "REMOVED":
                        conn.execute(
                            """
                            UPDATE lbank_catalog
                            SET
                                last_price = ?,
                                quote_volume = ?,
                                is_meme = ?,
                                scan_eligible = ?,
                                status = 'WATCH',
                                last_added_at = ?,
                                last_seen_at = ?,
                                removed_at = NULL,
                                consecutive_missing_snapshots = 0,
                                lifecycle_id = lifecycle_id + 1,
                                trigger_data = '{}'
                            WHERE symbol = ?
                            """,
                            (
                                price,
                                volume,
                                is_meme,
                                scan_eligible,
                                current_time,
                                current_time,
                                symbol,
                            ),
                        )

                        conn.execute(
                            """
                            INSERT INTO catalog_events (
                                symbol,
                                event_type,
                                timestamp
                            )
                            VALUES (?, 'ADDED', ?)
                            """,
                            (
                                symbol,
                                current_time,
                            ),
                        )

                        continue

                    if (
                        old_scan_eligible
                        != bool(scan_eligible)
                    ):
                        conn.execute(
                            """
                            UPDATE lbank_catalog
                            SET
                                last_price = ?,
                                quote_volume = ?,
                                is_meme = ?,
                                scan_eligible = ?,
                                status = 'WATCH',
                                last_seen_at = ?,
                                consecutive_missing_snapshots = 0,
                                lifecycle_id = lifecycle_id + 1,
                                trigger_data = '{}'
                            WHERE symbol = ?
                            """,
                            (
                                price,
                                volume,
                                is_meme,
                                scan_eligible,
                                current_time,
                                symbol,
                            ),
                        )

                        event_type = (
                            "SCAN_ELIGIBLE"
                            if scan_eligible
                            else "SCAN_INELIGIBLE"
                        )

                        conn.execute(
                            """
                            INSERT INTO catalog_events (
                                symbol,
                                event_type,
                                timestamp
                            )
                            VALUES (?, ?, ?)
                            """,
                            (
                                symbol,
                                event_type,
                                current_time,
                            ),
                        )

                        continue

                    conn.execute(
                        """
                        UPDATE lbank_catalog
                        SET
                            last_price = ?,
                            quote_volume = ?,
                            is_meme = ?,
                            scan_eligible = ?,
                            last_seen_at = ?,
                            consecutive_missing_snapshots = 0
                        WHERE symbol = ?
                        """,
                        (
                            price,
                            volume,
                            is_meme,
                            scan_eligible,
                            current_time,
                            symbol,
                        ),
                    )

        except Exception as exc:
            logger.error(
                "Error updating DB: %s",
                exc,
            )

    def record_missing_symbols(
        self,
        symbols: Iterable[str],
        removal_after: int = 2,
    ) -> set[str]:
        """
        Record absence from a *successful* LBank catalogue snapshot.

        A contract is removed only after `removal_after` consecutive successful
        snapshots in which it is absent.

        Failed catalogue fetches must never call this method.
        """
        symbol_set = {
            str(symbol)
            for symbol in symbols
            if symbol
        }

        if not symbol_set:
            return set()

        threshold = max(
            2,
            int(removal_after),
        )

        current_time = int(time.time())
        removed_now: set[str] = set()

        try:
            with connect_managed_sqlite(
                self.db_path,
                timeout=20.0,
            ) as conn:
                for symbol in symbol_set:
                    row = conn.execute(
                        """
                        SELECT
                            status,
                            consecutive_missing_snapshots
                        FROM lbank_catalog
                        WHERE symbol = ?
                        """,
                        (symbol,),
                    ).fetchone()

                    if not row:
                        continue

                    status = row[0]

                    if status == "REMOVED":
                        continue

                    previous_missing = int(
                        row[1]
                        or 0
                    )

                    new_missing = (
                        previous_missing + 1
                    )

                    if new_missing >= threshold:
                        conn.execute(
                            """
                            UPDATE lbank_catalog
                            SET
                                status = 'REMOVED',
                                scan_eligible = 0,
                                removed_at = ?,
                                consecutive_missing_snapshots = ?,
                                trigger_data = '{}'
                            WHERE symbol = ?
                            """,
                            (
                                current_time,
                                new_missing,
                                symbol,
                            ),
                        )

                        conn.execute(
                            """
                            INSERT INTO catalog_events (
                                symbol,
                                event_type,
                                timestamp
                            )
                            VALUES (?, 'REMOVED', ?)
                            """,
                            (
                                symbol,
                                current_time,
                            ),
                        )

                        removed_now.add(
                            symbol
                        )

                    else:
                        conn.execute(
                            """
                            UPDATE lbank_catalog
                            SET
                                consecutive_missing_snapshots = ?
                            WHERE symbol = ?
                            """,
                            (
                                new_missing,
                                symbol,
                            ),
                        )

        except Exception as exc:
            logger.error(
                "Error recording missing LBank symbols: %s",
                exc,
            )

        return removed_now

    def get_all_active_candidates(
        self,
    ) -> Dict[str, Any]:
        """
        Return only current scan-universe contracts.

        Catalogue-only contracts remain persisted in lbank_catalog but never
        enter the hunter until scan_eligible becomes true.
        """
        candidates: Dict[str, Any] = {}

        try:
            with connect_managed_sqlite(
                self.db_path,
                timeout=10.0,
            ) as conn:
                conn.row_factory = sqlite3.Row

                cursor = conn.execute(
                    """
                    SELECT *
                    FROM lbank_catalog
                    WHERE
                        scan_eligible = 1
                        AND status IN (
                            'WATCH',
                            'FUEL-RICH',
                            'PRE-TRIGGER',
                            'ARMED',
                            'TRIGGERED'
                        )
                    """
                )

                for row in cursor.fetchall():
                    candidates[
                        row["symbol"]
                    ] = dict(row)

        except Exception as exc:
            logger.error(
                "Error reading active candidates: %s",
                exc,
            )

        return candidates

    def update_candidate_state(
        self,
        symbol: str,
        new_state: str,
        trigger_data: Dict | None = None,
    ) -> bool:
        """
        Persist a state transition only while the contract remains scan eligible.

        A zero-row UPDATE can legitimately happen when catalogue refresh changes
        eligibility while an already-started analysis is still in flight. That
        lifecycle race is a benign skip and must not resurrect an ineligible or
        removed contract.

        Missing catalogue rows remain genuine persistence failures.
        """
        try:
            with connect_managed_sqlite(
                self.db_path,
                timeout=10.0,
            ) as conn:
                data_str = json.dumps(
                    trigger_data
                    if trigger_data
                    else {}
                )

                cursor = conn.execute(
                    """
                    UPDATE lbank_catalog
                    SET
                        status = ?,
                        trigger_data = ?
                    WHERE
                        symbol = ?
                        AND scan_eligible = 1
                    """,
                    (
                        new_state,
                        data_str,
                        symbol,
                    ),
                )

                if cursor.rowcount == 1:
                    return True

                current = conn.execute(
                    """
                    SELECT
                        status,
                        scan_eligible
                    FROM lbank_catalog
                    WHERE symbol = ?
                    """,
                    (symbol,),
                ).fetchone()

                if current is None:
                    logger.error(
                        "Candidate state update failed because catalogue row "
                        "is missing for %s",
                        symbol,
                    )
                    return False

                current_status = str(
                    current[0]
                    or ""
                )

                scan_eligible = bool(
                    current[1]
                )

                if (
                    not scan_eligible
                    or current_status == "REMOVED"
                ):
                    logger.info(
                        "Candidate state update skipped after lifecycle change "
                        "for %s -> %s "
                        "(status=%s scan_eligible=%s)",
                        symbol,
                        new_state,
                        current_status,
                        int(scan_eligible),
                    )
                    return True

                logger.error(
                    "Candidate state update affected %s rows for %s "
                    "despite scan_eligible=1 status=%s",
                    cursor.rowcount,
                    symbol,
                    current_status,
                )
                return False

        except Exception as exc:
            logger.error(
                "Candidate state update failed for %s: %s",
                symbol,
                exc,
            )
            return False

    def transition_candidate_state(
        self,
        symbol: str,
        expected_state: str,
        new_state: str,
        trigger_data: Dict | None = None,
    ) -> bool:
        """Atomically persist a state transition from the expected live state.

        Unlike ``update_candidate_state``, a lifecycle or state mismatch is a
        hard failure. Signal-producing callers use this compare-and-set path so
        an in-flight stale evaluation cannot persist or announce a trigger.
        """
        try:
            with connect_managed_sqlite(
                self.db_path,
                timeout=10.0,
            ) as conn:
                data_str = json.dumps(
                    trigger_data
                    if trigger_data
                    else {}
                )

                cursor = conn.execute(
                    """
                    UPDATE lbank_catalog
                    SET
                        status = ?,
                        trigger_data = ?
                    WHERE
                        symbol = ?
                        AND scan_eligible = 1
                        AND status = ?
                    """,
                    (
                        new_state,
                        data_str,
                        symbol,
                        expected_state,
                    ),
                )

                if cursor.rowcount == 1:
                    return True

                current = conn.execute(
                    """
                    SELECT status, scan_eligible
                    FROM lbank_catalog
                    WHERE symbol = ?
                    """,
                    (symbol,),
                ).fetchone()

                if current is None:
                    logger.error(
                        "Candidate transition failed because catalogue row "
                        "is missing for %s",
                        symbol,
                    )
                    return False

                logger.info(
                    "Candidate transition rejected as stale for %s: "
                    "expected=%s actual=%s scan_eligible=%s target=%s",
                    symbol,
                    expected_state,
                    str(current[0] or ""),
                    int(bool(current[1])),
                    new_state,
                )
                return False

        except Exception as exc:
            logger.error(
                "Candidate transition failed for %s: %s",
                symbol,
                exc,
            )
            return False
