import asyncio
import math
import time
from typing import Any, Iterable

import ccxt.async_support as ccxt

from waterfallhunter.core.multi_exchange import (
    is_usdt_linear_perpetual,
)


class LBankExecutionObserver:
    """
    Read-only LBank execution measurement.

    This component intentionally does NOT:
    - place orders
    - cancel orders
    - change scanner state
    - change trade eligibility
    - change scores
    - apply spread/slippage thresholds

    It only measures current public execution conditions.

    LBank can expose extreme synthetic/far-away order-book levels.
    Therefore:
    - raw total book depth is diagnostic only
    - actionable depth is measured inside bounded distance bands
      anchored independently to best bid and best ask
    """

    DEFAULT_NOTIONALS = (
        25.0,
        50.0,
        100.0,
    )

    DEFAULT_DEPTH_BANDS_BPS = (
        10,
        25,
        50,
        100,
    )

    def __init__(
        self,
        notionals: Iterable[float] | None = None,
        orderbook_limit: int = 50,
        depth_bands_bps: Iterable[int] | None = None,
    ):
        source_notionals = (
            notionals
            if notionals is not None
            else self.DEFAULT_NOTIONALS
        )

        cleaned_notionals = []

        for value in source_notionals:
            number = self._finite(value)

            if (
                number is not None
                and number > 0
            ):
                cleaned_notionals.append(number)

        self.notionals = tuple(
            sorted(
                set(
                    cleaned_notionals
                )
            )
        )

        source_bands = (
            depth_bands_bps
            if depth_bands_bps is not None
            else self.DEFAULT_DEPTH_BANDS_BPS
        )

        cleaned_bands = []

        for value in source_bands:
            number = self._finite(value)

            if (
                number is not None
                and number > 0
            ):
                cleaned_bands.append(
                    int(number)
                )

        self.depth_bands_bps = tuple(
            sorted(
                set(
                    cleaned_bands
                )
            )
        )

        self.orderbook_limit = max(
            20,
            int(orderbook_limit),
        )

        self._exchange = None
        self._markets_loaded = False
        self._exchange_lock = asyncio.Lock()

    @staticmethod
    def _finite(
        value: Any,
    ) -> float | None:
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except ValueError:
                return None
        if (
            isinstance(
                value,
                (int, float),
            )
            and not isinstance(
                value,
                bool,
            )
            and math.isfinite(value)
        ):
            return float(value)

        return None

    @classmethod
    def _safe_positive(
        cls,
        value: Any,
    ) -> float | None:
        number = cls._finite(value)

        if (
            number is None
            or number <= 0
        ):
            return None

        return number

    @classmethod
    def _mark_price(cls, market: dict, ticker: dict | None = None) -> float | None:
        ticker = ticker if isinstance(ticker, dict) else {}
        ticker_info = ticker.get("info") if isinstance(ticker.get("info"), dict) else {}
        market_info = market.get("info") if isinstance(market.get("info"), dict) else {}
        for value in (
            ticker.get("mark"),
            ticker.get("markPrice"),
            ticker.get("markedPrice"),
            ticker_info.get("markPrice"),
            ticker_info.get("markedPrice"),
            ticker_info.get("mark_price"),
            market_info.get("markPrice"),
            market_info.get("markedPrice"),
            market_info.get("mark_price"),
        ):
            mark_price = cls._safe_positive(value)
            if mark_price is not None:
                return mark_price
        return None

    def _create_exchange(self):
        return ccxt.lbank(
            {
                "enableRateLimit": True,
                "timeout": 15_000,
                "options": {
                    "defaultType": "swap",
                },
            }
        )

    async def _load_swap_markets(self) -> None:
        exchange = self._exchange
        fetch_swap_markets = getattr(exchange, "fetch_swap_markets", None)
        set_markets = getattr(exchange, "set_markets", None)

        if callable(fetch_swap_markets) and callable(set_markets):
            markets = await fetch_swap_markets()
            set_markets(markets)
            return

        await exchange.load_markets()

    async def _ensure_exchange(self):
        async with self._exchange_lock:
            if self._exchange is None:
                self._exchange = (
                    self._create_exchange()
                )
                self._markets_loaded = False

            if not self._markets_loaded:
                await self._load_swap_markets()
                self._markets_loaded = True

            return self._exchange

    async def close(self):
        async with self._exchange_lock:
            exchange = self._exchange
            self._exchange = None
            self._markets_loaded = False

        if exchange is not None:
            await exchange.close()

    @classmethod
    def _market_filters(
        cls,
        market: dict,
        reference_price: float,
    ) -> dict:
        limits = (
            market.get("limits")
            if isinstance(
                market.get("limits"),
                dict,
            )
            else {}
        )

        amount_limits = (
            limits.get("amount")
            if isinstance(
                limits.get("amount"),
                dict,
            )
            else {}
        )

        cost_limits = (
            limits.get("cost")
            if isinstance(
                limits.get("cost"),
                dict,
            )
            else {}
        )

        precision = (
            market.get("precision")
            if isinstance(
                market.get("precision"),
                dict,
            )
            else {}
        )
        info = market.get("info") if isinstance(market.get("info"), dict) else {}

        contract_size = (
            cls._safe_positive(
                market.get(
                    "contractSize"
                )
            )
            or 1.0
        )

        min_amount = (
            cls._safe_positive(
                amount_limits.get("min")
            )
        )

        explicit_min_cost = (
            cls._safe_positive(
                cost_limits.get("min")
            )
        )

        amount_implied_notional = (
            min_amount
            * contract_size
            * reference_price
            if (
                min_amount is not None
                and reference_price > 0
            )
            else None
        )

        candidates = [
            value
            for value in (
                explicit_min_cost,
                amount_implied_notional,
            )
            if (
                value is not None
                and value > 0
            )
        ]

        effective_min_notional = (
            max(candidates)
            if candidates
            else None
        )

        return {
            "contract_size": contract_size,
            "minimum_amount": min_amount,
            "explicit_min_cost": (
                explicit_min_cost
            ),
            "amount_implied_min_notional": (
                round(
                    amount_implied_notional,
                    8,
                )
                if amount_implied_notional
                is not None
                else None
            ),
            "effective_min_notional": (
                round(
                    effective_min_notional,
                    8,
                )
                if effective_min_notional
                is not None
                else None
            ),
            "precision": {
                "amount": precision.get(
                    "amount"
                ),
                "price": precision.get(
                    "price"
                ),
            },
            "price_tick": cls._safe_positive(
                info.get("priceTick")
            ) or cls._safe_positive(precision.get("price")),
            "quantity_step": cls._safe_positive(
                info.get("volumeTick")
            ) or cls._safe_positive(precision.get("amount")),
            "maximum_leverage": cls._safe_positive(info.get("maxLeverage")),
            "price_limit_lower_rate": cls._safe_positive(
                info.get("priceLimitLowerValue")
            ),
            "price_limit_upper_rate": cls._safe_positive(
                info.get("priceLimitUpperValue")
            ),
            "price_limit_semantics": "relative_to_reference_fraction",
        }

    @classmethod
    def _depth_usdt(
        cls,
        levels: list,
        contract_size: float,
    ) -> float:
        """
        Raw total returned-book depth.

        Diagnostic only. LBank may return extreme far-away levels.
        """
        total = 0.0

        for level in levels:
            if (
                not isinstance(
                    level,
                    (list, tuple),
                )
                or len(level) < 2
            ):
                continue

            price = cls._safe_positive(
                level[0]
            )

            amount = cls._safe_positive(
                level[1]
            )

            if (
                price is None
                or amount is None
            ):
                continue

            total += (
                price
                * amount
                * contract_size
            )

        return round(
            total,
            8,
        )

    @classmethod
    def _bounded_depth_usdt(
        cls,
        levels: list,
        contract_size: float,
        anchor_price: float,
        side: str,
        band_bps: int,
    ) -> dict:
        """
        Measure depth relative to the executable top-of-book price.

        bid:
            best_bid down to N bps below best_bid

        ask:
            best_ask up to N bps above best_ask

        This deliberately does not anchor to midpoint. With a wide
        spread, midpoint-based bands can exclude both best bid and
        best ask and falsely report zero actionable depth.
        """
        if (
            anchor_price <= 0
            or band_bps <= 0
            or side not in {
                "bid",
                "ask",
            }
        ):
            return {
                "depth_usdt": 0.0,
                "levels": 0,
                "farthest_distance_bps": None,
            }

        band_fraction = (
            float(band_bps)
            / 10_000.0
        )

        if side == "bid":
            lower_bound = (
                anchor_price
                * (
                    1.0
                    - band_fraction
                )
            )
            upper_bound = (
                anchor_price
            )

        else:
            lower_bound = (
                anchor_price
            )
            upper_bound = (
                anchor_price
                * (
                    1.0
                    + band_fraction
                )
            )

        total = 0.0
        levels_used = 0
        farthest_distance_bps = None

        for level in levels:
            if (
                not isinstance(
                    level,
                    (list, tuple),
                )
                or len(level) < 2
            ):
                continue

            price = cls._safe_positive(
                level[0]
            )

            amount = cls._safe_positive(
                level[1]
            )

            if (
                price is None
                or amount is None
            ):
                continue

            if not (
                lower_bound
                <= price
                <= upper_bound
            ):
                continue

            total += (
                price
                * amount
                * contract_size
            )

            levels_used += 1

            distance_bps = (
                abs(
                    price
                    - anchor_price
                )
                / anchor_price
                * 10_000.0
            )

            if (
                farthest_distance_bps
                is None
                or distance_bps
                > farthest_distance_bps
            ):
                farthest_distance_bps = (
                    distance_bps
                )

        return {
            "depth_usdt": round(
                total,
                8,
            ),
            "levels": levels_used,
            "farthest_distance_bps": (
                round(
                    farthest_distance_bps,
                    4,
                )
                if farthest_distance_bps
                is not None
                else None
            ),
        }

    @classmethod
    def _bounded_depth_profile(
        cls,
        bids: list,
        asks: list,
        contract_size: float,
        best_bid: float,
        best_ask: float,
        bands_bps: Iterable[int],
    ) -> dict:
        profile = {}

        for band in bands_bps:
            band_int = int(
                band
            )

            bid = (
                cls._bounded_depth_usdt(
                    bids,
                    contract_size,
                    best_bid,
                    "bid",
                    band_int,
                )
            )

            ask = (
                cls._bounded_depth_usdt(
                    asks,
                    contract_size,
                    best_ask,
                    "ask",
                    band_int,
                )
            )

            profile[
                str(
                    band_int
                )
            ] = {
                "band_bps": band_int,
                "anchor": (
                    "best_bid_best_ask"
                ),
                "bid": bid,
                "ask": ask,
                "minimum_side_depth_usdt": (
                    round(
                        min(
                            bid[
                                "depth_usdt"
                            ],
                            ask[
                                "depth_usdt"
                            ],
                        ),
                        8,
                    )
                ),
            }

        return profile

    @classmethod
    def _vwap_for_notional(
        cls,
        levels: list,
        target_notional: float,
        contract_size: float,
    ) -> dict:
        remaining = float(
            target_notional
        )

        base_quantity = 0.0
        spent_notional = 0.0
        levels_used = 0

        for level in levels:
            if remaining <= 1e-12:
                break

            if (
                not isinstance(
                    level,
                    (list, tuple),
                )
                or len(level) < 2
            ):
                continue

            price = cls._safe_positive(
                level[0]
            )

            contracts = cls._safe_positive(
                level[1]
            )

            if (
                price is None
                or contracts is None
            ):
                continue

            level_notional = (
                price
                * contracts
                * contract_size
            )

            if level_notional <= 0:
                continue

            take_notional = min(
                remaining,
                level_notional,
            )

            take_base_quantity = (
                take_notional
                / price
            )

            spent_notional += (
                take_notional
            )

            base_quantity += (
                take_base_quantity
            )

            remaining -= (
                take_notional
            )

            levels_used += 1

        complete = (
            remaining <= 1e-8
        )

        vwap = (
            spent_notional
            / base_quantity
            if (
                complete
                and base_quantity > 0
            )
            else None
        )

        return {
            "complete": complete,
            "vwap": (
                round(
                    vwap,
                    12,
                )
                if vwap is not None
                else None
            ),
            "filled_notional": (
                round(
                    spent_notional,
                    8,
                )
            ),
            "unfilled_notional": (
                round(
                    max(
                        remaining,
                        0.0,
                    ),
                    8,
                )
            ),
            "levels_used": (
                levels_used
            ),
        }

    @classmethod
    def measure_orderbook(
        cls,
        symbol: str,
        market: dict,
        orderbook: dict,
        notionals: Iterable[float],
        depth_bands_bps: Iterable[int] = (
            10,
            25,
            50,
            100,
        ),
        observed_at: float | None = None,
        mark_price: float | None = None,
    ) -> dict:
        if not is_usdt_linear_perpetual(
            market
        ):
            return {
                "available": False,
                "symbol": symbol,
                "source_exchange": (
                    "lbank"
                ),
                "reason": (
                    "not an active "
                    "USDT-linear perpetual"
                ),
            }

        bids = (
            orderbook.get("bids")
            if isinstance(
                orderbook.get("bids"),
                list,
            )
            else []
        )

        asks = (
            orderbook.get("asks")
            if isinstance(
                orderbook.get("asks"),
                list,
            )
            else []
        )

        if (
            not bids
            or not asks
        ):
            return {
                "available": False,
                "symbol": symbol,
                "source_exchange": (
                    "lbank"
                ),
                "reason": (
                    "empty LBank orderbook"
                ),
            }

        validated_mark_price = cls._safe_positive(mark_price) or cls._mark_price(market)
        if validated_mark_price is None:
            return {
                "available": False,
                "symbol": symbol,
                "source_exchange": "lbank",
                "reason": "LBank mark price unavailable",
            }

        best_bid = (
            cls._safe_positive(
                bids[0][0]
                if len(bids[0]) >= 2
                else None
            )
        )

        best_ask = (
            cls._safe_positive(
                asks[0][0]
                if len(asks[0]) >= 2
                else None
            )
        )

        if (
            best_bid is None
            or best_ask is None
            or best_ask <= best_bid
        ):
            return {
                "available": False,
                "symbol": symbol,
                "source_exchange": (
                    "lbank"
                ),
                "reason": (
                    "invalid LBank best bid/ask"
                ),
            }

        midpoint = (
            best_bid
            + best_ask
        ) / 2.0

        spread_pct = (
            (
                best_ask
                - best_bid
            )
            / midpoint
            * 100.0
        )

        spread_bps = (
            spread_pct
            * 100.0
        )

        contract_size = (
            cls._safe_positive(
                market.get(
                    "contractSize"
                )
            )
            or 1.0
        )

        raw_bid_depth = (
            cls._depth_usdt(
                bids,
                contract_size,
            )
        )

        raw_ask_depth = (
            cls._depth_usdt(
                asks,
                contract_size,
            )
        )

        bounded_depth = (
            cls._bounded_depth_profile(
                bids,
                asks,
                contract_size,
                best_bid,
                best_ask,
                depth_bands_bps,
            )
        )

        execution = {}

        for raw_notional in notionals:
            notional = (
                cls._safe_positive(
                    raw_notional
                )
            )

            if notional is None:
                continue

            sell = (
                cls._vwap_for_notional(
                    bids,
                    notional,
                    contract_size,
                )
            )

            buy = (
                cls._vwap_for_notional(
                    asks,
                    notional,
                    contract_size,
                )
            )

            sell_vwap = sell.get(
                "vwap"
            )

            buy_vwap = buy.get(
                "vwap"
            )

            entry_slippage_pct = (
                (
                    best_bid
                    - sell_vwap
                )
                / best_bid
                * 100.0
                if sell_vwap
                is not None
                else None
            )

            exit_slippage_pct = (
                (
                    buy_vwap
                    - best_ask
                )
                / best_ask
                * 100.0
                if buy_vwap
                is not None
                else None
            )

            round_trip_slippage_pct = (
                entry_slippage_pct
                + exit_slippage_pct
                if (
                    entry_slippage_pct
                    is not None
                    and exit_slippage_pct
                    is not None
                )
                else None
            )

            effective_crossing_cost_pct = (
                spread_pct
                + round_trip_slippage_pct
                if (
                    round_trip_slippage_pct
                    is not None
                )
                else None
            )

            execution[
                str(
                    int(notional)
                    if notional.is_integer()
                    else notional
                )
            ] = {
                "notional_usdt": (
                    notional
                ),
                "sell": sell,
                "buy": buy,
                "entry_slippage_pct": (
                    round(
                        entry_slippage_pct,
                        6,
                    )
                    if entry_slippage_pct
                    is not None
                    else None
                ),
                "exit_slippage_pct": (
                    round(
                        exit_slippage_pct,
                        6,
                    )
                    if exit_slippage_pct
                    is not None
                    else None
                ),
                "round_trip_slippage_pct": (
                    round(
                        round_trip_slippage_pct,
                        6,
                    )
                    if round_trip_slippage_pct
                    is not None
                    else None
                ),
                "effective_crossing_cost_pct": (
                    round(
                        effective_crossing_cost_pct,
                        6,
                    )
                    if effective_crossing_cost_pct
                    is not None
                    else None
                ),
            }

        return {
            "available": True,
            "symbol": symbol,
            "source_exchange": (
                "lbank"
            ),
            "observed_at": float(
                observed_at
                if observed_at is not None
                else time.time()
            ),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "midpoint": round(
                midpoint,
                12,
            ),
            "mark_price": round(validated_mark_price, 12),
            "spread_pct": round(
                spread_pct,
                6,
            ),
            "spread_bps": round(
                spread_bps,
                4,
            ),
            "depth": {
                "bounded": (
                    bounded_depth
                ),
                "raw_total_diagnostic": {
                    "bid_depth_usdt": (
                        raw_bid_depth
                    ),
                    "ask_depth_usdt": (
                        raw_ask_depth
                    ),
                    "warning": (
                        "raw total depth may "
                        "contain extreme far-away "
                        "LBank book levels and "
                        "must not be used for "
                        "execution eligibility"
                    ),
                },
            },
            "market_filters": (
                cls._market_filters(
                    market,
                    validated_mark_price,
                )
            ),
            "execution": execution,
        }

    async def observe(
        self,
        symbol: str,
    ) -> dict:
        try:
            exchange = (
                await self
                ._ensure_exchange()
            )

            market = (
                exchange.markets.get(
                    symbol
                )
            )

            if not market:
                return {
                    "available": False,
                    "symbol": symbol,
                    "source_exchange": (
                        "lbank"
                    ),
                    "reason": (
                        "symbol absent from "
                        "LBank markets"
                    ),
                }

            orderbook = (
                await exchange
                .fetch_order_book(
                    symbol,
                    limit=(
                        self.orderbook_limit
                    ),
                )
            )
            ticker = await exchange.fetch_ticker(symbol)
            mark_price = self._mark_price(market, ticker)

            return (
                self.measure_orderbook(
                    symbol,
                    market,
                    orderbook,
                    self.notionals,
                    depth_bands_bps=(
                        self.depth_bands_bps
                    ),
                    observed_at=(
                        time.time()
                    ),
                    mark_price=mark_price,
                )
            )

        except Exception as exc:
            return {
                "available": False,
                "symbol": symbol,
                "source_exchange": (
                    "lbank"
                ),
                "reason": (
                    "LBank execution "
                    "observation failed: "
                    f"{type(exc).__name__}: "
                    f"{exc}"
                ),
            }

    async def observe_many(
        self,
        symbols: Iterable[str],
    ) -> dict[str, dict]:
        results = {}

        try:
            await self._ensure_exchange()

            for symbol in symbols:
                results[
                    symbol
                ] = await self.observe(
                    symbol
                )

            return results

        finally:
            await self.close()
