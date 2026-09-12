import asyncio
import sqlite3

from schema_test_support import migrate_test_database
from waterfallhunter.core.db import (
    DBAdapter,
)
from waterfallhunter.core.lbank_execution_shadow import (
    LBankExecutionShadowWorker,
)
from waterfallhunter.core.lbank_execution_store import (
    LBankExecutionStore,
)


def seed_catalog(
    db_path,
):
    migrate_test_database(db_path)
    db = DBAdapter(
        str(db_path)
    )

    db.update_candidates(
        {
            "A/USDT:USDT": {
                "last_price": 0.1,
                "quote_volume": 100_000,
                "is_meme": True,
                "scan_eligible": False,
            },
            "B/USDT:USDT": {
                "last_price": 0.2,
                "quote_volume": 50_000,
                "is_meme": False,
                "scan_eligible": False,
            },
            "C/USDT:USDT": {
                "last_price": 0.3,
                "quote_volume": 5_000_000,
                "is_meme": False,
                "scan_eligible": True,
            },
            "TOOEXPENSIVE/USDT:USDT": {
                "last_price": 2.0,
                "quote_volume": 10_000_000,
                "is_meme": True,
                "scan_eligible": False,
            },
        }
    )

    return db


def success_packet(
    symbol,
):
    return {
        "available": True,
        "symbol": symbol,
        "source_exchange": "lbank",
        "observed_at": 1000.0,
        "spread_pct": 0.05,
        "depth": {
            "bounded": {
                "10": {
                    "minimum_side_depth_usdt": 100.0,
                },
                "25": {
                    "minimum_side_depth_usdt": 200.0,
                },
                "50": {
                    "minimum_side_depth_usdt": 400.0,
                },
                "100": {
                    "minimum_side_depth_usdt": 800.0,
                },
            }
        },
        "execution": {
            "25": {
                "effective_crossing_cost_pct": 0.05,
            },
            "50": {
                "effective_crossing_cost_pct": 0.06,
            },
            "100": {
                "effective_crossing_cost_pct": 0.08,
            },
        },
    }


class FakeObserver:
    def __init__(
        self,
        *,
        unavailable_symbols=None,
    ):
        self.unavailable_symbols = set(
            unavailable_symbols
            or set()
        )

        self.observe_many_calls = []
        self.close_count = 0

    async def observe_many(
        self,
        symbols,
    ):
        symbols = list(
            symbols
        )

        self.observe_many_calls.append(
            symbols
        )

        result = {}

        for symbol in symbols:
            if (
                symbol
                in self.unavailable_symbols
            ):
                result[
                    symbol
                ] = {
                    "available": False,
                    "symbol": symbol,
                    "source_exchange": "lbank",
                    "reason": "test unavailable",
                }

            else:
                result[
                    symbol
                ] = success_packet(
                    symbol
                )

        return result

    async def close(
        self,
    ):
        self.close_count += 1


def test_shadow_worker_processes_only_bounded_batch(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    observer = FakeObserver()

    worker = (
        LBankExecutionShadowWorker(
            store,
            observer,
            batch_size=2,
        )
    )

    result = asyncio.run(
        worker.run_once(
            now=1000.0,
        )
    )

    assert (
        result["attempted"]
        == 2
    )

    assert (
        len(
            observer
            .observe_many_calls[0]
        )
        == 2
    )

    assert (
        "TOOEXPENSIVE/USDT:USDT"
        not in result["symbols"]
    )


def test_shadow_worker_persists_success_without_changing_scan_eligibility(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    observer = FakeObserver()

    worker = (
        LBankExecutionShadowWorker(
            store,
            observer,
            batch_size=1,
        )
    )

    result = asyncio.run(
        worker.run_once(
            now=1000.0,
        )
    )

    symbol = result[
        "symbols"
    ][0]

    observation = (
        store.get_observation(
            symbol
        )
    )

    assert (
        observation is not None
    )

    assert (
        observation[
            "observation_status"
        ]
        == "OBSERVED"
    )

    with sqlite3.connect(
        str(db_path)
    ) as conn:
        row = conn.execute(
            """
            SELECT
                scan_eligible,
                status
            FROM lbank_catalog
            WHERE symbol = ?
            """,
            (
                symbol,
            ),
        ).fetchone()

    assert row == (
        0,
        "WATCH",
    )


def test_shadow_worker_persists_unavailable_result(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    observer = FakeObserver(
        unavailable_symbols={
            "A/USDT:USDT",
        }
    )

    worker = (
        LBankExecutionShadowWorker(
            store,
            observer,
            batch_size=1,
        )
    )

    result = asyncio.run(
        worker.run_once(
            now=1000.0,
        )
    )

    assert (
        result["attempted"]
        == 1
    )

    assert (
        result["unavailable"]
        == 1
    )

    row = store.get_observation(
        "A/USDT:USDT"
    )

    assert (
        row["observation_status"]
        == "UNAVAILABLE"
    )

    assert (
        row["failures"]
        == 1
    )


def test_shadow_worker_sets_future_recheck_time(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    observer = FakeObserver()

    worker = (
        LBankExecutionShadowWorker(
            store,
            observer,
            batch_size=1,
            success_recheck_seconds=600.0,
        )
    )

    result = asyncio.run(
        worker.run_once(
            now=1000.0,
        )
    )

    symbol = result[
        "symbols"
    ][0]

    observation = (
        store.get_observation(
            symbol
        )
    )

    assert (
        observation[
            "next_check_at"
        ]
        > 1000.0
    )


def test_shadow_worker_does_not_reprocess_symbol_before_next_check(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    observer = FakeObserver()

    worker = (
        LBankExecutionShadowWorker(
            store,
            observer,
            batch_size=1,
            success_recheck_seconds=600.0,
        )
    )

    first = asyncio.run(
        worker.run_once(
            now=1000.0,
        )
    )

    first_symbol = first[
        "symbols"
    ][0]

    second = asyncio.run(
        worker.run_once(
            now=1001.0,
        )
    )

    assert (
        first_symbol
        not in second["symbols"]
    )


def test_shadow_worker_updates_operational_counters(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    observer = FakeObserver(
        unavailable_symbols={
            "B/USDT:USDT",
        }
    )

    worker = (
        LBankExecutionShadowWorker(
            store,
            observer,
            batch_size=3,
        )
    )

    result = asyncio.run(
        worker.run_once(
            now=1000.0,
        )
    )

    assert (
        result["attempted"]
        == 3
    )

    assert (
        worker.total_attempted
        == 3
    )

    assert (
        worker.total_observed
        == 2
    )

    assert (
        worker.total_unavailable
        == 1
    )

    health = (
        worker.health_snapshot()
    )

    assert (
        health[
            "total_attempted"
        ]
        == 3
    )

    assert (
        health[
            "total_observed"
        ]
        == 2
    )

    assert (
        health[
            "total_unavailable"
        ]
        == 1
    )


def test_shadow_worker_handles_missing_result_as_unavailable(
    tmp_path,
):
    class MissingResultObserver:
        async def observe_many(
            self,
            symbols,
        ):
            return {}

        async def close(
            self,
        ):
            return None

    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    worker = (
        LBankExecutionShadowWorker(
            store,
            MissingResultObserver(),
            batch_size=1,
        )
    )

    result = asyncio.run(
        worker.run_once(
            now=1000.0,
        )
    )

    symbol = result[
        "symbols"
    ][0]

    row = store.get_observation(
        symbol
    )

    assert (
        row[
            "observation_status"
        ]
        == "UNAVAILABLE"
    )

    assert (
        "missing shadow observation result"
        in row["reason"]
    )


def test_shadow_worker_close_closes_observer(
    tmp_path,
):
    db_path = (
        tmp_path
        / "registry.db"
    )

    seed_catalog(
        db_path
    )

    store = LBankExecutionStore(
        str(db_path)
    )

    observer = FakeObserver()

    worker = (
        LBankExecutionShadowWorker(
            store,
            observer,
        )
    )

    asyncio.run(
        worker.close()
    )

    assert (
        observer.close_count
        == 1
    )
