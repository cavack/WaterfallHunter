import json
import logging
import sqlite3
import time
from typing import Any

from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.schema_contract import require_managed_schema


logger = logging.getLogger(
    "WaterfallHunter.LBankExecutionStore"
)


EXECUTION_STATUS_UNKNOWN = "UNKNOWN"
EXECUTION_STATUS_OBSERVED = "OBSERVED"
EXECUTION_STATUS_UNAVAILABLE = "UNAVAILABLE"

VALID_EXECUTION_STATUSES = frozenset(
    {
        EXECUTION_STATUS_UNKNOWN,
        EXECUTION_STATUS_OBSERVED,
        EXECUTION_STATUS_UNAVAILABLE,
    }
)


class LBankExecutionStore:
    """
    Persistence for read-only LBank execution observations.

    Two persistence layers are intentionally maintained:

    1. lbank_execution_observations
       Latest/current observation per symbol.
       Used for queue scheduling and current operational state.

    2. lbank_execution_observation_history
       Append-only evidence for every real observation attempt.
       Used later for temporal statistics and execution-quality research.

    This store is deliberately independent from lbank_catalog state.

    It must never:
    - change scan_eligible
    - change WATCH/FUEL-RICH/PRE-TRIGGER/ARMED/TRIGGERED
    - place orders
    - send alerts
    - determine strategy eligibility

    Suitability thresholds are intentionally absent.
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
                    {
                        "lbank_catalog",
                        "lbank_execution_observations",
                        "lbank_execution_observation_history",
                    }
                ),
            )

    def _connect(
        self,
        timeout: float = 10.0,
    ):
        return connect_managed_sqlite(
            self.db_path,
            timeout=timeout,
        )

    @staticmethod
    def _finite(
        value: Any,
    ) -> float | None:
        if isinstance(
            value,
            bool,
        ):
            return None

        if not isinstance(
            value,
            (int, float),
        ):
            return None

        number = float(
            value
        )

        if number != number:
            return None

        if number in (
            float("inf"),
            float("-inf"),
        ):
            return None

        return number

    @classmethod
    def _extract_cost(
        cls,
        packet: dict,
        notional: str,
    ) -> float | None:
        execution = (
            packet.get("execution")
            if isinstance(
                packet.get("execution"),
                dict,
            )
            else {}
        )

        entry = (
            execution.get(notional)
            if isinstance(
                execution.get(notional),
                dict,
            )
            else {}
        )

        return cls._finite(
            entry.get(
                "effective_crossing_cost_pct"
            )
        )

    @classmethod
    def _extract_depth(
        cls,
        packet: dict,
        band: str,
    ) -> float | None:
        depth = (
            packet.get("depth")
            if isinstance(
                packet.get("depth"),
                dict,
            )
            else {}
        )

        bounded = (
            depth.get("bounded")
            if isinstance(
                depth.get("bounded"),
                dict,
            )
            else {}
        )

        entry = (
            bounded.get(band)
            if isinstance(
                bounded.get(band),
                dict,
            )
            else {}
        )

        return cls._finite(
            entry.get(
                "minimum_side_depth_usdt"
            )
        )

    def ensure_symbol(
        self,
        symbol: str,
    ):
        """
        Ensure queue state exists without claiming an observation occurred.

        This must never create a history row.
        """
        if not symbol:
            return

        now = time.time()

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO
                    lbank_execution_observations (
                        symbol,
                        observation_status,
                        failures,
                        next_check_at,
                        payload,
                        updated_at
                    )
                    VALUES (
                        ?,
                        'UNKNOWN',
                        0,
                        0,
                        '{}',
                        ?
                    )
                    ON CONFLICT(symbol)
                    DO NOTHING
                    """,
                    (
                        symbol,
                        now,
                    ),
                )

        except Exception as exc:
            logger.error(
                "Failed ensuring execution symbol %s: %s",
                symbol,
                exc,
            )

    def ensure_symbols(
        self,
        symbols,
    ):
        """
        Ensure queue state for multiple symbols.

        This must never create history rows.
        """
        now = time.time()

        rows = [
            (
                str(symbol),
                now,
            )
            for symbol in symbols
            if symbol
        ]

        if not rows:
            return

        try:
            with self._connect(
                timeout=20.0,
            ) as conn:
                conn.executemany(
                    """
                    INSERT INTO
                    lbank_execution_observations (
                        symbol,
                        observation_status,
                        failures,
                        next_check_at,
                        payload,
                        updated_at
                    )
                    VALUES (
                        ?,
                        'UNKNOWN',
                        0,
                        0,
                        '{}',
                        ?
                    )
                    ON CONFLICT(symbol)
                    DO NOTHING
                    """,
                    rows,
                )

        except Exception as exc:
            logger.error(
                "Failed ensuring execution symbols: %s",
                exc,
            )

    def record_observation(
        self,
        symbol: str,
        packet: dict,
        *,
        next_check_at: float = 0.0,
    ) -> bool:
        """
        Persist one real public LBank execution observation.

        A successful measurement is OBSERVED.
        A failed/unavailable measurement is UNAVAILABLE.

        Atomic persistence contract:
        - latest/current row is inserted or updated
        - exactly one append-only history row is inserted
        - both writes occur in the same SQLite transaction

        If either write fails, neither write is committed.

        No suitability decision is made here.
        """
        if (
            not symbol
            or not isinstance(
                packet,
                dict,
            )
        ):
            return False

        now = time.time()

        available = (
            packet.get("available")
            is True
        )

        if available:
            observation_status = (
                EXECUTION_STATUS_OBSERVED
            )

            observed_at = (
                self._finite(
                    packet.get(
                        "observed_at"
                    )
                )
                or now
            )

            reason = None
            failures = 0

        else:
            observation_status = (
                EXECUTION_STATUS_UNAVAILABLE
            )

            observed_at = now

            reason = str(
                packet.get("reason")
                or "execution observation unavailable"
            )

            failures = None

        spread_pct = self._finite(
            packet.get(
                "spread_pct"
            )
        )

        cost_25_pct = self._extract_cost(
            packet,
            "25",
        )

        cost_50_pct = self._extract_cost(
            packet,
            "50",
        )

        cost_100_pct = self._extract_cost(
            packet,
            "100",
        )

        depth_10bps_min_usdt = (
            self._extract_depth(
                packet,
                "10",
            )
        )

        depth_25bps_min_usdt = (
            self._extract_depth(
                packet,
                "25",
            )
        )

        depth_50bps_min_usdt = (
            self._extract_depth(
                packet,
                "50",
            )
        )

        depth_100bps_min_usdt = (
            self._extract_depth(
                packet,
                "100",
            )
        )

        payload = json.dumps(
            packet,
            ensure_ascii=False,
            separators=(
                ",",
                ":",
            ),
            sort_keys=True,
        )

        try:
            with self._connect(
                timeout=20.0,
            ) as conn:
                existing = conn.execute(
                    """
                    SELECT failures
                    FROM lbank_execution_observations
                    WHERE symbol = ?
                    """,
                    (
                        symbol,
                    ),
                ).fetchone()

                previous_failures = (
                    int(
                        existing[0]
                        or 0
                    )
                    if existing
                    else 0
                )

                if failures is None:
                    failures = (
                        previous_failures
                        + 1
                    )

                conn.execute(
                    """
                    INSERT INTO
                    lbank_execution_observations (
                        symbol,
                        observation_status,
                        observed_at,
                        reason,
                        spread_pct,
                        cost_25_pct,
                        cost_50_pct,
                        cost_100_pct,
                        depth_10bps_min_usdt,
                        depth_25bps_min_usdt,
                        depth_50bps_min_usdt,
                        depth_100bps_min_usdt,
                        failures,
                        next_check_at,
                        payload,
                        updated_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?
                    )
                    ON CONFLICT(symbol)
                    DO UPDATE SET
                        observation_status =
                            excluded.observation_status,

                        observed_at =
                            excluded.observed_at,

                        reason =
                            excluded.reason,

                        spread_pct =
                            excluded.spread_pct,

                        cost_25_pct =
                            excluded.cost_25_pct,

                        cost_50_pct =
                            excluded.cost_50_pct,

                        cost_100_pct =
                            excluded.cost_100_pct,

                        depth_10bps_min_usdt =
                            excluded.depth_10bps_min_usdt,

                        depth_25bps_min_usdt =
                            excluded.depth_25bps_min_usdt,

                        depth_50bps_min_usdt =
                            excluded.depth_50bps_min_usdt,

                        depth_100bps_min_usdt =
                            excluded.depth_100bps_min_usdt,

                        failures =
                            excluded.failures,

                        next_check_at =
                            excluded.next_check_at,

                        payload =
                            excluded.payload,

                        updated_at =
                            excluded.updated_at
                    """,
                    (
                        symbol,
                        observation_status,
                        observed_at,
                        reason,
                        spread_pct,
                        cost_25_pct,
                        cost_50_pct,
                        cost_100_pct,
                        depth_10bps_min_usdt,
                        depth_25bps_min_usdt,
                        depth_50bps_min_usdt,
                        depth_100bps_min_usdt,
                        failures,
                        float(
                            next_check_at
                            or 0.0
                        ),
                        payload,
                        now,
                    ),
                )

                conn.execute(
                    """
                    INSERT INTO
                    lbank_execution_observation_history (
                        symbol,
                        observation_status,
                        observed_at,
                        reason,
                        spread_pct,
                        cost_25_pct,
                        cost_50_pct,
                        cost_100_pct,
                        depth_10bps_min_usdt,
                        depth_25bps_min_usdt,
                        depth_50bps_min_usdt,
                        depth_100bps_min_usdt,
                        payload,
                        created_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?,
                        ?, ?
                    )
                    """,
                    (
                        symbol,
                        observation_status,
                        observed_at,
                        reason,
                        spread_pct,
                        cost_25_pct,
                        cost_50_pct,
                        cost_100_pct,
                        depth_10bps_min_usdt,
                        depth_25bps_min_usdt,
                        depth_50bps_min_usdt,
                        depth_100bps_min_usdt,
                        payload,
                        now,
                    ),
                )

            return True

        except Exception as exc:
            logger.error(
                "Failed recording LBank execution observation for %s: %s",
                symbol,
                exc,
            )
            return False

    def get_observation(
        self,
        symbol: str,
    ) -> dict | None:
        try:
            with self._connect() as conn:
                conn.row_factory = (
                    sqlite3.Row
                )

                row = conn.execute(
                    """
                    SELECT *
                    FROM lbank_execution_observations
                    WHERE symbol = ?
                    """,
                    (
                        symbol,
                    ),
                ).fetchone()

                if row is None:
                    return None

                result = dict(
                    row
                )

                try:
                    result["payload"] = (
                        json.loads(
                            result.get(
                                "payload"
                            )
                            or "{}"
                        )
                    )
                except Exception:
                    result["payload"] = {}

                return result

        except Exception as exc:
            logger.error(
                "Failed reading LBank execution observation for %s: %s",
                symbol,
                exc,
            )
            return None

    def get_history(
        self,
        symbol: str | None = None,
        *,
        limit: int = 100,
    ) -> list[dict]:
        """
        Read append-only execution evidence.

        This is observational access only and performs no classification.
        """
        history_limit = max(
            1,
            min(
                int(limit),
                10_000,
            ),
        )

        try:
            with self._connect() as conn:
                conn.row_factory = (
                    sqlite3.Row
                )

                if symbol:
                    rows = conn.execute(
                        """
                        SELECT *
                        FROM lbank_execution_observation_history
                        WHERE symbol = ?
                        ORDER BY
                            observed_at ASC,
                            id ASC
                        LIMIT ?
                        """,
                        (
                            symbol,
                            history_limit,
                        ),
                    ).fetchall()

                else:
                    rows = conn.execute(
                        """
                        SELECT *
                        FROM lbank_execution_observation_history
                        ORDER BY
                            observed_at ASC,
                            id ASC
                        LIMIT ?
                        """,
                        (
                            history_limit,
                        ),
                    ).fetchall()

                results = []

                for row in rows:
                    result = dict(
                        row
                    )

                    try:
                        result["payload"] = (
                            json.loads(
                                result.get(
                                    "payload"
                                )
                                or "{}"
                            )
                        )
                    except Exception:
                        result["payload"] = {}

                    results.append(
                        result
                    )

                return results

        except Exception as exc:
            logger.error(
                "Failed reading LBank execution history: %s",
                exc,
            )
            return []

    def count_history(
        self,
        symbol: str | None = None,
    ) -> int:
        try:
            with self._connect() as conn:
                if symbol:
                    row = conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM lbank_execution_observation_history
                        WHERE symbol = ?
                        """,
                        (
                            symbol,
                        ),
                    ).fetchone()

                else:
                    row = conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM lbank_execution_observation_history
                        """
                    ).fetchone()

                return int(
                    row[0]
                    if row
                    else 0
                )

        except Exception as exc:
            logger.error(
                "Failed counting LBank execution history: %s",
                exc,
            )
            return 0

    def get_queue(
        self,
        limit: int = 20,
        *,
        now: float | None = None,
    ) -> list[dict]:
        """
        Return a bounded, fair shadow-observation queue.

        Important:
        - this does not alter scan_eligible
        - never-observed contracts are processed first
        - then the oldest observation is processed first
        - meme is a priority modifier, not an eligibility gate
        - 24h volume is a tie-break/ranking feature, not a hard gate
        """
        queue_limit = max(
            1,
            min(
                int(limit),
                100,
            ),
        )

        reference_time = float(
            now
            if now is not None
            else time.time()
        )

        try:
            with self._connect() as conn:
                conn.row_factory = (
                    sqlite3.Row
                )

                rows = conn.execute(
                    """
                    SELECT
                        c.symbol,
                        c.last_price,
                        c.quote_volume,
                        c.is_meme,
                        c.scan_eligible,
                        c.status AS hunter_status,
                        c.first_seen_at,
                        c.last_added_at,
                        c.last_seen_at,

                        e.observation_status,
                        e.observed_at,
                        e.reason,
                        e.failures,
                        e.next_check_at

                    FROM lbank_catalog AS c

                    LEFT JOIN
                        lbank_execution_observations AS e
                    ON
                        e.symbol = c.symbol

                    WHERE
                        c.status != 'REMOVED'

                        AND c.last_price > 0

                        AND c.last_price <= 1

                        AND (
                            e.next_check_at IS NULL
                            OR e.next_check_at <= ?
                        )

                    ORDER BY
                        CASE
                            WHEN e.symbol IS NULL
                                THEN 0
                            WHEN e.observed_at IS NULL
                                THEN 0
                            ELSE 1
                        END ASC,

                        COALESCE(
                            e.observed_at,
                            0
                        ) ASC,

                        c.is_meme DESC,

                        c.quote_volume DESC,

                        c.symbol ASC

                    LIMIT ?
                    """,
                    (
                        reference_time,
                        queue_limit,
                    ),
                ).fetchall()

                return [
                    dict(row)
                    for row in rows
                ]

        except Exception as exc:
            logger.error(
                "Failed reading LBank execution queue: %s",
                exc,
            )
            return []

    def count_statuses(
        self,
    ) -> dict[str, int]:
        counts = {
            status: 0
            for status in (
                EXECUTION_STATUS_UNKNOWN,
                EXECUTION_STATUS_OBSERVED,
                EXECUTION_STATUS_UNAVAILABLE,
            )
        }

        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT
                        observation_status,
                        COUNT(*)
                    FROM lbank_execution_observations
                    GROUP BY observation_status
                    """
                ).fetchall()

                for status, count in rows:
                    counts[
                        str(status)
                    ] = int(count)

        except Exception as exc:
            logger.error(
                "Failed reading execution status counts: %s",
                exc,
            )

        return counts
