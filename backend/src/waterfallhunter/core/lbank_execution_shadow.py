import asyncio
import logging
import time
from typing import Any

from waterfallhunter.core.lbank_execution import (
    LBankExecutionObserver,
)
from waterfallhunter.core.lbank_execution_store import (
    LBankExecutionStore,
)


logger = logging.getLogger(
    "WaterfallHunter.LBankExecutionShadow"
)


class LBankExecutionShadowWorker:
    """
    Bounded read-only shadow execution observer.

    This worker intentionally does NOT:
    - change scan_eligible
    - change hunter state
    - change score
    - send Telegram alerts
    - place or cancel orders
    - decide SUITABLE / UNSUITABLE

    It only:
    1. reads a bounded queue from LBankExecutionStore,
    2. measures public LBank execution conditions,
    3. persists observational evidence.

    Eligibility decisions remain disabled during L3-A2.
    """

    def __init__(
        self,
        store: LBankExecutionStore,
        observer: LBankExecutionObserver | None = None,
        *,
        batch_size: int = 8,
        success_recheck_seconds: float = 1800.0,
        failure_recheck_seconds: float = 600.0,
    ):
        self.store = store

        self.observer = (
            observer
            if observer is not None
            else LBankExecutionObserver(
                notionals=(
                    25,
                    50,
                    100,
                ),
                orderbook_limit=50,
                depth_bands_bps=(
                    10,
                    25,
                    50,
                    100,
                ),
            )
        )

        self.batch_size = max(
            1,
            min(
                int(batch_size),
                25,
            ),
        )

        self.success_recheck_seconds = max(
            60.0,
            float(
                success_recheck_seconds
            ),
        )

        self.failure_recheck_seconds = max(
            60.0,
            float(
                failure_recheck_seconds
            ),
        )

        self.last_started_at: float | None = None
        self.last_completed_at: float | None = None
        self.last_progress_at: float | None = None

        self.total_attempted = 0
        self.total_observed = 0
        self.total_unavailable = 0

        self._running = False
        self._run_lock = asyncio.Lock()

    @staticmethod
    def _symbol_from_row(
        row: dict[str, Any],
    ) -> str | None:
        symbol = row.get(
            "symbol"
        )

        if (
            not isinstance(
                symbol,
                str,
            )
            or not symbol.strip()
        ):
            return None

        return symbol.strip()

    async def run_once(
        self,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """
        Execute one bounded shadow batch.

        Concurrent run_once calls are serialized.

        Returns operational statistics only.
        """
        async with self._run_lock:
            started_at = float(
                now
                if now is not None
                else time.time()
            )

            self.last_started_at = (
                started_at
            )

            queue = self.store.get_queue(
                limit=self.batch_size,
                now=started_at,
            )

            symbols = []

            for row in queue:
                if not isinstance(
                    row,
                    dict,
                ):
                    continue

                symbol = (
                    self._symbol_from_row(
                        row
                    )
                )

                if (
                    symbol
                    and symbol not in symbols
                ):
                    symbols.append(
                        symbol
                    )

            if not symbols:
                self.last_progress_at = (
                    time.time()
                )

                self.last_completed_at = (
                    self.last_progress_at
                )

                return {
                    "attempted": 0,
                    "observed": 0,
                    "unavailable": 0,
                    "symbols": [],
                }

            attempted = 0
            observed = 0
            unavailable = 0

            results: dict[
                str,
                dict,
            ] = {}

            try:
                results = (
                    await self.observer
                    .observe_many(
                        symbols
                    )
                )

            except Exception as exc:
                logger.warning(
                    "LBank shadow batch failed: %s",
                    exc,
                )

                results = {
                    symbol: {
                        "available": False,
                        "symbol": symbol,
                        "source_exchange": "lbank",
                        "reason": (
                            "shadow batch failed: "
                            f"{type(exc).__name__}: "
                            f"{exc}"
                        ),
                    }
                    for symbol in symbols
                }

            for symbol in symbols:
                attempted += 1

                packet = results.get(
                    symbol
                )

                if not isinstance(
                    packet,
                    dict,
                ):
                    packet = {
                        "available": False,
                        "symbol": symbol,
                        "source_exchange": "lbank",
                        "reason": (
                            "missing shadow observation result"
                        ),
                    }

                available = (
                    packet.get(
                        "available"
                    )
                    is True
                )

                if available:
                    observed += 1

                    next_check_at = (
                        time.time()
                        + self.success_recheck_seconds
                    )

                else:
                    unavailable += 1

                    next_check_at = (
                        time.time()
                        + self.failure_recheck_seconds
                    )

                persisted = (
                    self.store
                    .record_observation(
                        symbol,
                        packet,
                        next_check_at=(
                            next_check_at
                        ),
                    )
                )

                if not persisted:
                    logger.error(
                        "Shadow execution persistence failed for %s",
                        symbol,
                    )

                self.last_progress_at = (
                    time.time()
                )

            self.total_attempted += (
                attempted
            )

            self.total_observed += (
                observed
            )

            self.total_unavailable += (
                unavailable
            )

            self.last_completed_at = (
                time.time()
            )

            return {
                "attempted": attempted,
                "observed": observed,
                "unavailable": unavailable,
                "symbols": symbols,
            }

    async def run_forever(
        self,
        *,
        interval_seconds: float = 60.0,
    ):
        """
        Continuously run bounded shadow batches.

        This method remains unused by production until explicitly wired later.
        """
        interval = max(
            10.0,
            float(
                interval_seconds
            ),
        )

        self._running = True

        while self._running:
            try:
                await self.run_once()

            except asyncio.CancelledError:
                break

            except Exception as exc:
                logger.exception(
                    "LBank execution shadow loop failed: %s",
                    exc,
                )

            try:
                await asyncio.sleep(
                    interval
                )

            except asyncio.CancelledError:
                break

        self._running = False

    def stop(
        self,
    ):
        self._running = False

    async def close(
        self,
    ):
        self.stop()

        try:
            await self.observer.close()

        except Exception as exc:
            logger.warning(
                "LBank execution observer close failed: %s",
                exc,
            )

    def health_snapshot(
        self,
    ) -> dict[str, Any]:
        return {
            "running": bool(
                self._running
            ),
            "batch_size": (
                self.batch_size
            ),
            "last_started_at": (
                self.last_started_at
            ),
            "last_progress_at": (
                self.last_progress_at
            ),
            "last_completed_at": (
                self.last_completed_at
            ),
            "total_attempted": (
                self.total_attempted
            ),
            "total_observed": (
                self.total_observed
            ),
            "total_unavailable": (
                self.total_unavailable
            ),
        }
