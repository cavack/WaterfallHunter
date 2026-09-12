import logging
import math
import asyncio
import time
import ccxt.async_support as ccxt
from typing import AsyncIterator, Callable, Dict, Any, Optional

logger = logging.getLogger("WaterfallHunter.MultiExchange")


def is_usdt_linear_perpetual(market: Dict[str, Any] | None) -> bool:
    """Return true only for an active USDT-settled linear perpetual contract."""
    return bool(
        market
        and market.get("active")
        and market.get("linear")
        and market.get("swap") is True
        and market.get("settle") == "USDT"
    )


class MultiExchangeGateway:
    derivatives_request_timeout_seconds = 10.0

    ccxt_exchange_aliases = {
        "gateio": "gate",
    }

    def __init__(self):
        # قانون سخت: ترتیب اولویت دقیقاً طبق معماری Production
        self.priority_chain = [
            "binance",
            "bybit",
            "kucoin",
            "okx",
            "mexc",
            "bingx",
            "gateio",
            "bitget",
            "htx",
        ]
        self._exchanges: Dict[str, ccxt.Exchange] = {}
        self._markets_loaded: Dict[str, bool] = {
            ex: False
            for ex in self.priority_chain
        }

    @classmethod
    def _ccxt_exchange_id(cls, ex_name: str) -> str:
        """
        Map WaterfallHunter's internal exchange name to the installed CCXT id.

        The internal identifier and provenance remain unchanged.
        """
        return cls.ccxt_exchange_aliases.get(
            ex_name,
            ex_name,
        )

    @staticmethod
    def _exchange_options(ex_name: str) -> dict:
        options = {
            "defaultType": "swap",
        }
        if ex_name in {
            "binance",
            "bybit",
        }:
            options["fetchMarkets"] = {
                "types": [
                    "linear",
                ]
            }
        return options

    async def _get_exchange(self, ex_name: str) -> ccxt.Exchange:
        """مقداردهی اولیه و کش کردن نمونه صرافی به صورت Singleton"""
        if ex_name not in self._exchanges:
            ccxt_id = self._ccxt_exchange_id(
                ex_name
            )

            ex_class = getattr(
                ccxt,
                ccxt_id,
            )

            self._exchanges[ex_name] = ex_class(
                {
                    "enableRateLimit": True,
                    "timeout": 10000,
                    "options": self._exchange_options(
                        ex_name
                    ),
                }
            )

        ex = self._exchanges[
            ex_name
        ]

        if ex_name not in self._markets_loaded:
            self._markets_loaded[
                ex_name
            ] = False

        if not self._markets_loaded[
            ex_name
        ]:
            try:
                await ex.load_markets()
                self._markets_loaded[
                    ex_name
                ] = True
            except Exception as e:
                logger.debug(
                    f"Failed to load markets for {ex_name}: {e}"
                )

        return ex

    def _map_symbol(
        self,
        base_symbol: str,
        exchange_markets: dict,
    ) -> Optional[str]:
        """سیستم مپینگ هوشمند: حل مشکل پیشوندهای 1000 و 1000000 در صرافی‌های مختلف"""
        base_coin = (
            base_symbol
            .split("/")[0]
            .split("-")[0]
            .upper()
        )

        clean_base = (
            base_coin
            .replace(
                "1000000",
                "",
            )
            .replace(
                "1000",
                "",
            )
        )

        # ترکیب‌های احتمالی در صرافی‌های مختلف برای فیوچرز
        possible_symbols = [
            f"{base_coin}/USDT:USDT",
            f"{base_coin}/USDT",
            f"1000{clean_base}/USDT:USDT",
            f"1000000{clean_base}/USDT:USDT",
            f"100{clean_base}/USDT:USDT",
            base_symbol,
        ]

        # جستجوی سریع مستقیم
        for variant in possible_symbols:
            if variant in exchange_markets:
                market = exchange_markets[
                    variant
                ]
                if is_usdt_linear_perpetual(
                    market
                ):
                    return variant

        # جستجوی عمیق در صورتی که نام‌گذاری نامتعارف باشد
        for k, v in exchange_markets.items():
            if (
                k.startswith(
                    f"{base_coin}/"
                )
                or k.startswith(
                    f"1000{clean_base}/"
                )
                or k.startswith(
                    f"1000000{clean_base}/"
                )
            ):
                if is_usdt_linear_perpetual(
                    v
                ):
                    return k

        return None

    async def execute_waterfall(
        self,
        method_name: str,
        symbol: str,
        *args,
        result_validator=None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        قانون سخت (Strict Rule):
        هر داده‌ای که از اولین منبع موفق تأمین شد، دیگر از بقیه صرافی‌ها درخواست نمی‌شود.
        """
        for ex_name in self.priority_chain:
            try:
                ex = await self._get_exchange(
                    ex_name
                )

                if not self._markets_loaded[
                    ex_name
                ]:
                    continue

                mapped_sym = self._map_symbol(
                    symbol,
                    ex.markets,
                )

                if not mapped_sym:
                    continue

                method = getattr(
                    ex,
                    method_name,
                )

                result = await method(
                    mapped_sym,
                    *args,
                    **kwargs,
                )

                if (
                    result
                    and (
                        result_validator is None
                        or result_validator(
                            result
                        )
                    )
                ):
                    logger.debug(
                        f"[SUCCESS] {method_name} for {symbol} "
                        f"sourced from {ex_name.upper()} "
                        f"(Mapped: {mapped_sym})"
                    )

                    return {
                        "exchange": ex_name,
                        "mapped_symbol": mapped_sym,
                        "data": result,
                        "exchange_instance": ex,
                    }

            except Exception as e:
                logger.debug(
                    f"[WATERFALL SKIP] {ex_name} failed for "
                    f"{symbol} on {method_name}: {e}"
                )
                continue

        return {}

    def _compatible_cached_ticker(
        self,
        getter: Callable[[str, str], Any] | None,
        ex_name: str,
        mapped_symbol: str,
        reference_price: float,
        max_deviation_pct: float,
    ) -> Dict[str, Any] | None:
        if getter is None:
            return None
        try:
            cached = getter(ex_name, mapped_symbol)
        except Exception:
            return None
        if not isinstance(cached, dict):
            return None
        return (
            cached
            if self._price_is_compatible(
                cached, reference_price, max_deviation_pct
            )
            else None
        )

    async def _compatible_market_source(
        self,
        ex_name: str,
        symbol: str,
        reference_price: float,
        max_deviation_pct: float,
        realtime_ticker_getter: Callable[[str, str], Any] | None,
    ) -> Dict[str, Any] | None:
        exchange = await self._get_exchange(ex_name)
        if not self._markets_loaded[ex_name]:
            return None
        mapped_symbol = self._map_symbol(symbol, exchange.markets)
        if not mapped_symbol:
            return None
        ticker = self._compatible_cached_ticker(
            realtime_ticker_getter,
            ex_name,
            mapped_symbol,
            reference_price,
            max_deviation_pct,
        )
        if ticker is None:
            ticker = await exchange.fetch_ticker(mapped_symbol)
        if not self._price_is_compatible(
            ticker, reference_price, max_deviation_pct
        ):
            return None
        return {
            "exchange": ex_name,
            "mapped_symbol": mapped_symbol,
            "data": ticker,
            "exchange_instance": exchange,
        }

    async def compatible_market_sources(
        self,
        symbol: str,
        reference_price: float,
        max_deviation_pct: float = 5.0,
        realtime_ticker_getter: Callable[[str, str], Any] | None = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Yield fresh, price-compatible USDT perpetual venues in priority order."""
        for ex_name in self.priority_chain:
            try:
                source = await self._compatible_market_source(
                    ex_name,
                    symbol,
                    reference_price,
                    max_deviation_pct,
                    realtime_ticker_getter,
                )
            except Exception as exc:
                logger.debug(
                    "[WATERFALL SKIP] %s failed for %s ticker: %s",
                    ex_name,
                    symbol,
                    exc,
                )
                continue
            if source is not None:
                yield source

    @staticmethod
    def _price_is_compatible(
        ticker: Dict[str, Any],
        reference_price: float,
        max_deviation_pct: float,
    ) -> bool:
        candidate_price = ticker.get(
            "last"
        )

        if (
            not isinstance(
                candidate_price,
                (int, float),
            )
            or not isinstance(
                reference_price,
                (int, float),
            )
        ):
            return False

        if (
            not math.isfinite(
                candidate_price
            )
            or not math.isfinite(
                reference_price
            )
            or candidate_price <= 0
            or reference_price <= 0
        ):
            return False

        deviation_pct = (
            abs(
                candidate_price
                - reference_price
            )
            / reference_price
            * 100.0
        )

        return (
            deviation_pct
            <= max_deviation_pct
        )

    async def fetch_ticker(
        self,
        symbol: str,
        reference_price: float | None = None,
        max_deviation_pct: float = 5.0,
    ) -> Dict[str, Any]:
        if reference_price is None:
            return await self.execute_waterfall(
                "fetch_ticker",
                symbol,
            )

        return await self.execute_waterfall(
            "fetch_ticker",
            symbol,
            result_validator=lambda ticker: (
                self._price_is_compatible(
                    ticker,
                    reference_price,
                    max_deviation_pct,
                )
            ),
        )

    async def fetch_order_book(
        self,
        symbol: str,
        limit: int = 20,
    ) -> Dict[str, Any]:
        return await self.execute_waterfall(
            "fetch_order_book",
            symbol,
            limit=limit,
        )

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 50,
    ) -> Dict[str, Any]:
        return await self.execute_waterfall(
            "fetch_ohlcv",
            symbol,
            timeframe=timeframe,
            limit=limit,
        )

    async def fetch_ohlcv_from_source(
        self,
        exchange_name: str,
        mapped_symbol: str,
        *,
        timeframe: str = "1m",
        since: int | None = None,
        limit: int = 500,
    ) -> Dict[str, Any]:
        """Fetch from the exact venue captured with a production signal.

        Outcome validation must not silently switch venues because that would
        compare signal levels with a different market. This method therefore
        has no waterfall fallback and accepts only a currently valid linear
        USDT perpetual market.
        """
        if exchange_name not in self.priority_chain:
            return {}

        try:
            exchange = await self._get_exchange(
                exchange_name
            )
            if not self._markets_loaded.get(
                exchange_name,
                False,
            ):
                return {}

            market = exchange.markets.get(
                mapped_symbol
            )
            if not is_usdt_linear_perpetual(
                market
            ):
                return {}

            rows = await exchange.fetch_ohlcv(
                mapped_symbol,
                timeframe=timeframe,
                since=since,
                limit=max(1, min(1000, int(limit))),
            )
            if not rows:
                return {}

            return {
                "exchange": exchange_name,
                "mapped_symbol": mapped_symbol,
                "data": rows,
            }
        except Exception as exc:
            logger.debug(
                "Exact-source OHLCV fetch failed for %s on %s: %s",
                mapped_symbol,
                exchange_name,
                exc,
            )
            return {}

    async def get_confirmation_exchange(
        self,
        symbol: str,
        exclude_name: str,
        reference_price: float | None = None,
        max_deviation_pct: float = 5.0,
    ):
        for name in self.priority_chain:
            if name == exclude_name:
                continue

            exchange = await self._get_exchange(
                name
            )

            if self._markets_loaded[
                name
            ]:
                mapped = self._map_symbol(
                    symbol,
                    exchange.markets,
                )

                if mapped:
                    if reference_price is not None:
                        try:
                            ticker = (
                                await exchange
                                .fetch_ticker(
                                    mapped
                                )
                            )
                        except Exception:
                            continue

                        if not self._price_is_compatible(
                            ticker,
                            reference_price,
                            max_deviation_pct,
                        ):
                            continue

                    return (
                        exchange,
                        mapped,
                    )

        return (
            None,
            None,
        )

    async def fetch_derivatives_context(
        self,
        exchange: ccxt.Exchange,
        mapped_symbol: str,
        analyzer: Any,
    ) -> Dict[str, Any]:
        """Read a complete packet from Binance USD-M through its canonical raw market ID."""
        exchange_name = str(
            getattr(
                exchange,
                "id",
                "unknown",
            )
        )

        market = getattr(
            exchange,
            "markets",
            {},
        ).get(
            mapped_symbol
        )

        market_id = (
            market.get(
                "id"
            )
            if isinstance(
                market,
                dict,
            )
            else None
        )

        retrieved_at = time.time()

        provenance = {
            "source_exchange": exchange_name,
            "mapped_symbol": mapped_symbol,
            "market_id": market_id,
            "retrieved_at": retrieved_at,
            "fallback_attempts": [],
        }

        if not is_usdt_linear_perpetual(
            market
        ):
            return {
                "available": False,
                "reason": "ineligible derivatives contract",
                **provenance,
            }

        if (
            not isinstance(
                market_id,
                str,
            )
            or not market_id
        ):
            return {
                "available": False,
                "reason": "missing canonical market id",
                **provenance,
            }

        if exchange_name != "binance":
            return {
                "available": False,
                "reason": (
                    f"unsupported derivatives source: {exchange_name}"
                ),
                **provenance,
            }

        async def request(
            method_name: str,
            params: dict[str, Any],
        ):
            method = getattr(
                exchange,
                method_name,
                None,
            )

            if not callable(
                method
            ):
                return None

            try:
                return await asyncio.wait_for(
                    method(
                        params
                    ),
                    timeout=(
                        self.derivatives_request_timeout_seconds
                    ),
                )

            except Exception as exc:
                logger.debug(
                    "Derivative %s unavailable from %s for %s: %s",
                    method_name,
                    exchange_name,
                    mapped_symbol,
                    exc,
                )
                return None

        (
            funding_rows,
            taker_rows,
            top_trader_rows,
            open_interest_rows,
        ) = await asyncio.gather(
            request(
                "fapiPublicGetFundingRate",
                {
                    "symbol": market_id,
                    "limit": 90,
                },
            ),
            request(
                "fapiDataGetTakerlongshortRatio",
                {
                    "symbol": market_id,
                    "period": "5m",
                    "limit": 13,
                },
            ),
            request(
                "fapiDataGetTopLongShortAccountRatio",
                {
                    "symbol": market_id,
                    "period": "5m",
                    "limit": 1,
                },
            ),
            request(
                "fapiDataGetOpenInterestHist",
                {
                    "symbol": market_id,
                    "period": "5m",
                    "limit": 13,
                },
            ),
        )
        # Bind freshness to the completed provider packet, not request start.
        # This keeps locally received timestamps causal without accepting a
        # genuinely future-dated exchange observation.
        retrieved_at = time.time()

        result = analyzer.evaluate_binance_rows(
            mapped_symbol=mapped_symbol,
            market_id=market_id,
            funding_rows=funding_rows,
            taker_rows=taker_rows,
            top_trader_rows=top_trader_rows,
            open_interest_rows=open_interest_rows,
            retrieved_at=retrieved_at,
        )
        result["source_capture"] = {
            "provider": "binance",
            "mapped_symbol": mapped_symbol,
            "market_id": market_id,
            "retrieved_at": retrieved_at,
            "funding_rows": funding_rows,
            "taker_rows": taker_rows,
            "top_trader_rows": top_trader_rows,
            "open_interest_rows": open_interest_rows,
        }
        return result

    async def close_all(self):
        for ex in self._exchanges.values():
            await ex.close()

        self._exchanges.clear()
        self._markets_loaded.clear()

        logger.info(
            "All Multi-Exchange connections securely closed."
        )
