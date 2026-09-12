import asyncio
import json
import logging
import math
import sqlite3
import time
from collections.abc import Awaitable, Callable
from typing import Any

from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.schema_contract import require_managed_schema


logger = logging.getLogger(
    "WaterfallHunter.LBankSignalOutcome"
)

MINUTE_MS = 60_000
DEFAULT_HORIZON_SECONDS = 86_400
PRICE_SOURCE = "closed_1m_trade_ohlcv_proxy"


class LBankSignalOutcomeStore:
    """Append-only observational outcomes linked to immutable signals."""

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
                    {"lbank_signal_outcomes", "lbank_signal_ledger"}
                ),
            )

    def pending_signals(
        self,
        *,
        mature_before: int,
        limit: int = 5,
    ) -> list[dict]:
        try:
            with connect_managed_sqlite(
                self.db_path,
                timeout=10.0,
            ) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT
                        s.signal_id AS id,
                        s.symbol,
                        s.triggered_at,
                        s.entry_price,
                        s.stop_loss,
                        s.take_profit_1,
                        s.take_profit_2,
                        s.trigger_metrics_json,
                        s.signal_class,
                        s.strategy_profile
                    FROM canonical_signal_view AS s
                    LEFT JOIN lbank_signal_outcomes AS o
                        ON o.signal_id = s.signal_id
                    WHERE
                        o.signal_id IS NULL
                        AND s.triggered_at <= ?
                    ORDER BY s.triggered_at, s.signal_id
                    LIMIT ?
                    """,
                    (
                        int(mature_before),
                        max(1, int(limit)),
                    ),
                ).fetchall()
                return [dict(row) for row in rows]
        except Exception as exc:
            logger.error(
                "Pending signal outcome query failed: %s",
                exc,
            )
            return []

    def append_outcome(
        self,
        signal: dict,
        outcome: dict,
        *,
        source_exchange: str | None,
        source_mapped_symbol: str | None,
        resolved_at: int | None = None,
    ) -> bool:
        try:
            details_json = json.dumps(
                outcome.get("details") or {},
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            with connect_managed_sqlite(
                self.db_path,
                timeout=10.0,
            ) as conn:
                conn.execute(
                    "PRAGMA foreign_keys=ON"
                )
                conn.execute(
                    """
                    INSERT INTO lbank_signal_outcomes (
                        signal_id,
                        symbol,
                        outcome_status,
                        signal_triggered_at,
                        observation_started_at,
                        observation_ended_at,
                        horizon_seconds,
                        price_source,
                        source_exchange,
                        source_mapped_symbol,
                        first_tp1_at,
                        first_tp2_at,
                        first_stop_at,
                        min_price,
                        max_price,
                        mfe_pct,
                        mae_pct,
                        observed_candles,
                        expected_candles,
                        details_json,
                        observational_only,
                        trade_eligible,
                        resolved_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, 1, NULL, ?
                    )
                    """,
                    (
                        int(signal["id"]),
                        str(signal["symbol"]),
                        str(outcome["status"]),
                        int(signal["triggered_at"]),
                        outcome.get("observation_started_at"),
                        outcome.get("observation_ended_at"),
                        int(outcome["horizon_seconds"]),
                        PRICE_SOURCE,
                        source_exchange,
                        source_mapped_symbol,
                        outcome.get("first_tp1_at"),
                        outcome.get("first_tp2_at"),
                        outcome.get("first_stop_at"),
                        outcome.get("min_price"),
                        outcome.get("max_price"),
                        outcome.get("mfe_pct"),
                        outcome.get("mae_pct"),
                        int(outcome.get("observed_candles") or 0),
                        int(outcome.get("expected_candles") or 0),
                        details_json,
                        int(time.time() if resolved_at is None else resolved_at),
                    ),
                )
            return True
        except sqlite3.IntegrityError as exc:
            logger.warning(
                "Signal outcome append rejected for signal %s: %s",
                signal.get("id"),
                exc,
            )
            return False
        except Exception as exc:
            logger.error(
                "Signal outcome append failed for signal %s: %s",
                signal.get("id"),
                exc,
            )
            return False


class LBankSignalOutcomeEvaluator:
    """Deterministic short-side outcome classification from closed 1m bars."""

    @staticmethod
    def _finite(value: Any) -> float | None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
        ):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    @classmethod
    def evaluate(
        cls,
        signal: dict,
        candles: list,
        *,
        horizon_seconds: int = DEFAULT_HORIZON_SECONDS,
    ) -> dict:
        trigger_ms = int(signal["triggered_at"]) * 1000
        start_ms = (
            trigger_ms
            if trigger_ms % MINUTE_MS == 0
            else (
                (trigger_ms // MINUTE_MS) + 1
            ) * MINUTE_MS
        )
        end_ms = start_ms + int(horizon_seconds) * 1000
        first_fetch_ms = (
            trigger_ms // MINUTE_MS
        ) * MINUTE_MS
        expected_timestamps = list(
            range(
                first_fetch_ms,
                end_ms,
                MINUTE_MS,
            )
        )

        levels = {
            "entry": cls._finite(signal.get("entry_price")),
            "stop": cls._finite(signal.get("stop_loss")),
            "tp1": cls._finite(signal.get("take_profit_1")),
            "tp2": cls._finite(signal.get("take_profit_2")),
        }
        if (
            any(value is None or value <= 0 for value in levels.values())
            or not (
                levels["tp2"] < levels["tp1"]
                < levels["entry"] < levels["stop"]
            )
        ):
            return cls._packet(
                "UNRESOLVABLE_SIGNAL_LEVELS",
                start_ms,
                end_ms,
                horizon_seconds,
                [],
                len(expected_timestamps),
                details={"reason": "invalid short position levels"},
            )

        rows: dict[int, tuple[float, float]] = {}
        invalid_rows = 0
        for candle in candles or []:
            try:
                timestamp = int(candle[0])
                high = float(candle[2])
                low = float(candle[3])
                if (
                    timestamp % MINUTE_MS != 0
                    or not math.isfinite(high)
                    or not math.isfinite(low)
                    or high <= 0
                    or low <= 0
                    or high < low
                ):
                    raise ValueError
                rows[timestamp] = (high, low)
            except (IndexError, TypeError, ValueError):
                invalid_rows += 1

        missing = [
            timestamp
            for timestamp in expected_timestamps
            if timestamp not in rows
        ]
        observed = [
            (timestamp, *rows[timestamp])
            for timestamp in expected_timestamps
            if timestamp in rows
        ]
        path_rows = [
            row for row in observed
            if start_ms <= row[0] < end_ms
        ]
        expected_path_candles = int(
            horizon_seconds
        ) // 60

        if missing or invalid_rows:
            return cls._packet(
                "DATA_INCOMPLETE",
                start_ms,
                end_ms,
                horizon_seconds,
                path_rows,
                expected_path_candles,
                details={
                    "missing_candles": len(missing),
                    "invalid_candles": invalid_rows,
                },
                entry=levels["entry"],
            )

        if first_fetch_ms < start_ms:
            high, low = rows[first_fetch_ms]
            if (
                high >= levels["stop"]
                or low <= levels["tp1"]
            ):
                return cls._packet(
                    "UNRESOLVABLE_TRIGGER_MINUTE",
                    start_ms,
                    end_ms,
                    horizon_seconds,
                    path_rows,
                    expected_path_candles,
                    details={
                        "reason": (
                            "a monitored level was touched in the "
                            "partially pre-trigger candle"
                        )
                    },
                    entry=levels["entry"],
                )

        first_tp1_at = None
        first_tp2_at = None
        first_stop_at = None
        status = "NO_LEVEL_HIT_24H"

        for timestamp, high, low in path_rows:
            hit_stop = high >= levels["stop"]
            hit_tp1 = low <= levels["tp1"]
            hit_tp2 = low <= levels["tp2"]

            if hit_tp1 and first_tp1_at is None:
                first_tp1_at = timestamp // 1000
            if hit_tp2 and first_tp2_at is None:
                first_tp2_at = timestamp // 1000
            if hit_stop and first_stop_at is None:
                first_stop_at = timestamp // 1000

            if hit_stop and hit_tp1:
                status = "AMBIGUOUS_INTRACANDLE_PATH"
                break
            if hit_stop:
                status = (
                    "TP1_THEN_STOP"
                    if first_tp1_at is not None
                    else "STOP_FIRST"
                )
                break
            if hit_tp2:
                status = (
                    "TP2_AFTER_TP1"
                    if first_tp1_at is not None
                    and first_tp1_at < timestamp // 1000
                    else "TP2_FIRST"
                )
                break

        else:
            if first_tp1_at is not None:
                status = "TP1_ONLY_24H"

        return cls._packet(
            status,
            start_ms,
            end_ms,
            horizon_seconds,
            path_rows,
            expected_path_candles,
            first_tp1_at=first_tp1_at,
            first_tp2_at=first_tp2_at,
            first_stop_at=first_stop_at,
            details={
                "direction": "short",
                "level_price_sources": {
                    "take_profit": "best_ask_at_signal_design",
                    "stop_loss": "mark_price_at_signal_design",
                },
                "outcome_price_source": PRICE_SOURCE,
            },
            entry=levels["entry"],
        )

    @staticmethod
    def _packet(
        status: str,
        start_ms: int,
        end_ms: int,
        horizon_seconds: int,
        observed: list[tuple[int, float, float]],
        expected_candles: int,
        *,
        first_tp1_at: int | None = None,
        first_tp2_at: int | None = None,
        first_stop_at: int | None = None,
        details: dict | None = None,
        entry: float | None = None,
    ) -> dict:
        highs = [row[1] for row in observed]
        lows = [row[2] for row in observed]
        min_price = min(lows) if lows else None
        max_price = max(highs) if highs else None
        mfe_pct = (
            (entry - min_price) / entry * 100
            if entry and min_price is not None
            else None
        )
        mae_pct = (
            (max_price - entry) / entry * 100
            if entry and max_price is not None
            else None
        )
        return {
            "status": status,
            "observation_started_at": start_ms // 1000,
            "observation_ended_at": end_ms // 1000,
            "horizon_seconds": int(horizon_seconds),
            "first_tp1_at": first_tp1_at,
            "first_tp2_at": first_tp2_at,
            "first_stop_at": first_stop_at,
            "min_price": min_price,
            "max_price": max_price,
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
            "observed_candles": len(observed),
            "expected_candles": expected_candles,
            "details": details or {},
        }


class LBankSignalSettlementWorker:
    """Settle mature natural signals without affecting trading decisions."""

    def __init__(
        self,
        store: LBankSignalOutcomeStore,
        candle_fetcher: Callable[
            [dict, int, int],
            Awaitable[list],
        ],
        *,
        horizon_seconds: int = DEFAULT_HORIZON_SECONDS,
        close_delay_seconds: int = 120,
        batch_size: int = 3,
    ):
        self.store = store
        self.candle_fetcher = candle_fetcher
        self.horizon_seconds = max(60, int(horizon_seconds))
        self.close_delay_seconds = max(60, int(close_delay_seconds))
        self.batch_size = max(1, int(batch_size))
        self._running = False
        self.last_started_at: float | None = None
        self.last_progress_at: float | None = None
        self.last_completed_at: float | None = None
        self.last_error_at: float | None = None
        self.total_cycles = 0
        self.total_failures = 0

    @staticmethod
    def _source(signal: dict) -> tuple[str | None, str | None]:
        try:
            metrics = json.loads(signal.get("trigger_metrics_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            metrics = {}
        exchange = metrics.get("exchange")
        mapped_symbol = metrics.get("mapped_symbol")
        return (
            str(exchange) if exchange else None,
            str(mapped_symbol) if mapped_symbol else None,
        )

    async def settle_once(
        self,
        *,
        now: int | None = None,
    ) -> int:
        current_time = int(time.time() if now is None else now)
        mature_before = (
            current_time
            - self.horizon_seconds
            - 60
            - self.close_delay_seconds
        )
        signals = self.store.pending_signals(
            mature_before=mature_before,
            limit=self.batch_size,
        )
        settled = 0

        for signal in signals:
            exchange, mapped_symbol = self._source(signal)
            if not exchange or not mapped_symbol:
                outcome = LBankSignalOutcomeEvaluator.evaluate(
                    signal,
                    [],
                    horizon_seconds=self.horizon_seconds,
                )
                outcome["status"] = "UNRESOLVABLE_SIGNAL_SOURCE"
                outcome["details"] = {
                    "reason": "captured exchange or mapped symbol missing"
                }
            else:
                trigger_ms = int(signal["triggered_at"]) * 1000
                fetch_start_ms = (
                    trigger_ms // MINUTE_MS
                ) * MINUTE_MS
                observation_start_ms = (
                    trigger_ms
                    if trigger_ms % MINUTE_MS == 0
                    else fetch_start_ms + MINUTE_MS
                )
                fetch_end_ms = (
                    observation_start_ms
                    + self.horizon_seconds * 1000
                )
                try:
                    candles = await self.candle_fetcher(
                        signal,
                        fetch_start_ms,
                        fetch_end_ms,
                    )
                except Exception as exc:
                    logger.warning(
                        "Signal outcome candle fetch failed for signal %s: %s",
                        signal.get("id"),
                        exc,
                    )
                    continue

                if not candles:
                    continue

                outcome = LBankSignalOutcomeEvaluator.evaluate(
                    signal,
                    candles,
                    horizon_seconds=self.horizon_seconds,
                )

            if self.store.append_outcome(
                signal,
                outcome,
                source_exchange=exchange,
                source_mapped_symbol=mapped_symbol,
                resolved_at=current_time,
            ):
                settled += 1

        return settled

    async def run_forever(
        self,
        *,
        interval_seconds: float = 900.0,
    ) -> None:
        self._running = True
        self.last_started_at = time.time()
        interval = max(60.0, float(interval_seconds))
        try:
            while self._running:
                self.last_progress_at = time.time()
                self.total_cycles += 1
                try:
                    settled = await self.settle_once()
                    self.last_completed_at = time.time()
                    if settled:
                        logger.info(
                            "Settled %s observational signal outcomes",
                            settled,
                        )
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    self.total_failures += 1
                    self.last_error_at = time.time()
                    logger.error(
                        "Signal settlement cycle failed: %s",
                        exc,
                    )
                await asyncio.sleep(interval)
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False

    def health_snapshot(self) -> dict:
        return {
            "running": self._running,
            "last_started_at": self.last_started_at,
            "last_progress_at": self.last_progress_at,
            "last_completed_at": self.last_completed_at,
            "last_error_at": self.last_error_at,
            "total_cycles": self.total_cycles,
            "total_failures": self.total_failures,
        }
