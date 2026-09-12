import asyncio
import sqlite3
import time

from schema_test_support import migrate_test_database
from waterfallhunter.core.db import (
    DBAdapter,
)
from waterfallhunter.discovery.lbank_scanner import (
    FORCED_SCAN_SYMBOLS,
    LBankCatalogScanner,
    is_meme_symbol,
)


def _db(db_path):
    migrate_test_database(db_path)
    return DBAdapter(str(db_path))


def test_meme_classifier_covers_the_explicit_high_beta_basket_without_cati_false_positive():
    assert (
        is_meme_symbol(
            "1000BONK/USDT:USDT"
        )
        is True
    )

    assert (
        is_meme_symbol(
            "FARTCOIN/USDT:USDT"
        )
        is True
    )

    assert (
        is_meme_symbol(
            "PENGU/USDT:USDT"
        )
        is True
    )

    assert (
        is_meme_symbol(
            "1000CAT/USDT:USDT"
        )
        is True
    )

    assert (
        is_meme_symbol(
            "KOMA/USDT:USDT"
        )
        is True
    )

    assert (
        is_meme_symbol(
            "BANANAS31/USDT:USDT"
        )
        is True
    )

    assert (
        is_meme_symbol(
            "1000WOJAK/USDT:USDT"
        )
        is True
    )

    assert (
        is_meme_symbol(
            "CATI/USDT:USDT"
        )
        is False
    )


def test_scanner_keeps_volume_floor_as_transitional_scan_filter_only():
    scanner = LBankCatalogScanner(
        db_adapter=None,
        min_volume_usdt=2_000_000,
    )

    assert (
        scanner.min_volume_usdt
        == 2_000_000
    )

    assert (
        scanner._temporary_scan_eligibility(
            "MEME/USDT:USDT",
            0.1,
            1_999_999,
        )
        is False
    )

    assert (
        scanner._temporary_scan_eligibility(
            "MEME/USDT:USDT",
            0.1,
            2_000_000,
        )
        is True
    )


def test_btc_and_eth_are_forced_into_scan_universe():
    scanner = LBankCatalogScanner(
        db_adapter=None,
    )

    for symbol in FORCED_SCAN_SYMBOLS:
        assert (
            scanner._temporary_scan_eligibility(
                symbol,
                50_000.0,
                0.0,
            )
            is True
        )


def test_reference_price_requires_a_fresh_observation():
    scanner = LBankCatalogScanner(
        db_adapter=None
    )

    scanner.active_candidates[
        "TEST/USDT:USDT"
    ] = {
        "last_price": 0.1,
        "reference_observed_at": (
            time.time()
        ),
    }

    assert (
        scanner.get_live_reference(
            "TEST/USDT:USDT"
        )[0]
        == 0.1
    )

    scanner.active_candidates[
        "TEST/USDT:USDT"
    ][
        "reference_observed_at"
    ] = (
        time.time()
        - 91
    )

    assert (
        scanner.get_live_reference(
            "TEST/USDT:USDT"
        )
        == (
            None,
            None,
        )
    )


def test_db_keeps_catalogue_membership_separate_from_scan_eligibility(
    tmp_path,
):
    db_path = (
        tmp_path
        / "catalog.db"
    )

    db = _db(db_path)

    db.update_candidates(
        {
            "LOWVOL/USDT:USDT": {
                "last_price": 0.1,
                "quote_volume": 100_000,
                "is_meme": True,
                "scan_eligible": False,
            },
            "ACTIVE/USDT:USDT": {
                "last_price": 0.1,
                "quote_volume": 5_000_000,
                "is_meme": True,
                "scan_eligible": True,
            },
        }
    )

    assert db.get_catalog_symbols() == {
        "LOWVOL/USDT:USDT",
        "ACTIVE/USDT:USDT",
    }

    assert db.get_tracked_symbols() == {
        "ACTIVE/USDT:USDT",
    }

    assert set(
        db.get_all_active_candidates()
    ) == {
        "ACTIVE/USDT:USDT",
    }


def test_missing_contract_is_removed_only_after_two_successful_missing_snapshots(
    tmp_path,
):
    db_path = (
        tmp_path
        / "catalog.db"
    )

    db = _db(db_path)

    symbol = (
        "TEST/USDT:USDT"
    )

    db.update_candidates(
        {
            symbol: {
                "last_price": 0.1,
                "quote_volume": 5_000_000,
                "is_meme": False,
                "scan_eligible": True,
            }
        }
    )

    first_removed = (
        db.record_missing_symbols(
            {symbol},
            removal_after=2,
        )
    )

    assert (
        first_removed
        == set()
    )

    assert (
        symbol
        in db.get_catalog_symbols()
    )

    second_removed = (
        db.record_missing_symbols(
            {symbol},
            removal_after=2,
        )
    )

    assert (
        second_removed
        == {symbol}
    )

    assert (
        symbol
        not in db.get_catalog_symbols()
    )


def test_seen_contract_resets_missing_snapshot_counter(
    tmp_path,
):
    db_path = (
        tmp_path
        / "catalog.db"
    )

    db = _db(db_path)

    symbol = (
        "TEST/USDT:USDT"
    )

    packet = {
        symbol: {
            "last_price": 0.1,
            "quote_volume": 5_000_000,
            "is_meme": False,
            "scan_eligible": True,
        }
    }

    db.update_candidates(
        packet
    )

    assert (
        db.record_missing_symbols(
            {symbol},
            removal_after=2,
        )
        == set()
    )

    db.update_candidates(
        packet
    )

    assert (
        db.record_missing_symbols(
            {symbol},
            removal_after=2,
        )
        == set()
    )

    assert (
        symbol
        in db.get_catalog_symbols()
    )


def test_scan_eligibility_change_resets_stale_candidate_state(
    tmp_path,
):
    db_path = (
        tmp_path
        / "catalog.db"
    )

    db = _db(db_path)

    symbol = (
        "TEST/USDT:USDT"
    )

    db.update_candidates(
        {
            symbol: {
                "last_price": 0.1,
                "quote_volume": 5_000_000,
                "is_meme": False,
                "scan_eligible": True,
            }
        }
    )

    assert (
        db.update_candidate_state(
            symbol,
            "PRE-TRIGGER",
        )
        is True
    )

    db.update_candidates(
        {
            symbol: {
                "last_price": 0.1,
                "quote_volume": 100_000,
                "is_meme": False,
                "scan_eligible": False,
            }
        }
    )

    with sqlite3.connect(
        str(db_path)
    ) as conn:
        row = conn.execute(
            """
            SELECT
                status,
                scan_eligible
            FROM lbank_catalog
            WHERE symbol = ?
            """,
            (symbol,),
        ).fetchone()

    assert row == (
        "WATCH",
        0,
    )


def test_background_catalogue_default_interval_is_six_hours():
    scanner = LBankCatalogScanner(
        db_adapter=None
    )

    defaults = (
        scanner
        .start_background_scanner
        .__defaults__
    )

    assert defaults == (
        21_600,
    )


def test_catalog_refresh_reports_candidates_removed_from_active_universe(monkeypatch):
    scanner = LBankCatalogScanner(db_adapter=None)
    symbol = "DROP/USDT:USDT"
    scanner.active_candidates = {symbol: {"symbol": symbol, "scan_eligible": True, "metrics": {"exchange": "binance", "mapped_symbol": symbol}}}
    removed = []
    scanner.on_candidates_removed = lambda packet: removed.append(packet)

    async def fetch():
        return [{"symbol": symbol, "last_price": 0.1, "quote_volume": 100_000.0, "is_meme": False, "scan_eligible": False}]

    monkeypatch.setattr(scanner, "fetch_lbank_futures_symbols", fetch)
    monkeypatch.setattr(scanner, "_enrich_dex_context", lambda _symbols: asyncio.sleep(0))
    asyncio.run(scanner.update_catalog())

    assert symbol not in scanner.active_candidates
    assert len(removed) == 1
    assert removed[0][symbol]["metrics"]["exchange"] == "binance"


def test_catalog_refresh_projects_canonical_lifecycle_into_active_candidate(tmp_path, monkeypatch):
    db = _db(tmp_path / "catalog-lifecycle.db")
    scanner = LBankCatalogScanner(db_adapter=db)
    symbol = "LIFECYCLE/USDT:USDT"

    async def fetch():
        return [{
            "symbol": symbol,
            "last_price": 0.1,
            "quote_volume": 3_000_000.0,
            "is_meme": False,
            "contract_size": 1.0,
            "scan_eligible": True,
        }]

    monkeypatch.setattr(scanner, "fetch_lbank_futures_symbols", fetch)
    monkeypatch.setattr(scanner, "_enrich_dex_context", lambda _symbols: asyncio.sleep(0))
    asyncio.run(scanner.update_catalog())

    active = scanner.active_candidates[symbol]
    assert active["lifecycle_id"] == 1
    assert active["status"] == "WATCH"

    assert db.update_candidate_state(symbol, "PRE-TRIGGER") is True
    asyncio.run(scanner.update_catalog())
    active = scanner.active_candidates[symbol]
    assert active["lifecycle_id"] == 1
    assert active["status"] == "PRE-TRIGGER"
