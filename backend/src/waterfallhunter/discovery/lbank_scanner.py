import asyncio
import logging
import time
from typing import Any, Dict, List, Set

import ccxt.async_support as ccxt

from waterfallhunter.core.multi_exchange import (
    is_usdt_linear_perpetual,
)

logger = logging.getLogger(
    "WaterfallHunter.LBankScanner"
)

MEME_KEYWORDS = (
    "PEPE",
    "SHIB",
    "DOGE",
    "FLOKI",
    "BONK",
    "WIF",
    "BOME",
    "NEIRO",
    "PNUT",
    "GOAT",
    "MOODENG",
    "CHILLGUY",
    "MEME",
    "TURBO",
    "SLERF",
    "DOGS",
    "MOG",
    "POPCAT",
    "PENGU",
    "PUMP",
    "FARTCOIN",
    "BRETT",
    "TRUMP",
    "MELANIA",
    "ACT",
    "FWOG",
    "MEW",
    "GIGA",
    "RATS",
    "KOMA",
    "1000CAT",
)

MEME_EXACT_BASES = frozenset(
    {
        "BANANAS31",
        "TOAD",
        "ANSEM",
        "1000WOJAK",
        "MEMECOIN",
    }
)

FORCED_SCAN_SYMBOLS = frozenset(
    {
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
    }
)


def is_meme_symbol(
    symbol: str,
) -> bool:
    base = (
        symbol.upper()
        .split("/")[0]
        .split(":")[0]
    )

    return (
        base in MEME_EXACT_BASES
        or any(
            keyword in base
            for keyword in MEME_KEYWORDS
        )
    )


class LBankCatalogScanner:
    """
    LBank has two intentionally separate responsibilities here:

    1. Canonical catalogue:
       Every active USDT-settled linear perpetual is persisted.

    2. Scanner eligibility:
       A temporary pre-execution filter decides which catalogue contracts enter
       the hunter.

    The current volume floor is transitional. It will later be replaced by the
    LBank execution-suitability layer using spread, slippage, usable depth and
    market constraints.
    """

    def __init__(
        self,
        db_adapter,
        max_price: float = 1.0,
        min_volume_usdt: float = 2_000_000.0,
        dex_client=None,
        onchain_client=None,
    ):
        self.max_price = float(
            max_price
        )

        self.min_volume_usdt = float(
            min_volume_usdt
        )

        # Guards concurrent mutation of active_candidates: the six-hour
        # catalogue swap (update_catalog) versus per-evaluation writes from
        # concurrently running hunter evaluations.
        self._candidates_lock = asyncio.Lock()

        self.active_candidates: Dict[
            str,
            Dict[str, Any],
        ] = {}

        self.db = db_adapter
        self.dex_client = dex_client
        self.onchain_client = onchain_client
        self.on_candidates_removed = None

        self._is_running = False

        self.last_successful_refresh_at: (
            float | None
        ) = None

        self.reference_ttl_seconds = 90.0

        self._reference_exchange = None
        self._reference_lock = asyncio.Lock()

    def _temporary_scan_eligibility(
        self,
        symbol: str,
        last_price: float,
        quote_volume: float,
        *,
        currently_eligible: bool = False,
    ) -> bool:
        """
        Transitional universe filter.

        This is deliberately separate from catalogue membership.

        BTC/ETH remain forced because they are benchmark/reference contracts.
        The rigid volume floor will be removed once LBank execution suitability
        is available.

        Hysteresis: an already-eligible contract must fall 20% below the floor
        before losing eligibility, so volume oscillating around the threshold
        no longer flips scan_eligible (and thereby recycles lifecycle_id,
        wiping ARMED/TRIGGERED state) every refresh.
        """
        if symbol in FORCED_SCAN_SYMBOLS:
            return True

        if not (
            0.0
            < last_price
            <= self.max_price
        ):
            return False

        if currently_eligible:
            return quote_volume >= self.min_volume_usdt * 0.8

        return quote_volume >= self.min_volume_usdt

    async def fetch_lbank_futures_symbols(
        self,
    ) -> List[Dict[str, Any]]:
        """
        Fetch one complete successful LBank USDT-linear perpetual catalogue.

        No price or volume filter is allowed to remove contracts from the
        canonical catalogue snapshot.
        """
        catalogue: List[
            Dict[str, Any]
        ] = []

        exchange = None

        try:
            exchange = ccxt.lbank(
                {
                    "enableRateLimit": True,
                    "timeout": 15_000,
                    "options": {
                        "defaultType": "swap",
                    },
                }
            )

            await exchange.load_markets()

            tickers = await exchange.fetch_tickers()

            for (
                symbol,
                market,
            ) in exchange.markets.items():
                if not is_usdt_linear_perpetual(
                    market
                ):
                    continue

                ticker = (
                    tickers.get(symbol)
                    or {}
                )

                last_price = float(
                    ticker.get("last")
                    or 0.0
                )

                quote_volume = float(
                    ticker.get(
                        "quoteVolume"
                    )
                    or 0.0
                )

                contract_size = float(
                    market.get(
                        "contractSize"
                    )
                    or 1.0
                )

                scan_eligible = (
                    self._temporary_scan_eligibility(
                        symbol,
                        last_price,
                        quote_volume,
                        currently_eligible=bool(
                            (self.active_candidates.get(symbol) or {}).get("scan_eligible")
                        ),
                    )
                )

                catalogue.append(
                    {
                        "symbol": symbol,
                        "last_price": (
                            last_price
                        ),
                        "quote_volume": (
                            quote_volume
                        ),
                        "is_meme": (
                            is_meme_symbol(
                                symbol
                            )
                        ),
                        "contract_size": (
                            contract_size
                        ),
                        "scan_eligible": (
                            scan_eligible
                        ),
                    }
                )

            catalogue.sort(
                key=lambda item: (
                    not item["scan_eligible"],
                    not item["is_meme"],
                    -item["quote_volume"],
                    item["symbol"],
                )
            )

            return catalogue

        except Exception as exc:
            logger.error(
                "LBank catalog fetch failed: %s",
                exc,
            )
            return []

        finally:
            if exchange:
                await exchange.close()

    async def refresh_live_references(
        self,
    ) -> bool:
        """
        Refresh LBank ticker references independently of the six-hour catalogue.

        Catalogue membership may be hours old. A price used by the live hunter
        may not.
        """
        async with self._reference_lock:
            try:
                if (
                    self._reference_exchange
                    is None
                ):
                    self._reference_exchange = (
                        ccxt.lbank(
                            {
                                "enableRateLimit": True,
                                "timeout": 15_000,
                                "options": {
                                    "defaultType": (
                                        "swap"
                                    )
                                },
                            }
                        )
                    )

                    await (
                        self._reference_exchange
                        .load_markets()
                    )

                tickers = await (
                    self._reference_exchange
                    .fetch_tickers()
                )

                observed_at = (
                    time.time()
                )

                for (
                    symbol,
                    candidate,
                ) in self.active_candidates.items():
                    ticker = (
                        tickers.get(symbol)
                        or {}
                    )

                    price = ticker.get(
                        "last"
                    )

                    if (
                        isinstance(
                            price,
                            (int, float),
                        )
                        and price > 0
                    ):
                        candidate[
                            "last_price"
                        ] = float(price)

                        volume = ticker.get(
                            "quoteVolume"
                        )

                        if (
                            isinstance(
                                volume,
                                (int, float),
                            )
                            and volume >= 0
                        ):
                            candidate[
                                "quote_volume"
                            ] = float(
                                volume
                            )

                        candidate[
                            "reference_observed_at"
                        ] = observed_at

                        candidate[
                            "reference_source"
                        ] = "lbank"

                    else:
                        candidate.pop(
                            "reference_observed_at",
                            None,
                        )

                return True

            except Exception as exc:
                logger.warning(
                    "LBank live reference refresh failed: %s",
                    exc,
                )
                return False

    def get_live_reference(
        self,
        symbol: str,
    ) -> tuple[
        float | None,
        float | None,
    ]:
        candidate = (
            self.active_candidates.get(
                symbol
            )
            or {}
        )

        observed_at = candidate.get(
            "reference_observed_at"
        )

        price = candidate.get(
            "last_price"
        )

        if (
            not isinstance(
                observed_at,
                (int, float),
            )
            or (
                time.time()
                - observed_at
                > self.reference_ttl_seconds
            )
        ):
            return None, None

        if (
            not isinstance(
                price,
                (int, float),
            )
            or price <= 0
        ):
            return None, None

        return (
            float(price),
            float(observed_at),
        )

    async def update_catalog(
        self,
    ):
        """
        Apply one complete successful six-hour LBank catalogue snapshot.

        A failed/empty fetch does not mutate membership or missing counters.

        A symbol is removed only after two consecutive successful catalogue
        snapshots in which it is absent.
        """
        new_symbols_raw = (
            await self
            .fetch_lbank_futures_symbols()
        )

        if not new_symbols_raw:
            return

        new_symbols_map = {
            item["symbol"]: item
            for item in new_symbols_raw
        }

        fetched_symbols = set(
            new_symbols_map.keys()
        )

        if self.db:
            current_catalog_symbols = (
                self.db
                .get_catalog_symbols()
            )
        else:
            current_catalog_symbols = set(
                self.active_candidates.keys()
            )

        missing_symbols = (
            current_catalog_symbols
            - fetched_symbols
        )

        canonical_active: Dict[str, Any] | None = None

        if self.db:
            self.db.update_candidates(
                new_symbols_map
            )

            removed_now = (
                self.db
                .record_missing_symbols(
                    missing_symbols,
                    removal_after=2,
                )
            )
            canonical_active = (
                self.db
                .get_all_active_candidates()
            )
        else:
            removed_now = (
                missing_symbols
            )

        previous_active = (
            self.active_candidates
        )

        next_active: Dict[
            str,
            Dict[str, Any],
        ] = {}

        for (
            symbol,
            catalog_data,
        ) in new_symbols_map.items():
            if not catalog_data.get(
                "scan_eligible"
            ):
                continue

            current_data = dict(
                previous_active.get(
                    symbol,
                    {},
                )
            )

            current_data.update(
                catalog_data
            )

            if canonical_active is not None:
                canonical_data = canonical_active.get(symbol)
                if canonical_data is None:
                    logger.warning(
                        "Skipping %s because canonical active lifecycle is unavailable",
                        symbol,
                    )
                    continue
                current_data["lifecycle_id"] = int(
                    canonical_data.get("lifecycle_id")
                    or 1
                )
                current_data["status"] = str(
                    canonical_data.get("status")
                    or "WATCH"
                )
                current_data["scan_eligible"] = bool(
                    canonical_data.get("scan_eligible")
                )

            next_active[
                symbol
            ] = current_data

        for symbol in removed_now:
            next_active.pop(
                symbol,
                None,
            )

        removed_from_active = {
            symbol: {
                **dict(previous_active[symbol]),
                "metrics": dict(previous_active[symbol].get("metrics") or {}),
            }
            for symbol in set(previous_active) - set(next_active)
        }

        # Swap atomically under the candidates lock so in-flight evaluations
        # either finish against the old dict (their writes are copied into
        # next_active via the per-symbol merge above) or start on the new one.
        async with self._candidates_lock:
            self.active_candidates = (
                next_active
            )

        if removed_from_active and callable(self.on_candidates_removed):
            try:
                self.on_candidates_removed(removed_from_active)
            except Exception as exc:
                logger.warning("Active-candidate removal callback failed: %s", exc)

        await self._enrich_dex_context(
            set(
                self.active_candidates.keys()
            )
        )

        self.last_successful_refresh_at = (
            time.time()
        )

    async def _enrich_dex_context(
        self,
        symbols: Set[str],
    ):
        if not self.dex_client:
            return

        async def enrich(
            symbol: str,
        ):
            context = (
                await self.dex_client
                .fetch_context(
                    symbol.split("/")[0]
                )
            )

            if context is not None:
                self.active_candidates[
                    symbol
                ][
                    "dex_context"
                ] = context

                if self.onchain_client:
                    onchain_context = (
                        await self
                        .onchain_client
                        .fetch_context(
                            context
                        )
                    )

                    if (
                        onchain_context
                        is not None
                    ):
                        self.active_candidates[
                            symbol
                        ][
                            "onchain_context"
                        ] = onchain_context

                    else:
                        self.active_candidates[
                            symbol
                        ].pop(
                            "onchain_context",
                            None,
                        )

            else:
                self.active_candidates[
                    symbol
                ].pop(
                    "dex_context",
                    None,
                )

                self.active_candidates[
                    symbol
                ].pop(
                    "onchain_context",
                    None,
                )

        semaphore = asyncio.Semaphore(
            8
        )

        async def bounded(
            symbol: str,
        ):
            async with semaphore:
                await enrich(
                    symbol
                )

        results = await asyncio.gather(
            *(
                bounded(symbol)
                for symbol in symbols
            ),
            return_exceptions=True,
        )

        for result in results:
            if isinstance(
                result,
                Exception,
            ):
                logger.warning(
                    "DEX enrichment failed: %s",
                    result,
                )

    async def start_background_scanner(
        self,
        interval_seconds: int = 21_600,
    ):
        """
        Canonical LBank catalogue refresh.

        Default: exactly six hours.
        """
        self._is_running = True

        while self._is_running:
            try:
                await self.update_catalog()

            except Exception as exc:
                logger.exception(
                    "LBank background refresh failed: %s",
                    exc,
                )

            await asyncio.sleep(
                interval_seconds
            )

    def stop(
        self,
    ):
        self._is_running = False

    async def close(
        self,
    ):
        if (
            self._reference_exchange
            is not None
        ):
            await (
                self._reference_exchange
                .close()
            )

            self._reference_exchange = None
