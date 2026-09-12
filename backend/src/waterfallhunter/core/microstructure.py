import asyncio
import copy
import time
from typing import Any, Dict, List, Optional


class MicrostructureAnalyzer:
    def __init__(self, executable_notional: float = 50.0, snapshot_delay_seconds: float = 0.25):
        self.executable_notional = executable_notional
        self.snapshot_delay_seconds = max(0.0, float(snapshot_delay_seconds))
        self.snapshot_ttl_seconds = 5.0
        self.trade_ttl_seconds = 60.0

    @staticmethod
    def _vwap(levels: List[List[float]], notional: float, contract_size: float = 1.0) -> Optional[float]:
        remaining, quantity, value = notional, 0.0, 0.0
        for price, amount, *_ in levels:
            price, amount = float(price), float(amount)
            take = min(remaining, price * amount * contract_size)
            if take > 0:
                quantity += take / price
                value += take
                remaining -= take
            if remaining <= 0:
                break
        return value / quantity if remaining <= 0 and quantity else None

    @staticmethod
    def _depth(levels: List[List[float]], contract_size: float = 1.0) -> float:
        return sum(float(price) * float(amount) * contract_size for price, amount, *_ in levels)

    @staticmethod
    def _change_pct(current: float, previous: float) -> float | None:
        if previous <= 0:
            return None
        return (current - previous) / previous * 100.0

    def _snapshot_is_usable(self, snapshot: Any, *, now: float) -> bool:
        if not isinstance(snapshot, dict) or not snapshot.get("bids") or not snapshot.get("asks"):
            return False
        received_at = snapshot.get("_received_at")
        if (
            isinstance(received_at, bool)
            or not isinstance(received_at, (int, float))
            or float(received_at) > now
            or now - float(received_at) > self.snapshot_ttl_seconds
        ):
            return False
        timestamp = snapshot.get("timestamp")
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            return True
        now_ms = int(now * 1000)
        timestamp_ms = int(timestamp)
        return bool(
            0 < timestamp_ms <= now_ms
            and now_ms - timestamp_ms <= int(self.snapshot_ttl_seconds * 1000)
        )

    def _fresh_trades(self, trades: Any, *, now: float) -> list[Dict[str, Any]]:
        if not isinstance(trades, list):
            return []
        now_ms = int(now * 1000)
        ttl_ms = int(self.trade_ttl_seconds * 1000)
        return [
            trade
            for trade in trades
            if isinstance(trade, dict)
            and isinstance(trade.get("timestamp"), (int, float))
            and not isinstance(trade.get("timestamp"), bool)
            and 0 < int(trade["timestamp"]) <= now_ms
            and now_ms - int(trade["timestamp"]) <= ttl_ms
        ]

    def _preloaded_snapshots_are_usable(
        self, snapshots: Any, *, now: float
    ) -> bool:
        if not isinstance(snapshots, list) or len(snapshots) != 3:
            return False
        if not all(self._snapshot_is_usable(snapshot, now=now) for snapshot in snapshots):
            return False
        received = [float(snapshot["_received_at"]) for snapshot in snapshots]
        return received[-1] - received[0] >= 2.0 * self.snapshot_delay_seconds

    def _preloaded_trades_are_usable(self, trades: Any, *, now: float) -> bool:
        return isinstance(trades, list) and len(self._fresh_trades(trades, now=now)) >= 20

    def _preloaded_evidence_is_usable(
        self,
        snapshots: Any,
        trades: Any,
        *,
        now: float,
    ) -> bool:
        return self._preloaded_snapshots_are_usable(
            snapshots, now=now
        ) and self._preloaded_trades_are_usable(trades, now=now)

    @staticmethod
    def _contract_size(market: Dict[str, Any]) -> float | None:
        try:
            contract_size = float(market.get("contractSize"))
        except (TypeError, ValueError):
            return None
        return contract_size if contract_size > 0 else None

    async def _fetch_orderbook_snapshot_series(
        self,
        exchange: Any,
        symbol: str,
        *,
        initial: Dict[str, Any] | None = None,
    ) -> list[Dict[str, Any]]:
        snapshots: list[Dict[str, Any]] = []
        if initial is not None:
            first = copy.deepcopy(initial)
            first.setdefault("_received_at", time.time())
            snapshots.append(first)
        while len(snapshots) < 3:
            if snapshots:
                await asyncio.sleep(self.snapshot_delay_seconds)
            snapshot = await exchange.fetch_order_book(symbol, limit=20)
            snapshot["_received_at"] = time.time()
            snapshots.append(snapshot)
        return snapshots

    @staticmethod
    async def _settle_fetch_task(task: asyncio.Task | None) -> None:
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _fetch_trades_with_receipt_freshness(
        self, exchange: Any, symbol: str
    ) -> tuple[list[Dict[str, Any]], bool]:
        trades = await exchange.fetch_trades(symbol, limit=100)
        return trades, self._preloaded_trades_are_usable(trades, now=time.time())

    async def _refresh_expired_evidence(
        self,
        exchange: Any,
        symbol: str,
        snapshots: list[Dict[str, Any]],
        trades: list[Dict[str, Any]],
        *,
        allow_snapshot_refresh: bool = True,
        allow_trade_refresh: bool = True,
    ) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
        # Evidence can age while the other half is being fetched. Revalidate both
        # sides after collection and refresh only the stale half. Bound the loop
        # so a slow provider cannot turn freshness repair into an unbounded retry.
        for _ in range(3):
            now = time.time()
            snapshots_fresh = (
                not allow_snapshot_refresh
                or self._preloaded_snapshots_are_usable(snapshots, now=now)
            )
            trades_fresh = self._preloaded_trades_are_usable(trades, now=now)
            if snapshots_fresh and trades_fresh:
                break
            snapshot_task = (
                asyncio.create_task(self._fetch_orderbook_snapshot_series(exchange, symbol))
                if not snapshots_fresh
                else None
            )
            trades_task = (
                asyncio.create_task(self._fetch_trades_with_receipt_freshness(exchange, symbol))
                if not trades_fresh and allow_trade_refresh
                else None
            )
            if snapshot_task is None and trades_task is None:
                break
            try:
                if snapshot_task is not None:
                    snapshots = await snapshot_task
                if trades_task is not None:
                    trades, allow_trade_refresh = await trades_task
                    # An immediately underfilled REST batch cannot become sufficient
                    # by refetching it again in the same evaluation. Only batches
                    # that were usable when received may be refreshed later if they
                    # age while the other half of evidence is collected.
            except asyncio.CancelledError:
                await self._settle_fetch_task(snapshot_task)
                await self._settle_fetch_task(trades_task)
                raise
            except Exception:
                await self._settle_fetch_task(snapshot_task)
                await self._settle_fetch_task(trades_task)
                raise
        return snapshots, trades

    async def _collect_evidence(
        self,
        exchange: Any,
        symbol: str,
        first: Dict[str, Any],
        preloaded_snapshots: list[Dict[str, Any]] | None,
        preloaded_trades: list[Dict[str, Any]] | None,
    ) -> tuple[list[Dict[str, Any]] | None, list[Dict[str, Any]] | None, str | None]:
        preload_now = time.time()
        snapshots_preloaded = self._preloaded_snapshots_are_usable(
            preloaded_snapshots, now=preload_now
        )
        trades_preloaded = self._preloaded_trades_are_usable(
            preloaded_trades, now=preload_now
        )
        if snapshots_preloaded and trades_preloaded:
            return (
                copy.deepcopy(preloaded_snapshots),
                copy.deepcopy(preloaded_trades),
                None,
            )

        snapshots = copy.deepcopy(preloaded_snapshots) if snapshots_preloaded else None
        trades = copy.deepcopy(preloaded_trades) if trades_preloaded else None
        initial_for_validation = copy.deepcopy(first)
        initial_for_validation.setdefault("_received_at", preload_now)
        allow_snapshot_refresh = bool(
            snapshots_preloaded
            or self._snapshot_validation_reason(
                [initial_for_validation], now=preload_now
            ) is None
        )
        trades_task = (
            None
            if trades_preloaded
            else asyncio.create_task(self._fetch_trades_with_receipt_freshness(exchange, symbol))
        )
        allow_trade_refresh = bool(trades_preloaded)
        try:
            if snapshots is None:
                snapshots = await self._fetch_orderbook_snapshot_series(
                    exchange, symbol, initial=first
                )
            if trades_task is not None:
                trades, allow_trade_refresh = await trades_task
            snapshots, trades = await self._refresh_expired_evidence(
                exchange,
                symbol,
                snapshots,
                trades,
                allow_snapshot_refresh=allow_snapshot_refresh,
                allow_trade_refresh=allow_trade_refresh,
            )
            return snapshots, trades, None
        except asyncio.CancelledError:
            await self._settle_fetch_task(trades_task)
            raise
        except Exception:
            await self._settle_fetch_task(trades_task)
            return None, None, "missing live orderbook snapshots or trades"

    def _snapshot_validation_reason(
        self,
        snapshots: list[Dict[str, Any]],
        *,
        now: float,
    ) -> str | None:
        if any(not item.get("bids") or not item.get("asks") for item in snapshots):
            return "empty live orderbook"
        now_ms = int(now * 1000)
        ttl_ms = int(self.snapshot_ttl_seconds * 1000)
        for snapshot in snapshots:
            timestamp = snapshot.get("timestamp")
            received_at = snapshot.get("_received_at")
            if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
                timestamp_ms = int(timestamp)
                if timestamp_ms <= 0 or timestamp_ms > now_ms or now_ms - timestamp_ms > ttl_ms:
                    return "stale orderbook snapshot"
                continue
            if (
                isinstance(received_at, bool)
                or not isinstance(received_at, (int, float))
                or float(received_at) > now
                or now - float(received_at) > self.snapshot_ttl_seconds
            ):
                return "orderbook receipt timestamp unavailable"
        return None

    def _orderbook_geometry(
        self,
        snapshots: list[Dict[str, Any]],
        *,
        contract_size: float,
    ) -> tuple[dict[str, Any] | None, str | None]:
        bids = snapshots[-1]["bids"]
        asks = snapshots[-1]["asks"]
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        if best_bid <= 0 or best_ask <= best_bid:
            return None, "invalid spread"
        sell_vwap = self._vwap(bids, self.executable_notional, contract_size)
        buy_vwap = self._vwap(asks, self.executable_notional, contract_size)
        bid_depth = self._depth(bids, contract_size)
        ask_depth = self._depth(asks, contract_size)
        initial_bid_depth = self._depth(snapshots[0]["bids"], contract_size)
        initial_ask_depth = self._depth(snapshots[0]["asks"], contract_size)
        churn = sum(
            abs(self._depth(item["bids"], contract_size) - initial_bid_depth)
            for item in snapshots[1:]
        ) / max(bid_depth, 1.0)
        return {
            "bids": bids,
            "asks": asks,
            "observed_at": float(snapshots[-1]["_received_at"]),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "sell_vwap": sell_vwap,
            "buy_vwap": buy_vwap,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "initial_bid_depth": initial_bid_depth,
            "initial_ask_depth": initial_ask_depth,
            "churn": churn,
            "spoofing": churn > 1.5,
            "spread_pct": (best_ask - best_bid) / best_ask * 100,
            "entry_slippage_pct": (
                (best_bid - sell_vwap) / best_bid * 100 if sell_vwap is not None else None
            ),
            "exit_slippage_pct": (
                (buy_vwap - best_ask) / best_ask * 100 if buy_vwap is not None else None
            ),
        }, None

    @staticmethod
    def _trade_flow_metrics(
        trades: list[Dict[str, Any]],
        *,
        contract_size: float,
    ) -> dict[str, Any]:
        sell_flow = sum(
            float(trade.get("amount", 0)) * float(trade.get("price", 0)) * contract_size
            for trade in trades
            if trade.get("side") == "sell"
        )
        buy_flow = sum(
            float(trade.get("amount", 0)) * float(trade.get("price", 0)) * contract_size
            for trade in trades
            if trade.get("side") == "buy"
        )
        sell_by_price: Dict[float, float] = {}
        buy_by_price: Dict[float, float] = {}
        for trade in trades:
            try:
                price = float(trade.get("price"))
                notional = price * float(trade.get("amount")) * contract_size
            except (TypeError, ValueError):
                continue
            side = trade.get("side")
            bucket = sell_by_price if side == "sell" else buy_by_price if side == "buy" else None
            if bucket is not None and price > 0 and notional > 0:
                bucket[price] = bucket.get(price, 0.0) + notional
        flow_total = sell_flow + buy_flow
        sell_imbalances = sum(
            value >= 2.0 * buy_by_price.get(price, 0.0) and value > 0
            for price, value in sell_by_price.items()
        )
        return {
            "sell_flow": sell_flow,
            "buy_flow": buy_flow,
            "flow_total": flow_total,
            "sell_flow_ratio": sell_flow / flow_total if flow_total > 0 else None,
            "sell_imbalances": sell_imbalances,
            "footprint_available": len(trades) >= 20 and flow_total > 0,
        }

    def _exchange_filter_metrics(
        self,
        market: Dict[str, Any],
        *,
        best_bid: float,
        sell_vwap: float | None,
        buy_vwap: float | None,
        contract_size: float,
    ) -> tuple[dict[str, Any] | None, str | None]:
        limits = market.get("limits", {})
        try:
            min_amount = float(limits.get("amount", {}).get("min"))
        except (TypeError, ValueError):
            min_amount = None
        try:
            explicit_min_cost = float(limits.get("cost", {}).get("min"))
        except (TypeError, ValueError):
            explicit_min_cost = None
        if min_amount is None or min_amount <= 0:
            return None, "invalid exchange filters: minimum amount unavailable"
        minimum_notional = max(explicit_min_cost or 0.0, min_amount * contract_size * best_bid)
        contracts = self.executable_notional / (sell_vwap * contract_size) if sell_vwap is not None else None
        executable = bool(
            contracts is not None
            and buy_vwap is not None
            and self.executable_notional >= minimum_notional
            and contracts >= min_amount
        )
        return {
            "min_amount": min_amount,
            "minimum_notional": minimum_notional,
            "contracts": contracts,
            "executable": executable,
        }, None

    @staticmethod
    def _source_capture(
        trades: list[Dict[str, Any]],
        snapshots: list[Dict[str, Any]],
        market: Dict[str, Any],
        *,
        trade_ttl_seconds: float,
    ) -> dict[str, Any]:
        source_trades = [
            {
                key: trade.get(key)
                for key in (
                    "id", "timestamp", "datetime", "symbol",
                    "side", "price", "amount", "cost", "takerOrMaker",
                )
                if key in trade
            }
            for trade in trades[:100]
        ]
        source_orderbook_snapshots = [
            {
                "timestamp": snapshot.get("timestamp"),
                "received_at": snapshot.get("_received_at"),
                "bids": [list(level[:3]) for level in (snapshot.get("bids") or [])],
                "asks": [list(level[:3]) for level in (snapshot.get("asks") or [])],
            }
            for snapshot in snapshots
        ]
        source_market = {
            "contractSize": market.get("contractSize"),
            "limits": market.get("limits"),
            "precision": market.get("precision"),
        }
        return {
            "fresh_trades": source_trades,
            "raw_trades_captured": len(source_trades) >= 20,
            "trade_ttl_seconds": trade_ttl_seconds,
            "orderbook_snapshots": source_orderbook_snapshots,
            "orderbook_snapshots_captured": bool(
                len(source_orderbook_snapshots) == 3
                and all(item.get("bids") and item.get("asks") for item in source_orderbook_snapshots)
            ),
            "market": source_market,
            "market_filters_captured": bool(
                source_market.get("contractSize") is not None
                and isinstance(source_market.get("limits"), dict)
            ),
        }

    def _result_packet(
        self,
        *,
        geometry: dict[str, Any],
        flow: dict[str, Any],
        filters: dict[str, Any],
        market: Dict[str, Any],
        trades: list[Dict[str, Any]],
        snapshots: list[Dict[str, Any]],
        contract_size: float,
    ) -> Dict[str, Any]:
        entry_slippage = geometry["entry_slippage_pct"]
        exit_slippage = geometry["exit_slippage_pct"]
        approved = bool(
            filters["executable"]
            and not geometry["spoofing"]
            and flow["sell_flow"] > flow["buy_flow"]
            and geometry["spread_pct"] <= 0.5
            and entry_slippage is not None
            and entry_slippage <= 0.3
            and exit_slippage is not None
            and exit_slippage <= 0.3
        )
        reason = (
            "insufficient executable bid depth" if geometry["sell_vwap"] is None
            else "insufficient executable ask depth" if geometry["buy_vwap"] is None
            else None
        )
        bid_change = self._change_pct(geometry["bid_depth"], geometry["initial_bid_depth"])
        ask_change = self._change_pct(geometry["ask_depth"], geometry["initial_ask_depth"])
        footprint_available = flow["footprint_available"]
        return {
            "approved": approved,
            "reason": reason,
            "observed_at": geometry["observed_at"],
            "spread_pct": round(geometry["spread_pct"], 4),
            "best_bid": geometry["best_bid"],
            "best_ask": geometry["best_ask"],
            "sell_vwap": geometry["sell_vwap"],
            "buy_vwap": geometry["buy_vwap"],
            "slippage_pct": round(entry_slippage, 4) if entry_slippage is not None else None,
            "entry_slippage_pct": round(entry_slippage, 4) if entry_slippage is not None else None,
            "exit_slippage_pct": round(exit_slippage, 4) if exit_slippage is not None else None,
            "round_trip_slippage_pct": (
                round(entry_slippage + exit_slippage, 4)
                if entry_slippage is not None and exit_slippage is not None
                else None
            ),
            "bid_depth_usdt": geometry["bid_depth"],
            "ask_depth_usdt": geometry["ask_depth"],
            "sell_flow_usdt": flow["sell_flow"],
            "buy_flow_usdt": flow["buy_flow"],
            "churn": round(geometry["churn"], 4),
            "sell_flow_ratio": (
                round(flow["sell_flow_ratio"], 4)
                if flow["sell_flow_ratio"] is not None
                else None
            ),
            "footprint": {
                "available": footprint_available,
                "trade_count": len(trades),
                "sell_delta_usdt": round(flow["sell_flow"] - flow["buy_flow"], 4),
                "sell_imbalance_levels": flow["sell_imbalances"],
                "aggressive_selling": bool(
                    footprint_available
                    and flow["sell_flow"] > flow["buy_flow"]
                    and flow["sell_imbalances"] > 0
                ),
            },
            "spoofing_detected": geometry["spoofing"],
            "executable_notional": self.executable_notional,
            "executable": filters["executable"],
            "minimum_notional": filters["minimum_notional"],
            "contracts": filters["contracts"],
            "exchange_filters": {
                "precision": market.get("precision"),
                "minimum_amount": filters["min_amount"],
                "contract_size": contract_size,
            },
            "precrash_observations": {
                "contract_version": "precrash_orderbook_observation_v1",
                "observational_only": True,
                "hard_gating_allowed": False,
                "promotion_allowed": False,
                "bid_depth_change_pct": round(bid_change, 4) if bid_change is not None else None,
                "ask_depth_change_pct": round(ask_change, 4) if ask_change is not None else None,
                "depth_churn_ratio": round(geometry["churn"], 4),
            },
            "source_capture": self._source_capture(
                trades,
                snapshots,
                market,
                trade_ttl_seconds=self.trade_ttl_seconds,
            ),
        }

    async def analyze(
        self,
        exchange: Any,
        symbol: str,
        first: Dict[str, Any],
        market: Dict[str, Any],
        *,
        preloaded_snapshots: list[Dict[str, Any]] | None = None,
        preloaded_trades: list[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """Analyze a causally fresh microstructure packet with REST fallback."""
        if not first:
            return {"approved": False, "reason": "missing live orderbook"}
        contract_size = self._contract_size(market)
        if contract_size is None:
            return {"approved": False, "reason": "invalid exchange filters: contract size unavailable"}

        snapshots, trades, error = await self._collect_evidence(
            exchange,
            symbol,
            first,
            preloaded_snapshots,
            preloaded_trades,
        )
        if error is not None or snapshots is None or trades is None:
            return {"approved": False, "reason": error or "missing live orderbook snapshots or trades"}

        now = time.time()
        snapshot_error = self._snapshot_validation_reason(snapshots, now=now)
        if snapshot_error is not None:
            return {"approved": False, "reason": snapshot_error}
        fresh_trades = self._fresh_trades(trades, now=now)
        if len(fresh_trades) < 20:
            return {"approved": False, "reason": "insufficient fresh trades"}

        geometry, geometry_error = self._orderbook_geometry(
            snapshots,
            contract_size=contract_size,
        )
        if geometry_error is not None or geometry is None:
            return {"approved": False, "reason": geometry_error or "invalid spread"}
        flow = self._trade_flow_metrics(fresh_trades, contract_size=contract_size)
        filters, filter_error = self._exchange_filter_metrics(
            market,
            best_bid=geometry["best_bid"],
            sell_vwap=geometry["sell_vwap"],
            buy_vwap=geometry["buy_vwap"],
            contract_size=contract_size,
        )
        if filter_error is not None or filters is None:
            return {"approved": False, "reason": filter_error or "invalid exchange filters"}
        return self._result_packet(
            geometry=geometry,
            flow=flow,
            filters=filters,
            market=market,
            trades=fresh_trades,
            snapshots=snapshots,
            contract_size=contract_size,
        )
