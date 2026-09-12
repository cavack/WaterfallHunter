import asyncio
import copy
import logging
import math
import time

from waterfallhunter.core.liquidation_flow import LIQUIDATION_FLOW_FRESHNESS_SECONDS
from waterfallhunter.core.multi_exchange import MultiExchangeGateway
import random
from typing import Dict, Any, Optional

try:
    import ccxt.pro as ccxt_pro
except ImportError:  # CCXT Pro is a separately licensed package.
    ccxt_pro = None


def _install_ccxt_ws_client_reset_compat(client_cls: Any | None = None) -> bool:
    """Backport the CCXT JS ``Client.reset`` contract when Python omits it."""
    if client_cls is None:
        if ccxt_pro is None:
            return False
        try:
            from ccxt.async_support.base.ws.client import Client as client_cls
        except ImportError:
            return False
    if callable(getattr(client_cls, "reset", None)):
        return False

    def reset(self, error):
        ping_looper = getattr(self, "ping_looper", None)
        cancel = getattr(ping_looper, "cancel", None)
        if callable(cancel):
            cancel()
        return self.reject(error)

    setattr(client_cls, "reset", reset)
    return True


_install_ccxt_ws_client_reset_compat()

logger = logging.getLogger("WaterfallHunter.WebSockets")

class CircuitBreaker:
    """قطع‌کننده مدار 3-حالته برای مدیریت هوشمند قطعی‌های صرافی"""
    def __init__(self, failure_threshold=3, recovery_timeout=30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failures = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"  # حالت‌ها: CLOSED (سالم), OPEN (قطع), HALF_OPEN (در حال تست)

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        if self.failures >= self.failure_threshold:
            self.state = "OPEN"

    def record_success(self):
        self.failures = 0
        self.state = "CLOSED"

    def can_try(self) -> bool:
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "HALF_OPEN"
                return True
            return False
        return True  # در حالت HALF_OPEN یک شانس مجدد می‌دهیم

class WebSocketManager:
    def __init__(self):
        self.exchanges_names = ['binance', 'bybit', 'kucoin', 'okx', 'mexc', 'bingx']
        self.exchanges: Dict[str, Any] = {}
        # Direct orderbook/ticker/trades share one exchange instance per venue,
        # but every symbol retirement explicitly unwatches its CCXT subscriptions
        # and closes zero-subscription WebSocket clients. Liquidation ownership is
        # isolated because CCXT Pro does not expose matching unwatch methods.
        self.liquidation_exchanges: Dict[str, Any] = {}
        self.shared_liquidation_exchanges: Dict[str, Any] = {}
        self._direct_symbol_retire_tasks: Dict[str, asyncio.Task] = {}
        self._liquidation_exchange_retire_tasks: Dict[str, asyncio.Task] = {}
        self._shared_liquidation_retire_tasks: Dict[str, asyncio.Task] = {}
        self._exchange_close_finalizer_tasks: set[asyncio.Task] = set()
        self._exchange_close_finalizer_venues: Dict[asyncio.Task, str] = {}
        self.exchange_close_timeouts = 0
        self._direct_venue_locks: Dict[str, asyncio.Lock] = {}
        self.retirement_timeout_seconds = 10.0
        self.exchange_close_timeout_seconds = 10.0
        # Keep shared FUEL-RICH subscriptions on dedicated CCXT Pro clients so
        # their dynamic unwatch operations cannot cancel PRE-TRIGGER/ARMED
        # direct subscriptions that use the same venue/message hashes.
        self.shared_evidence_exchanges: Dict[str, Any] = {}

        # ساختار کش: { 'exchange:symbol': {'data': orderbook, 'updated_at': timestamp} }
        self.live_orderbooks: Dict[str, Dict[str, Any]] = {}
        self.live_orderbook_history: Dict[str, list[Dict[str, Any]]] = {}
        self.live_tickers: Dict[str, Dict[str, Any]] = {}
        self.live_trades: Dict[str, Dict[str, Any]] = {}
        self.orderbook_history_limit = 8
        self.trade_history_limit = 500
        self.trade_ttl_seconds = 60.0
        self.live_liquidations: Dict[str, Dict[str, Any]] = {}
        self.liquidation_window_seconds = LIQUIDATION_FLOW_FRESHNESS_SECONDS
        self.liquidation_retention_seconds = 120.0
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.message_counters: Dict[str, int] = {}
        self.unsupported_liquidation_exchanges: set[str] = set()
        # Binance exposes an official all-market liquidation stream. Track
        # desired symbols separately so PRE-TRIGGER evidence uses one bounded
        # consumer instead of one WebSocket subscription per symbol.
        self.liquidation_subscribers: Dict[str, set[str]] = {}
        # FUEL-RICH symbols express desired logical membership only. A single-flight
        # reconciler owns transport generations so candidate lifecycle never mutates
        # CCXT Pro multi-symbol subscriptions in place.
        self.shared_evidence_subscribers: Dict[str, set[str]] = {}
        self.shared_evidence_active_symbols: Dict[str, tuple[str, ...]] = {}
        self.shared_evidence_generation: Dict[str, int] = {}
        self.shared_evidence_retirement_failures: Dict[str, int] = {}
        self.shared_evidence_blocked_exchanges: set[str] = set()
        self._abandoned_close_tasks: set[asyncio.Task] = set()
        self.unsupported_shared_evidence_exchanges: set[str] = set()
        self.shared_evidence_direct_fallbacks: set[str] = set()
        self.shared_evidence_symbol_limit = 64
        # Multi-symbol CCXT transports are expensive to retire/recreate and a
        # rapidly changing FUEL-RICH set can otherwise keep the evidence cache
        # permanently cold. Keep a healthy generation alive long enough to
        # coalesce membership churn; initial startup, empty membership and
        # broken-stream repair remain immediate.
        self.shared_evidence_reconcile_min_interval_seconds = 30.0
        self._shared_evidence_generation_started_at: Dict[str, float] = {}
        self._shared_evidence_reconcile_events: Dict[str, asyncio.Event] = {}
        self._shared_evidence_reconcile_tasks: Dict[str, asyncio.Task] = {}
        self._shared_evidence_reconcile_locks: Dict[str, asyncio.Lock] = {}
        self._shared_evidence_reconcile_dirty: set[str] = set()

        # قانون سخت: دیتای قدیمی‌تر از 5 ثانیه منقضی (Stale) محسوب می‌شود
        self.ttl_seconds = 5.0
        self._lock = asyncio.Lock()
        # One slot per concurrent stream consumer instead of a shared pool:
        # every stream holds its slot only around the await boundary it needs,
        # so N symbols no longer starve each other through a single semaphore.
        self._stream_slots = asyncio.Semaphore(10)

    def _ingest_orderbook(
        self,
        ex_name: str,
        symbol: str,
        orderbook: Any,
        *,
        received_at: float | None = None,
    ) -> None:
        if not isinstance(orderbook, dict):
            return
        now = time.time() if received_at is None else float(received_at)
        if not math.isfinite(now) or now < 0:
            return
        stream_id = f"{ex_name}:{symbol}"
        snapshot = copy.deepcopy(orderbook)
        snapshot["_received_at"] = now
        self.live_orderbooks[stream_id] = {
            "data": snapshot,
            "updated_at": now,
        }
        history = self.live_orderbook_history.setdefault(stream_id, [])
        history.append(snapshot)
        if len(history) > self.orderbook_history_limit:
            del history[:-self.orderbook_history_limit]

    _TRADE_CACHE_FIELDS = (
        "id", "timestamp", "datetime", "symbol", "side",
        "price", "amount", "cost", "takerOrMaker",
    )

    @staticmethod
    def _trade_fingerprint(trade: Dict[str, Any]) -> tuple[Any, ...]:
        trade_id = trade.get("id")
        if trade_id not in (None, ""):
            return ("id", str(trade_id), None, None, None)
        return (
            "fields", trade.get("timestamp"), trade.get("side"),
            trade.get("price"), trade.get("amount"),
        )

    @classmethod
    def _trade_cache_row(cls, trade: Any) -> Dict[str, Any] | None:
        if not isinstance(trade, dict):
            return None
        return {key: trade.get(key) for key in cls._TRADE_CACHE_FIELDS if key in trade}

    def _ingest_trades(
        self,
        ex_name: str,
        symbol: str,
        rows: Any,
        *,
        received_at: float | None = None,
    ) -> None:
        now = time.time() if received_at is None else float(received_at)
        if not math.isfinite(now) or now < 0:
            return
        stream_id = f"{ex_name}:{symbol}"
        existing = self.live_trades.get(stream_id, {}).get("data", [])
        cutoff_ms = (now - self.trade_ttl_seconds) * 1000.0
        deduped: dict[tuple[Any, ...], Dict[str, Any]] = {}

        # Cached rows are already detached, normalized evidence packets. Reusing
        # them avoids O(history_size) deepcopy work on every high-frequency trade
        # event while preserving the same TTL/dedupe semantics.
        for trade in existing if isinstance(existing, list) else []:
            if not isinstance(trade, dict):
                continue
            timestamp = trade.get("timestamp")
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                continue
            if timestamp <= 0 or float(timestamp) < cutoff_ms:
                continue
            deduped[self._trade_fingerprint(trade)] = trade

        iterable = rows if isinstance(rows, list) else [rows]
        for raw_trade in iterable:
            trade = self._trade_cache_row(raw_trade)
            if trade is None:
                continue
            timestamp = trade.get("timestamp")
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                continue
            if timestamp <= 0 or float(timestamp) < cutoff_ms:
                continue
            deduped[self._trade_fingerprint(trade)] = trade

        fresh = sorted(
            deduped.values(),
            key=lambda trade: float(trade.get("timestamp") or 0.0),
        )[-self.trade_history_limit:]
        self.live_trades[stream_id] = {"data": fresh, "updated_at": now}

    @staticmethod
    def _guard_gate_retired_orderbook_callback(exchange: Any) -> Any:
        if getattr(exchange, "_wfh_gate_retired_orderbook_guarded", False):
            return exchange
        original = getattr(exchange, "handle_order_book", None)
        if not callable(original):
            return exchange

        def guarded(client: Any, message: object) -> Any:
            try:
                return original(client, message)
            except KeyError as exc:
                key = exc.args[0] if len(exc.args) == 1 else None
                subscriptions = getattr(client, "subscriptions", None)
                if (
                    isinstance(key, str)
                    and key.startswith("orderbook:")
                    and isinstance(subscriptions, dict)
                    and key not in subscriptions
                ):
                    logger.debug(
                        "Dropping late Gate.io orderbook message for retired subscription %s",
                        key,
                    )
                    return None
                raise

        exchange.handle_order_book = guarded
        exchange._wfh_gate_retired_orderbook_guarded = True
        return exchange

    @staticmethod
    def _new_exchange(ex_name: str) -> Any:
        if ccxt_pro is None:
            raise RuntimeError("CCXT Pro is not installed; WebSocket streaming is unavailable")
        ccxt_id = MultiExchangeGateway._ccxt_exchange_id(ex_name)
        ex_class = getattr(ccxt_pro, ccxt_id)
        exchange = ex_class({
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        if ex_name == "gateio":
            return WebSocketManager._guard_gate_retired_orderbook_callback(exchange)
        return exchange

    def _direct_venue_lock(self, ex_name: str) -> asyncio.Lock:
        return self._direct_venue_locks.setdefault(ex_name, asyncio.Lock())

    async def _get_exchange(self, ex_name: str) -> Any:
        if ex_name not in self.exchanges:
            self.exchanges[ex_name] = self._new_exchange(ex_name)
        return self.exchanges[ex_name]

    async def _direct_exchange_for_start(
        self, ex_name: str, symbol: str, stream_id: str
    ) -> Any | None:
        await self._await_direct_symbol_retirement(ex_name, symbol)
        if stream_id not in self.active_tasks:
            return None
        async with self._direct_venue_lock(ex_name):
            if stream_id not in self.active_tasks:
                return None
            return await self._get_exchange(ex_name)

    async def _get_liquidation_exchange(self, ex_name: str, symbol: str) -> Any:
        stream_id = f"{ex_name}:{symbol}"
        while True:
            await self._await_liquidation_venue_retirements(ex_name)
            await self._await_exchange_close_finalizers(ex_name)
            async with self._lock:
                if self._liquidation_venue_retire_tasks(ex_name):
                    continue
                if self._exchange_close_finalizers_for_venue(ex_name):
                    continue
                if stream_id not in self.liquidation_exchanges:
                    self.liquidation_exchanges[stream_id] = self._new_exchange(ex_name)
                return self.liquidation_exchanges[stream_id]

    async def _get_shared_liquidation_exchange(self, ex_name: str) -> Any:
        async with self._lock:
            if ex_name not in self.shared_liquidation_exchanges:
                self.shared_liquidation_exchanges[ex_name] = self._new_exchange(ex_name)
            return self.shared_liquidation_exchanges[ex_name]

    async def watch_orderbook_stream(self, ex_name: str, symbol: str, limit: int = 20):
        """مصرف‌کننده (Consumer) دائمی با پایداری سطح نهادی (Institutional)"""
        stream_id = f"{ex_name}:{symbol}"

        async with self._lock:
            if stream_id not in self.circuit_breakers:
                self.circuit_breakers[stream_id] = CircuitBreaker()
            self.message_counters[stream_id] = 0

        exchange = await self._direct_exchange_for_start(
            ex_name, symbol, stream_id
        )
        if exchange is None:
            return
        cb = self.circuit_breakers[stream_id]
        retry_delay = 1.0

        logger.info(f"🟢 [WS START] Subscribed to {stream_id}")

        while stream_id in self.active_tasks:
            if not cb.can_try():
                await asyncio.sleep(1.0)
                continue

            try:
                # برای کوکوین و بای‌بیت محدودیت‌های اوردربوک متفاوت است
                safe_limit = 50 if ex_name in ['bybit', 'kucoin'] else limit
                orderbook = await exchange.watch_order_book(symbol, limit=safe_limit)

                # Preserve an immutable bounded history because CCXT Pro may
                # mutate its live OrderBook object in place between updates.
                self._ingest_orderbook(
                    ex_name, symbol, orderbook, received_at=time.time()
                )

                self.message_counters[stream_id] += 1
                cb.record_success()
                retry_delay = 1.0  # ریست کردن بک‌آف در صورت موفقیت

            except Exception as e:
                cb.record_failure()

                if cb.state == "OPEN":
                    logger.error(f"🛑 [CIRCUIT BREAKER OPEN] for {stream_id}. Network unstable. Pausing for {cb.recovery_timeout}s.")
                    await asyncio.sleep(cb.recovery_timeout)
                else:
                    # Exponential Backoff with Jitter (جلوگیری از حملات Throttling)
                    jitter = random.uniform(0.1, 0.5)
                    sleep_time = retry_delay + jitter
                    logger.warning(f"⚠️ [WS ERROR] {stream_id} disconnected: {str(e)[:60]}. Reconnecting in {sleep_time:.2f}s...")

                    await asyncio.sleep(sleep_time)
                    retry_delay = min(retry_delay * 2, 15.0)

        logger.info(f"🛑 [WS STOP] Stream terminated for {stream_id}")

    async def _watch_stream(self, ex_name: str, symbol: str, kind: str):
        stream_id = f"{ex_name}:{symbol}:{kind}"
        exchange = await self._direct_exchange_for_start(
            ex_name, symbol, stream_id
        )
        if exchange is None:
            return
        breaker = self.circuit_breakers.setdefault(stream_id, CircuitBreaker())
        delay = 1.0
        cache = self.live_tickers if kind == "ticker" else self.live_trades
        while stream_id in self.active_tasks:
            if not breaker.can_try():
                await asyncio.sleep(1.0)
                continue
            try:
                data = await getattr(exchange, f"watch_{kind}")(symbol)
                received_at = time.time()
                if kind == "trades":
                    self._ingest_trades(
                        ex_name, symbol, data, received_at=received_at
                    )
                else:
                    cache[f"{ex_name}:{symbol}"] = {
                        "data": copy.deepcopy(data),
                        "updated_at": received_at,
                    }
                self.message_counters[stream_id] = self.message_counters.get(stream_id, 0) + 1
                breaker.record_success()
                delay = 1.0
            except Exception:
                breaker.record_failure()
                pause = cb_recovery(breaker, delay)
                await asyncio.sleep(pause)
                delay = min(delay * 2, 15.0)

    @staticmethod
    def _positive_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    @classmethod
    def _normalized_liquidation(cls, row: Any) -> Dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        timestamp = cls._positive_number(row.get("timestamp"))
        side = str(row.get("side") or "").lower()
        if timestamp is None or side not in {"buy", "sell"}:
            return None
        quote_value = cls._positive_number(row.get("quoteValue"))
        if quote_value is None:
            contracts = cls._positive_number(row.get("contracts"))
            contract_size = cls._positive_number(row.get("contractSize"))
            price = cls._positive_number(row.get("price"))
            if contracts is None or contract_size is None or price is None:
                return None
            quote_value = contracts * contract_size * price
        if not math.isfinite(quote_value) or quote_value <= 0:
            return None
        return {
            "timestamp": int(timestamp),
            "side": side,
            "notional_usd": float(quote_value),
        }

    def _ingest_liquidations(
        self, ex_name: str, symbol: str, rows: Any, *, received_at: float | None = None
    ) -> None:
        now = time.time() if received_at is None else float(received_at)
        if not math.isfinite(now) or now < 0:
            return
        stream_id = f"{ex_name}:{symbol}"
        existing = self.live_liquidations.get(stream_id, {}).get("events", [])
        cutoff_ms = (now - self.liquidation_retention_seconds) * 1000.0
        events = [
            event for event in existing
            if isinstance(event, dict) and float(event.get("timestamp", 0)) >= cutoff_ms
        ]
        seen = {
            (int(event["timestamp"]), str(event["side"]), float(event["notional_usd"]))
            for event in events
            if {"timestamp", "side", "notional_usd"} <= set(event)
        }
        iterable = rows if isinstance(rows, list) else [rows]
        for row in iterable:
            event = self._normalized_liquidation(row)
            if event is None or event["timestamp"] < cutoff_ms:
                continue
            fingerprint = (
                int(event["timestamp"]), str(event["side"]), float(event["notional_usd"])
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            events.append(event)
        events.sort(key=lambda item: int(item["timestamp"]))
        self.live_liquidations[stream_id] = {
            "events": events[-2000:],
            "updated_at": now,
        }

    @staticmethod
    def _shared_liquidation_watcher(exchange: Any):
        watch = getattr(exchange, "watch_liquidations_for_symbols", None)
        supports = getattr(exchange, "has", {}).get("watchLiquidationsForSymbols")
        return watch if callable(watch) and supports is True else None

    @staticmethod
    def _group_subscribed_liquidations(
        rows: Any, subscribers: set[str]
    ) -> Dict[str, list[Dict[str, Any]]]:
        grouped: Dict[str, list[Dict[str, Any]]] = {}
        iterable = rows if isinstance(rows, list) else [rows]
        for row in iterable:
            if not isinstance(row, dict):
                continue
            symbol = row.get("symbol")
            if isinstance(symbol, str) and symbol in subscribers:
                grouped.setdefault(symbol, []).append(row)
        return grouped

    async def _consume_shared_liquidations(
        self, ex_name: str, watch: Any
    ) -> None:
        # An empty symbol list selects Binance's official all-market
        # !forceOrder@arr feed. Route only lifecycle-requested symbols.
        rows = await watch([], limit=1000)
        subscribers = set(self.liquidation_subscribers.get(ex_name, ()))
        grouped = self._group_subscribed_liquidations(rows, subscribers)
        received_at = time.time()
        for symbol, symbol_rows in grouped.items():
            self._ingest_liquidations(
                ex_name, symbol, symbol_rows, received_at=received_at
            )

    async def _watch_shared_liquidations_stream(self, ex_name: str) -> None:
        """Consume one exchange-wide liquidation stream and route subscribed symbols."""
        task_id = f"{ex_name}:liquidations"
        try:
            await self._await_shared_liquidation_retirement(ex_name)
            exchange = await self._get_shared_liquidation_exchange(ex_name)
            watch = self._shared_liquidation_watcher(exchange)
            if watch is None:
                self.unsupported_liquidation_exchanges.add(ex_name)
                self.liquidation_subscribers.pop(ex_name, None)
                logger.info("Shared liquidation stream unavailable for %s", ex_name)
                return
            breaker = self.circuit_breakers.setdefault(task_id, CircuitBreaker())
            delay = 1.0
            while task_id in self.active_tasks:
                if not self.liquidation_subscribers.get(ex_name):
                    await asyncio.sleep(0.25)
                    continue
                if not breaker.can_try():
                    await asyncio.sleep(1.0)
                    continue
                try:
                    await self._consume_shared_liquidations(ex_name, watch)
                    self.message_counters[task_id] = self.message_counters.get(task_id, 0) + 1
                    breaker.record_success()
                    delay = 1.0
                except Exception as exc:
                    breaker.record_failure()
                    logger.debug(
                        "Shared liquidation stream unavailable for %s: %s",
                        ex_name, type(exc).__name__,
                    )
                    await asyncio.sleep(cb_recovery(breaker, delay))
                    delay = min(delay * 2, 15.0)
        finally:
            current_task = asyncio.current_task()
            if self.active_tasks.get(task_id) is current_task:
                self.active_tasks.pop(task_id, None)
            if task_id not in self.active_tasks:
                self._schedule_shared_liquidation_retire(ex_name)

    async def _watch_liquidations_stream(self, ex_name: str, symbol: str) -> None:
        task_id = f"{ex_name}:{symbol}:liquidations"
        try:
            exchange = await self._get_liquidation_exchange(ex_name, symbol)
            watch = getattr(exchange, "watch_liquidations", None)
            supports = getattr(exchange, "has", {}).get("watchLiquidations")
            if not callable(watch) or supports not in {True, "emulated"}:
                self.unsupported_liquidation_exchanges.add(ex_name)
                logger.info("Liquidation stream unavailable for %s:%s", ex_name, symbol)
                return
            breaker = self.circuit_breakers.setdefault(task_id, CircuitBreaker())
            delay = 1.0
            while task_id in self.active_tasks:
                if not breaker.can_try():
                    await asyncio.sleep(1.0)
                    continue
                try:
                    rows = await watch(symbol, limit=1000)
                    self._ingest_liquidations(ex_name, symbol, rows, received_at=time.time())
                    self.message_counters[task_id] = self.message_counters.get(task_id, 0) + 1
                    breaker.record_success()
                    delay = 1.0
                except Exception as exc:
                    breaker.record_failure()
                    logger.debug(
                        "Liquidation stream unavailable for %s:%s: %s",
                        ex_name, symbol, type(exc).__name__,
                    )
                    await asyncio.sleep(cb_recovery(breaker, delay))
                    delay = min(delay * 2, 15.0)
        finally:
            current_task = asyncio.current_task()
            if self.active_tasks.get(task_id) is current_task:
                self.active_tasks.pop(task_id, None)
            if task_id not in self.active_tasks:
                self._schedule_liquidation_exchange_retire(ex_name, symbol)

    def get_realtime_liquidation_flow(
        self, ex_name: str, symbol: str, *, now: float | None = None
    ) -> Optional[Dict[str, Any]]:
        observed_now = time.time() if now is None else float(now)
        if not math.isfinite(observed_now) or observed_now < 0:
            return None
        entry = self.live_liquidations.get(f"{ex_name}:{symbol}")
        events = entry.get("events") if isinstance(entry, dict) else None
        if not isinstance(events, list):
            return None
        current: list[Dict[str, Any]] = []
        baseline: list[Dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            timestamp = self._positive_number(event.get("timestamp"))
            notional = self._positive_number(event.get("notional_usd"))
            side = str(event.get("side") or "").lower()
            if timestamp is None or notional is None or side not in {"buy", "sell"}:
                continue
            age_seconds = observed_now - timestamp / 1000.0
            if 0 <= age_seconds < self.liquidation_window_seconds:
                current.append(event)
            elif self.liquidation_window_seconds <= age_seconds < self.liquidation_retention_seconds:
                baseline.append(event)
        if not current:
            return None
        long_notional = sum(float(event["notional_usd"]) for event in current if event["side"] == "sell")
        short_notional = sum(float(event["notional_usd"]) for event in current if event["side"] == "buy")
        current_total = long_notional + short_notional
        baseline_total = sum(float(event["notional_usd"]) for event in baseline)
        burst_ratio = current_total / baseline_total if baseline_total > 0 else 1.0
        latest_timestamp = max(int(event["timestamp"]) for event in current)
        return {
            "available": True,
            "source_exchange": ex_name,
            "mapped_symbol": symbol,
            "observed_at": latest_timestamp / 1000.0,
            "long_liquidation_notional_1m": round(long_notional, 6),
            "short_liquidation_notional_1m": round(short_notional, 6),
            "liquidation_velocity_usd_per_min": round(current_total, 6),
            "burst_ratio": round(burst_ratio, 6),
            "sample_count_1m": len(current),
            "baseline_notional_1m": round(baseline_total, 6),
        }

    @staticmethod
    def _shared_evidence_capable(exchange: Any) -> bool:
        # Shared evidence retires an entire transport generation with close();
        # it never mutates a live membership via unwatch. Requiring unwatch
        # support here was a stale contract from the pre-generation design and
        # unnecessarily forced otherwise capable venues back to REST.
        required = (
            ("watchOrderBookForSymbols", "watch_order_book_for_symbols"),
            ("watchTradesForSymbols", "watch_trades_for_symbols"),
            ("watchTickers", "watch_tickers"),
        )
        capabilities = getattr(exchange, "has", {})
        return all(
            capabilities.get(flag) is True and callable(getattr(exchange, method, None))
            for flag, method in required
        )

    @staticmethod
    def _shared_payload_rows(payload: Any) -> list[Dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        if isinstance(payload.get("symbol"), str):
            return [payload]
        return [row for row in payload.values() if isinstance(row, dict)]

    def _ingest_shared_orderbook_update(
        self,
        ex_name: str,
        payload: Any,
        *,
        allowed_symbols: set[str],
        received_at: float,
    ) -> None:
        symbol = payload.get("symbol") if isinstance(payload, dict) else None
        if isinstance(symbol, str) and symbol in allowed_symbols:
            self._ingest_orderbook(ex_name, symbol, payload, received_at=received_at)

    def _ingest_shared_trade_update(
        self,
        ex_name: str,
        payload: Any,
        *,
        allowed_symbols: set[str],
        received_at: float,
    ) -> None:
        grouped: Dict[str, list[Dict[str, Any]]] = {}
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            symbol = row.get("symbol") if isinstance(row, dict) else None
            if isinstance(symbol, str) and symbol in allowed_symbols:
                grouped.setdefault(symbol, []).append(row)
        for symbol, symbol_rows in grouped.items():
            self._ingest_trades(ex_name, symbol, symbol_rows, received_at=received_at)

    def _ingest_shared_ticker_update(
        self,
        ex_name: str,
        payload: Any,
        *,
        allowed_symbols: set[str],
        received_at: float,
    ) -> None:
        for row in self._shared_payload_rows(payload):
            symbol = row.get("symbol")
            if isinstance(symbol, str) and symbol in allowed_symbols:
                self.live_tickers[f"{ex_name}:{symbol}"] = {
                    "data": copy.deepcopy(row),
                    "updated_at": float(received_at),
                }

    def _ingest_shared_evidence_update(
        self,
        ex_name: str,
        kind: str,
        payload: Any,
        *,
        allowed_symbols: set[str],
        received_at: float,
    ) -> None:
        if not allowed_symbols:
            return
        handlers = {
            "orderbook": self._ingest_shared_orderbook_update,
            "trades": self._ingest_shared_trade_update,
            "ticker": self._ingest_shared_ticker_update,
        }
        handler = handlers.get(kind)
        if handler is not None:
            handler(
                ex_name,
                payload,
                allowed_symbols=allowed_symbols,
                received_at=received_at,
            )

    @staticmethod
    def _shared_evidence_methods(exchange: Any, kind: str) -> tuple[Any, Any]:
        methods = {
            "orderbook": (
                "watch_order_book_for_symbols", "un_watch_order_book_for_symbols"
            ),
            "trades": ("watch_trades_for_symbols", "un_watch_trades_for_symbols"),
            "ticker": ("watch_tickers", "un_watch_tickers"),
        }
        watch_name, unwatch_name = methods[kind]
        return getattr(exchange, watch_name), getattr(exchange, unwatch_name, None)

    def _shared_evidence_symbols(self, ex_name: str) -> tuple[str, ...]:
        return tuple(sorted(self.shared_evidence_subscribers.get(ex_name, ())))

    @staticmethod
    def _purge_exchange_symbol_state(exchange: Any | None, symbols: tuple[str, ...]) -> None:
        if exchange is None or not symbols:
            return
        for attribute in ("orderbooks", "trades", "tickers", "bidsasks"):
            cache = getattr(exchange, attribute, None)
            if not isinstance(cache, dict):
                continue
            for symbol in symbols:
                cache.pop(symbol, None)

    @staticmethod
    async def _watch_shared_evidence_payload(
        watch: Any,
        *,
        ex_name: str,
        kind: str,
        symbols: tuple[str, ...],
    ) -> Any:
        if kind == "orderbook":
            safe_limit = 50 if ex_name in {"bybit", "kucoin"} else 20
            return await watch(list(symbols), limit=safe_limit)
        if kind == "trades":
            return await watch(list(symbols), limit=500)
        return await watch(list(symbols))

    @staticmethod
    def _shared_evidence_task_ids(ex_name: str) -> tuple[str, str, str]:
        return (
            f"shared-evidence:{ex_name}:orderbook",
            f"shared-evidence:{ex_name}:trades",
            f"shared-evidence:{ex_name}:ticker",
        )

    def _shared_evidence_task_count(self, ex_name: str) -> int:
        return sum(
            1
            for task_id in self._shared_evidence_task_ids(ex_name)
            if task_id in self.active_tasks and not self.active_tasks[task_id].done()
        )

    async def _watch_shared_evidence_stream(
        self,
        ex_name: str,
        kind: str,
        exchange: Any,
        symbols: tuple[str, ...],
        generation: int,
    ) -> None:
        task_id = f"shared-evidence:{ex_name}:{kind}"
        watch, _ = self._shared_evidence_methods(exchange, kind)
        breaker = self.circuit_breakers.setdefault(task_id, CircuitBreaker())
        delay = 1.0
        try:
            while (
                self.active_tasks.get(task_id) is asyncio.current_task()
                and self.shared_evidence_generation.get(ex_name) == generation
            ):
                if not breaker.can_try():
                    await asyncio.sleep(1.0)
                    continue
                try:
                    payload = await self._watch_shared_evidence_payload(
                        watch,
                        ex_name=ex_name,
                        kind=kind,
                        symbols=symbols,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    breaker.record_failure()
                    logger.debug(
                        "Shared evidence stream unavailable for %s:%s generation=%d: %s",
                        ex_name,
                        kind,
                        generation,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(cb_recovery(breaker, delay))
                    delay = min(delay * 2, 15.0)
                    continue
                desired_now = set(self.shared_evidence_subscribers.get(ex_name, ()))
                allowed_symbols = set(symbols).intersection(desired_now)
                self._ingest_shared_evidence_update(
                    ex_name,
                    kind,
                    payload,
                    allowed_symbols=allowed_symbols,
                    received_at=time.time(),
                )
                self.message_counters[task_id] = self.message_counters.get(task_id, 0) + 1
                breaker.record_success()
                delay = 1.0
        finally:
            current_task = asyncio.current_task()
            if self.active_tasks.get(task_id) is current_task:
                self.active_tasks.pop(task_id, None)

    def _cancel_shared_evidence_generation_tasks(
        self, ex_name: str
    ) -> tuple[asyncio.Task, ...]:
        cancelled: list[asyncio.Task] = []
        for task_id in self._shared_evidence_task_ids(ex_name):
            task = self.active_tasks.pop(task_id, None)
            if task is None or task is asyncio.current_task():
                continue
            task.cancel()
            cancelled.append(task)
        return tuple(cancelled)

    async def _settle_shared_evidence_generation_tasks(
        self,
        tasks: tuple[asyncio.Task, ...],
        *,
        context: str,
    ) -> tuple[asyncio.Task, ...]:
        if not tasks:
            return ()
        done, pending = await asyncio.wait(
            tasks, timeout=self.retirement_timeout_seconds
        )
        for task in done:
            self._consume_settled_task_result(task)
        if pending:
            logger.warning(
                "Shared evidence task retirement timed out for %s: pending=%d",
                context,
                len(pending),
            )
        return tuple(pending)

    @staticmethod
    def _shared_evidence_transport_counts(exchange: Any | None) -> tuple[int | None, int | None]:
        if exchange is None:
            return 0, 0
        clients = getattr(exchange, "clients", None)
        if not isinstance(clients, dict):
            return None, None
        subscriptions = 0
        for client in clients.values():
            client_subscriptions = getattr(client, "subscriptions", None)
            if hasattr(client_subscriptions, "__len__"):
                subscriptions += len(client_subscriptions)
        return len(clients), subscriptions

    def _finish_abandoned_exchange_close(self, ex_name: str, exchange: Any) -> None:
        """Best-effort background close for a transport that already timed out.

        The retired generation is detached from the pool at this point, so the
        abandoned instance only needs its websocket transport released. The
        close runs with a generous timeout and never blocks reconciliation.
        """

        async def _finish() -> None:
            try:
                await asyncio.wait_for(
                    exchange.close(),
                    timeout=self.exchange_close_timeout_seconds * 4,
                )
                logger.info(
                    "Abandoned shared evidence exchange closed for %s",
                    ex_name,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(
                    "Abandoned shared evidence exchange close still unfinished "
                    "for %s: %s",
                    ex_name,
                    type(exc).__name__,
                )

        task = asyncio.create_task(_finish())
        self._abandoned_close_tasks.add(task)
        task.add_done_callback(self._abandoned_close_tasks.discard)

    async def _retire_shared_evidence_generation(
        self, ex_name: str
    ) -> tuple[bool, Any | None]:
        exchange = self.shared_evidence_exchanges.get(ex_name)
        cancelled = self._cancel_shared_evidence_generation_tasks(ex_name)
        pending = await self._settle_shared_evidence_generation_tasks(
            cancelled, context=ex_name
        )

        close_abandoned = False
        close_ok = True
        if exchange is not None:
            try:
                await asyncio.wait_for(
                    exchange.close(), timeout=self.exchange_close_timeout_seconds
                )
            except asyncio.TimeoutError:
                # A hung CCXT websocket close must not suppress the replacement
                # generation indefinitely: a dead evidence pool blocks live
                # decision data for every subscriber. The instance is detached
                # below and the transport close finishes in the background.
                close_abandoned = True
                logger.warning(
                    "Shared evidence exchange close timed out for %s; "
                    "abandoning instance and completing retirement",
                    ex_name,
                )
            except Exception as exc:
                close_ok = False
                logger.warning(
                    "Shared evidence exchange close failed for %s: %s",
                    ex_name,
                    type(exc).__name__,
                )

        if pending:
            done_after_close, pending_after_close = await asyncio.wait(
                pending, timeout=self.retirement_timeout_seconds
            )
            for task in done_after_close:
                self._consume_settled_task_result(task)
            pending = tuple(pending_after_close)

        clients, subscriptions = self._shared_evidence_transport_counts(exchange)
        transport_empty = (
            clients is None
            or subscriptions is None
            or (clients == 0 and subscriptions == 0)
        )
        retirement_complete = (
            not pending and (close_ok or close_abandoned)
        )
        transport_acceptable = transport_empty or close_abandoned
        if retirement_complete and transport_acceptable:
            if self.shared_evidence_exchanges.get(ex_name) is exchange:
                self.shared_evidence_exchanges.pop(ex_name, None)
            self.shared_evidence_active_symbols.pop(ex_name, None)
            self._shared_evidence_generation_started_at.pop(ex_name, None)
            self.shared_evidence_blocked_exchanges.discard(ex_name)
            if close_abandoned:
                self._finish_abandoned_exchange_close(ex_name, exchange)
            return True, exchange

        self.shared_evidence_retirement_failures[ex_name] = (
            self.shared_evidence_retirement_failures.get(ex_name, 0) + 1
        )
        self.shared_evidence_blocked_exchanges.add(ex_name)
        logger.error(
            "Shared evidence generation retirement incomplete for %s; "
            "replacement generation suppressed pending=%d clients=%s subscriptions=%s",
            ex_name,
            len(pending),
            clients,
            subscriptions,
        )
        return False, None

    async def _start_shared_evidence_generation(
        self,
        ex_name: str,
        symbols: tuple[str, ...],
        *,
        market_source: Any | None = None,
    ) -> bool:
        if not symbols:
            return True
        exchange = self._new_exchange(ex_name)

        if market_source is not None and getattr(market_source, "markets", None):
            share_markets = getattr(exchange, "set_markets_from_exchange", None)
            if callable(share_markets):
                try:
                    result = share_markets(market_source)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:
                    logger.debug(
                        "Shared evidence static market handoff failed for %s: %s",
                        ex_name,
                        type(exc).__name__,
                    )

        if not self._shared_evidence_capable(exchange):
            self.unsupported_shared_evidence_exchanges.add(ex_name)
            self.shared_evidence_subscribers.pop(ex_name, None)
            try:
                await asyncio.wait_for(
                    exchange.close(), timeout=self.exchange_close_timeout_seconds
                )
            except Exception:
                pass
            for symbol in symbols:
                self._subscribe_shared_evidence_direct_fallback(ex_name, symbol)
            logger.info(
                "Shared evidence pool unsupported for %s; using direct evidence fallback",
                ex_name,
            )
            return False

        generation = self.shared_evidence_generation.get(ex_name, 0) + 1
        self.shared_evidence_generation[ex_name] = generation
        self.shared_evidence_exchanges[ex_name] = exchange
        self.shared_evidence_active_symbols[ex_name] = symbols
        self._shared_evidence_generation_started_at[ex_name] = time.monotonic()
        self.shared_evidence_blocked_exchanges.discard(ex_name)
        for kind in ("orderbook", "trades", "ticker"):
            task_id = f"shared-evidence:{ex_name}:{kind}"
            self.active_tasks[task_id] = asyncio.create_task(
                self._watch_shared_evidence_stream(
                    ex_name, kind, exchange, symbols, generation
                )
            )
        return True

    def _shared_evidence_generation_state(
        self, ex_name: str
    ) -> tuple[tuple[str, ...], tuple[str, ...], Any | None, int]:
        return (
            self._shared_evidence_symbols(ex_name),
            self.shared_evidence_active_symbols.get(ex_name, ()),
            self.shared_evidence_exchanges.get(ex_name),
            self._shared_evidence_task_count(ex_name),
        )

    async def _reconcile_shared_evidence_once(
        self, ex_name: str, market_source: Any | None
    ) -> tuple[bool, Any | None]:
        desired, active, exchange, task_count = self._shared_evidence_generation_state(
            ex_name
        )
        if not desired and exchange is None and task_count == 0:
            self.shared_evidence_active_symbols.pop(ex_name, None)
            return False, market_source
        generation_current = (
            bool(desired)
            and exchange is not None
            and desired == active
            and task_count == 3
        )
        if generation_current:
            return False, market_source

        generation_exists = exchange is not None or bool(active) or task_count > 0
        if generation_exists:
            retired, market_source = await self._retire_shared_evidence_generation(ex_name)
            if not retired:
                return False, market_source

        desired = self._shared_evidence_symbols(ex_name)
        if not desired or ex_name in self.unsupported_shared_evidence_exchanges:
            return False, market_source
        started = await self._start_shared_evidence_generation(
            ex_name, desired, market_source=market_source
        )
        if not started:
            return False, market_source
        return self._shared_evidence_symbols(ex_name) != desired, market_source

    async def _reconcile_shared_evidence_exchange(self, ex_name: str) -> None:
        lock = self._shared_evidence_reconcile_locks.setdefault(
            ex_name, asyncio.Lock()
        )
        async with lock:
            market_source: Any | None = None
            repeat = True
            while repeat:
                repeat, market_source = await self._reconcile_shared_evidence_once(
                    ex_name, market_source
                )

    def _shared_evidence_reconcile_delay(self, ex_name: str) -> float:
        desired, active, exchange, task_count = self._shared_evidence_generation_state(
            ex_name
        )
        # Initial creation, full retirement, and repair of an incomplete
        # generation are safety/liveness operations and must not be batched.
        if not desired or exchange is None or not active or task_count != 3:
            return 0.0
        if desired == active:
            return 0.0
        started_at = self._shared_evidence_generation_started_at.get(ex_name)
        if started_at is None:
            return 0.0
        interval = max(
            0.0, float(self.shared_evidence_reconcile_min_interval_seconds)
        )
        return max(0.0, started_at + interval - time.monotonic())

    async def _run_shared_evidence_reconciler(self, ex_name: str) -> None:
        event = self._shared_evidence_reconcile_events.setdefault(
            ex_name, asyncio.Event()
        )
        while ex_name in self._shared_evidence_reconcile_dirty:
            self._shared_evidence_reconcile_dirty.discard(ex_name)
            while True:
                event.clear()
                delay = self._shared_evidence_reconcile_delay(ex_name)
                if delay <= 0.0:
                    break
                # A request can arrive between event.clear() and this check.
                # The dirty marker closes that lost-wakeup window.
                if ex_name in self._shared_evidence_reconcile_dirty:
                    # Consume the wakeup before recomputing the delay. Leaving
                    # the marker set turns this branch into a tight no-await loop
                    # for the remainder of the batching window.
                    self._shared_evidence_reconcile_dirty.discard(ex_name)
                    continue
                try:
                    await asyncio.wait_for(event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    break
            await self._reconcile_shared_evidence_exchange(ex_name)

    def _schedule_shared_evidence_reconcile(self, ex_name: str) -> None:
        self._shared_evidence_reconcile_dirty.add(ex_name)
        event = self._shared_evidence_reconcile_events.get(ex_name)
        if event is not None:
            event.set()
        task = self._shared_evidence_reconcile_tasks.get(ex_name)
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self._run_shared_evidence_reconciler(ex_name))
        self._shared_evidence_reconcile_tasks[ex_name] = task

        def _done(completed: asyncio.Task) -> None:
            if self._shared_evidence_reconcile_tasks.get(ex_name) is completed:
                self._shared_evidence_reconcile_tasks.pop(ex_name, None)
            self._consume_settled_task_result(completed)
            if ex_name in self._shared_evidence_reconcile_dirty:
                self._schedule_shared_evidence_reconcile(ex_name)

        task.add_done_callback(_done)

    @staticmethod
    def _shared_evidence_fallback_key(ex_name: str, symbol: str) -> str:
        return f"{ex_name}:{symbol}"

    def has_shared_evidence_direct_fallback(self, ex_name: str, symbol: str) -> bool:
        return self._shared_evidence_fallback_key(ex_name, symbol) in self.shared_evidence_direct_fallbacks

    def _subscribe_shared_evidence_direct_fallback(self, ex_name: str, symbol: str) -> bool:
        key = self._shared_evidence_fallback_key(ex_name, symbol)
        if key not in self.shared_evidence_direct_fallbacks:
            self.shared_evidence_direct_fallbacks.add(key)
            self.subscribe_direct_evidence(ex_name, symbol)
        return True

    def subscribe_shared_evidence(self, ex_name: str, symbol: str) -> bool:
        if ccxt_pro is None:
            return False
        if ex_name in self.unsupported_shared_evidence_exchanges:
            return self._subscribe_shared_evidence_direct_fallback(ex_name, symbol)
        subscribers = self.shared_evidence_subscribers.setdefault(ex_name, set())
        if symbol not in subscribers and len(subscribers) >= self.shared_evidence_symbol_limit:
            logger.warning(
                "Shared evidence subscriber limit reached for %s; REST fallback remains active",
                ex_name,
            )
            return False
        added = symbol not in subscribers
        subscribers.add(symbol)
        if added or self._shared_evidence_task_count(ex_name) != 3:
            self._schedule_shared_evidence_reconcile(ex_name)
        return True

    def unsubscribe_shared_evidence(self, ex_name: str, symbol: str) -> None:
        fallback_key = self._shared_evidence_fallback_key(ex_name, symbol)
        if fallback_key in self.shared_evidence_direct_fallbacks:
            self.shared_evidence_direct_fallbacks.discard(fallback_key)
            self.unsubscribe(ex_name, symbol)
            return
        subscribers = self.shared_evidence_subscribers.get(ex_name)
        if subscribers is None:
            return
        removed = symbol in subscribers
        subscribers.discard(symbol)
        if not subscribers:
            self.shared_evidence_subscribers.pop(ex_name, None)
        if removed:
            self._schedule_shared_evidence_reconcile(ex_name)

    def has_direct_evidence_subscription(self, ex_name: str, symbol: str) -> bool:
        stream_id = f"{ex_name}:{symbol}"
        return any(
            task_id == stream_id or task_id in {f"{stream_id}:ticker", f"{stream_id}:trades"}
            for task_id in self.active_tasks
        )

    def subscribe_liquidations(self, ex_name: str, symbol: str):
        """Start bounded liquidation evidence acquisition for early PRE-TRIGGER state."""
        if ccxt_pro is None:
            logger.warning(
                "CCXT Pro is unavailable; skipping liquidation subscription for %s:%s",
                ex_name,
                symbol,
            )
            return
        if ex_name in self.unsupported_liquidation_exchanges:
            return
        if ex_name == "binance":
            self.liquidation_subscribers.setdefault(ex_name, set()).add(symbol)
            liquidation_id = f"{ex_name}:liquidations"
            if liquidation_id not in self.active_tasks:
                self.active_tasks[liquidation_id] = asyncio.create_task(
                    self._watch_shared_liquidations_stream(ex_name)
                )
            return
        liquidation_id = f"{ex_name}:{symbol}:liquidations"
        if liquidation_id not in self.active_tasks:
            self.active_tasks[liquidation_id] = asyncio.create_task(
                self._watch_liquidations_stream(ex_name, symbol)
            )

    def refresh_liquidation_capability(self, ex_name: str | None = None) -> None:
        """Forget cached unsupported capability after an explicit refresh request."""
        if ex_name is None:
            self.unsupported_liquidation_exchanges.clear()
        else:
            self.unsupported_liquidation_exchanges.discard(ex_name)

    @staticmethod
    def _ccxt_client_is_idle(client: Any) -> bool:
        subscriptions = getattr(client, "subscriptions", None)
        futures = getattr(client, "futures", None)
        return (
            hasattr(subscriptions, "__len__")
            and len(subscriptions) == 0
            and hasattr(futures, "__len__")
            and len(futures) == 0
        )

    @classmethod
    def _ccxt_transport_counts(cls, exchanges: tuple[Any, ...]) -> tuple[int, int, int]:
        client_count = 0
        subscription_count = 0
        idle_client_count = 0
        seen_exchanges: set[int] = set()
        for exchange in exchanges:
            identity = id(exchange)
            if identity in seen_exchanges:
                continue
            seen_exchanges.add(identity)
            clients = getattr(exchange, "clients", None)
            if not isinstance(clients, dict):
                continue
            client_count += len(clients)
            for client in clients.values():
                subscriptions = getattr(client, "subscriptions", None)
                if hasattr(subscriptions, "__len__"):
                    subscription_count += len(subscriptions)
                if cls._ccxt_client_is_idle(client):
                    idle_client_count += 1
        return client_count, subscription_count, idle_client_count

    async def _close_idle_ccxt_clients_locked(self, ex_name: str, exchange: Any) -> None:
        clients = getattr(exchange, "clients", None)
        if not isinstance(clients, dict):
            return
        for url, client in list(clients.items()):
            if not self._ccxt_client_is_idle(client):
                continue
            close = getattr(client, "close", None)
            try:
                if callable(close):
                    result = close()
                    if asyncio.iscoroutine(result):
                        await asyncio.wait_for(result, timeout=5.0)
            except Exception as exc:
                logger.debug(
                    "Idle CCXT client close failed for %s:%s: %s",
                    ex_name,
                    url,
                    type(exc).__name__,
                )
                continue
            if clients.get(url) is client:
                clients.pop(url, None)

    async def _close_idle_ccxt_clients(self, ex_name: str, exchange: Any) -> None:
        async with self._direct_venue_lock(ex_name):
            await self._close_idle_ccxt_clients_locked(ex_name, exchange)

    @staticmethod
    def _consume_settled_task_result(task: asyncio.Task) -> None:
        try:
            task.result()
        except (asyncio.CancelledError, Exception):
            pass

    async def _settle_cancelled_tasks(
        self, cancelled_tasks: tuple[asyncio.Task, ...], *, context: str
    ) -> None:
        if not cancelled_tasks:
            return
        done, pending = await asyncio.wait(
            cancelled_tasks, timeout=self.retirement_timeout_seconds
        )
        for task in done:
            self._consume_settled_task_result(task)
        if pending:
            logger.warning(
                "WebSocket cancelled-task retirement timed out for %s: pending=%d",
                context,
                len(pending),
            )
            for task in pending:
                task.add_done_callback(self._consume_settled_task_result)
                task.cancel()

    async def _mexc_unwatch_direct_stream(
        self, exchange: Any, symbol: str, method_name: str
    ) -> None:
        if getattr(exchange, "markets", None) is None:
            await exchange.load_markets()
        market = exchange.market(symbol)
        unified_symbol = market["symbol"]
        market_id = market["id"]
        is_spot = bool(market.get("spot"))

        if method_name == "un_watch_order_book":
            topic = "orderbook"
            swap_channel = "unsub.depth"
            spot_channel = f"spot@public.aggre.depth.v3.api.pb@100ms@{market_id}"
        elif method_name == "un_watch_ticker":
            topic = "ticker"
            swap_channel = "unsub.ticker"
            spot_channel = f"spot@public.aggre.bookTicker.v3.api.pb@100ms@{market_id}"
        elif method_name == "un_watch_trades":
            topic = "trades"
            swap_channel = "unsub.deal"
            spot_channel = f"spot@public.aggre.deals.v3.api.pb@100ms@{market_id}"
        else:
            raise ValueError(f"Unsupported MEXC unwatch method: {method_name}")

        message_hash = f"unsubscribe:{topic}:{unified_symbol}"
        if is_spot:
            url = exchange.urls["api"]["ws"]["spot"]
            worker = asyncio.create_task(
                exchange.watch_spot_public(
                    spot_channel, message_hash, {"unsubscribed": True}
                )
            )
        else:
            url = exchange.urls["api"]["ws"]["swap"]
            worker = asyncio.create_task(
                exchange.watch_swap_public(
                    swap_channel, message_hash, {"symbol": market_id}, {}
                )
            )

        client = exchange.client(url)
        registration_timeout = min(1.0, self.retirement_timeout_seconds)
        deadline = asyncio.get_running_loop().time() + registration_timeout
        try:
            while message_hash not in getattr(client, "futures", {}):
                if worker.done():
                    await worker
                    raise RuntimeError(
                        f"MEXC unsubscribe future was not registered: {message_hash}"
                    )
                if asyncio.get_running_loop().time() >= deadline:
                    raise asyncio.TimeoutError(
                        f"MEXC unsubscribe registration timed out: {message_hash}"
                    )
                await asyncio.sleep(0)
            exchange.handle_unsubscriptions(client, [message_hash])
            await asyncio.wait_for(worker, timeout=registration_timeout)
        except BaseException:
            if not worker.done():
                worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            raise

    @staticmethod
    async def _gate_unwatch_ticker(exchange: Any, symbol: str) -> None:
        if getattr(exchange, "markets", None) is None:
            await exchange.load_markets()
        market = exchange.market(symbol)
        unified_symbol = market["symbol"]
        market_id = market["id"]
        message_type = exchange.get_type_by_market(market)
        url = exchange.get_url_by_market(market)
        await exchange.un_subscribe_public_multiple(
            url,
            "ticker",
            [unified_symbol],
            [f"unsubscribe:ticker:{unified_symbol}"],
            [f"ticker:{unified_symbol}"],
            [market_id],
            f"{message_type}.tickers",
            {},
        )

    async def _unwatch_direct_stream(
        self, ex_name: str, exchange: Any, symbol: str, method_name: str
    ) -> None:
        if ex_name == "mexc":
            await self._mexc_unwatch_direct_stream(exchange, symbol, method_name)
            return
        if ex_name == "gateio" and method_name == "un_watch_ticker":
            await self._gate_unwatch_ticker(exchange, symbol)
            return
        unwatch = getattr(exchange, method_name, None)
        if callable(unwatch):
            await unwatch(symbol)

    async def _retire_direct_symbol(
        self, ex_name: str, symbol: str, cancelled_tasks: tuple[asyncio.Task, ...]
    ) -> None:
        await self._settle_cancelled_tasks(
            cancelled_tasks, context=f"{ex_name}:{symbol}"
        )
        async with self._direct_venue_lock(ex_name):
            exchange = self.exchanges.get(ex_name)
            if exchange is None:
                return
            methods = (
                "un_watch_order_book",
                "un_watch_ticker",
                "un_watch_trades",
            )
            for method_name in methods:
                try:
                    await asyncio.wait_for(
                        self._unwatch_direct_stream(
                            ex_name, exchange, symbol, method_name
                        ),
                        timeout=10.0,
                    )
                except Exception as exc:
                    logger.debug(
                        "Direct WebSocket unwatch failed for %s:%s via %s: %s",
                        ex_name, symbol, method_name, type(exc).__name__,
                    )
            self._purge_exchange_symbol_state(exchange, (symbol,))
            await self._close_idle_ccxt_clients_locked(ex_name, exchange)

    def _schedule_direct_symbol_retire(
        self, ex_name: str, symbol: str, cancelled_tasks: tuple[asyncio.Task, ...] = ()
    ) -> None:
        if self.exchanges.get(ex_name) is None and not cancelled_tasks:
            return
        stream_id = f"{ex_name}:{symbol}"
        existing = self._direct_symbol_retire_tasks.get(stream_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._retire_direct_symbol(ex_name, symbol, cancelled_tasks)
        )
        self._direct_symbol_retire_tasks[stream_id] = task

        def _done(completed: asyncio.Task) -> None:
            if self._direct_symbol_retire_tasks.get(stream_id) is completed:
                self._direct_symbol_retire_tasks.pop(stream_id, None)

        task.add_done_callback(_done)

    async def _await_direct_symbol_retirement(self, ex_name: str, symbol: str) -> None:
        task = self._direct_symbol_retire_tasks.get(f"{ex_name}:{symbol}")
        if task is not None and not task.done():
            await asyncio.shield(task)

    async def _close_exchange_instance(
        self, ex_name: str, stream_id: str, exchange: Any, cancelled_tasks: tuple[asyncio.Task, ...]
    ) -> None:
        await self._settle_cancelled_tasks(cancelled_tasks, context=stream_id)
        close_task = asyncio.create_task(exchange.close())
        timed_out = False
        try:
            try:
                await asyncio.wait_for(
                    asyncio.shield(close_task),
                    timeout=self.exchange_close_timeout_seconds,
                )
                return
            except asyncio.TimeoutError:
                self.exchange_close_timeouts += 1
                timed_out = True
                self._exchange_close_finalizer_tasks.add(close_task)
                self._exchange_close_finalizer_venues[close_task] = ex_name
                logger.warning(
                    "WebSocket exchange close timed out for %s (%s); "
                    "retaining ownership until the original close completes",
                    stream_id, ex_name,
                )
                try:
                    await close_task
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "WebSocket exchange close finalizer failed for %s (%s): %s",
                        stream_id, ex_name, type(exc).__name__,
                    )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "WebSocket exchange close failed for %s (%s): %s",
                    stream_id, ex_name, type(exc).__name__,
                )
                return
        finally:
            if not close_task.done():
                close_task.cancel()
                await asyncio.gather(close_task, return_exceptions=True)
            if timed_out:
                self._exchange_close_finalizer_tasks.discard(close_task)
                self._exchange_close_finalizer_venues.pop(close_task, None)

    def _exchange_close_finalizers_for_venue(
        self, ex_name: str
    ) -> tuple[asyncio.Task, ...]:
        return tuple(
            task
            for task in self._exchange_close_finalizer_tasks
            if not task.done()
            and self._exchange_close_finalizer_venues.get(task) == ex_name
        )

    async def _await_exchange_close_finalizers(self, ex_name: str) -> None:
        tasks = self._exchange_close_finalizers_for_venue(ex_name)
        if not tasks:
            return
        logger.warning(
            "WebSocket %s exchange creation waiting for %d unfinished close finalizer(s)",
            ex_name,
            len(tasks),
        )
        await asyncio.gather(
            *(asyncio.shield(task) for task in tasks),
            return_exceptions=True,
        )

    def _liquidation_venue_retire_tasks(
        self, ex_name: str
    ) -> tuple[asyncio.Task, ...]:
        prefix = f"{ex_name}:"
        return tuple(
            task
            for retire_id, task in self._liquidation_exchange_retire_tasks.items()
            if retire_id.startswith(prefix) and not task.done()
        )

    async def _await_liquidation_venue_retirements(self, ex_name: str) -> None:
        tasks = self._liquidation_venue_retire_tasks(ex_name)
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks))

    async def _await_liquidation_exchange_retirement(
        self, ex_name: str, symbol: str
    ) -> None:
        prefix = f"{ex_name}:{symbol}:"
        tasks = tuple(
            task
            for retire_id, task in self._liquidation_exchange_retire_tasks.items()
            if retire_id.startswith(prefix) and not task.done()
        )
        if tasks:
            await asyncio.gather(*(asyncio.shield(task) for task in tasks))

    def _schedule_liquidation_exchange_retire(
        self, ex_name: str, symbol: str, cancelled_tasks: tuple[asyncio.Task, ...] = ()
    ) -> None:
        stream_id = f"{ex_name}:{symbol}"
        exchange = self.liquidation_exchanges.pop(stream_id, None)
        if exchange is None:
            return
        task = asyncio.create_task(
            self._close_exchange_instance(ex_name, stream_id, exchange, cancelled_tasks)
        )
        retire_id = f"{stream_id}:{id(exchange)}"
        self._liquidation_exchange_retire_tasks[retire_id] = task

        def _done(completed: asyncio.Task) -> None:
            if self._liquidation_exchange_retire_tasks.get(retire_id) is completed:
                self._liquidation_exchange_retire_tasks.pop(retire_id, None)

        task.add_done_callback(_done)

    def _schedule_shared_liquidation_retire(
        self, ex_name: str, cancelled_tasks: tuple[asyncio.Task, ...] = ()
    ) -> None:
        exchange = self.shared_liquidation_exchanges.pop(ex_name, None)
        if exchange is None:
            return
        existing = self._shared_liquidation_retire_tasks.get(ex_name)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._close_exchange_instance(ex_name, f"{ex_name}:liquidations", exchange, cancelled_tasks)
        )
        self._shared_liquidation_retire_tasks[ex_name] = task

        def _done(completed: asyncio.Task) -> None:
            if self._shared_liquidation_retire_tasks.get(ex_name) is completed:
                self._shared_liquidation_retire_tasks.pop(ex_name, None)

        task.add_done_callback(_done)

    async def _await_shared_liquidation_retirement(self, ex_name: str) -> None:
        task = self._shared_liquidation_retire_tasks.get(ex_name)
        if task is not None and not task.done():
            await asyncio.shield(task)

    def retain_liquidations_only(self, ex_name: str, symbol: str):
        """Keep liquidation ownership while retiring heavier direct evidence clients."""
        stream_id = f"{ex_name}:{symbol}"
        liquidation_id = f"{stream_id}:liquidations"
        cancelled_direct: list[asyncio.Task] = []
        for task_id in (stream_id, f"{stream_id}:ticker", f"{stream_id}:trades"):
            task = self.active_tasks.pop(task_id, None)
            if task is not None:
                task.cancel()
                cancelled_direct.append(task)
        self.live_orderbooks.pop(stream_id, None)
        self.live_orderbook_history.pop(stream_id, None)
        self.live_tickers.pop(stream_id, None)
        self.live_trades.pop(stream_id, None)
        self._schedule_direct_symbol_retire(ex_name, symbol, tuple(cancelled_direct))
        for key in [
            key
            for key in list(self.circuit_breakers)
            if (key == stream_id or key.startswith(f"{stream_id}:"))
            and key != liquidation_id
        ]:
            self.circuit_breakers.pop(key, None)
        for key in [
            key
            for key in list(self.message_counters)
            if (key == stream_id or key.startswith(f"{stream_id}:"))
            and key != liquidation_id
        ]:
            self.message_counters.pop(key, None)
        self.subscribe_liquidations(ex_name, symbol)

    def subscribe_direct_evidence(self, ex_name: str, symbol: str) -> None:
        """Start orderbook/ticker/trades without adding liquidation ownership."""
        if ccxt_pro is None:
            logger.warning(
                "CCXT Pro is unavailable; skipping WebSocket subscription for %s:%s",
                ex_name,
                symbol,
            )
            return
        stream_id = f"{ex_name}:{symbol}"
        if stream_id not in self.active_tasks:
            self.active_tasks[stream_id] = asyncio.create_task(
                self.watch_orderbook_stream(ex_name, symbol)
            )
        for kind in ("ticker", "trades"):
            child_id = f"{stream_id}:{kind}"
            if child_id not in self.active_tasks:
                self.active_tasks[child_id] = asyncio.create_task(
                    self._watch_stream(ex_name, symbol, kind)
                )

    def subscribe(self, ex_name: str, symbol: str):
        """Start direct evidence plus the lifecycle-appropriate liquidation stream."""
        self.subscribe_direct_evidence(ex_name, symbol)
        if ccxt_pro is not None:
            self.subscribe_liquidations(ex_name, symbol)

    def _detach_shared_liquidation_subscriber(self, ex_name: str, symbol: str) -> None:
        if ex_name != "binance":
            return
        subscribers = self.liquidation_subscribers.get(ex_name)
        if subscribers is None:
            return
        subscribers.discard(symbol)
        if subscribers:
            return
        self.liquidation_subscribers.pop(ex_name, None)
        shared_task = self.active_tasks.pop(f"{ex_name}:liquidations", None)
        cancelled: tuple[asyncio.Task, ...] = ()
        if shared_task is not None:
            shared_task.cancel()
            cancelled = (shared_task,)
        self._schedule_shared_liquidation_retire(ex_name, cancelled)

    def unsubscribe(self, ex_name: str, symbol: str):
        stream_id = f"{ex_name}:{symbol}"
        liquidation_id = f"{stream_id}:liquidations"
        self._detach_shared_liquidation_subscriber(ex_name, symbol)
        cancelled_direct: list[asyncio.Task] = []
        for task_id in (stream_id, f"{stream_id}:ticker", f"{stream_id}:trades"):
            task = self.active_tasks.pop(task_id, None)
            if task is not None:
                task.cancel()
                cancelled_direct.append(task)
        liquidation_task = None
        if ex_name != "binance":
            liquidation_task = self.active_tasks.pop(liquidation_id, None)
            if liquidation_task is not None:
                liquidation_task.cancel()
        self.live_orderbooks.pop(stream_id, None)
        self.live_orderbook_history.pop(stream_id, None)
        self.live_tickers.pop(stream_id, None)
        self.live_trades.pop(stream_id, None)
        self.live_liquidations.pop(stream_id, None)
        self._schedule_direct_symbol_retire(ex_name, symbol, tuple(cancelled_direct))
        if ex_name != "binance":
            cancelled_liquidation = (liquidation_task,) if liquidation_task is not None else ()
            self._schedule_liquidation_exchange_retire(
                ex_name, symbol, cancelled_liquidation
            )
        for key in [key for key in list(self.circuit_breakers) if key == stream_id or key.startswith(f"{stream_id}:")]:
            self.circuit_breakers.pop(key, None)
        for key in [key for key in list(self.message_counters) if key == stream_id or key.startswith(f"{stream_id}:")]:
            self.message_counters.pop(key, None)

    def get_realtime_orderbook(self, ex_name: str, symbol: str) -> Optional[Dict]:
        """Return only a fresh latest OrderBook snapshot."""
        stream_id = f"{ex_name}:{symbol}"
        cached = self.live_orderbooks.get(stream_id)
        if not cached:
            return None
        if time.time() - cached["updated_at"] > self.ttl_seconds:
            logger.debug(f"Cache TTL expired for {stream_id}. Triggering REST fallback.")
            return None
        return copy.deepcopy(cached["data"])

    def get_realtime_orderbook_samples(
        self,
        ex_name: str,
        symbol: str,
        *,
        count: int = 3,
        min_span_seconds: float = 0.5,
        now: float | None = None,
    ) -> list[Dict[str, Any]]:
        observed_now = time.time() if now is None else float(now)
        if not math.isfinite(observed_now) or observed_now < 0 or count <= 0:
            return []
        history = self.live_orderbook_history.get(f"{ex_name}:{symbol}", [])
        cutoff = observed_now - self.ttl_seconds
        fresh = [
            snapshot for snapshot in history
            if isinstance(snapshot.get("_received_at"), (int, float))
            and not isinstance(snapshot.get("_received_at"), bool)
            and cutoff <= float(snapshot["_received_at"]) <= observed_now
        ]
        if len(fresh) < count:
            return []
        required_span = max(0.0, float(min_span_seconds))
        if count == 1:
            return copy.deepcopy(fresh[-1:]) if required_span == 0.0 else []
        if required_span == 0.0:
            return copy.deepcopy(fresh[-count:])

        latest_at = float(fresh[-1]["_received_at"])
        latest_start = len(fresh) - count
        start_index = None
        for index in range(latest_start, -1, -1):
            if latest_at - float(fresh[index]["_received_at"]) >= required_span:
                start_index = index
                break
        if start_index is None:
            return []

        window = fresh[start_index:]
        last = len(window) - 1
        selected = [
            window[round(position * last / (count - 1))]
            for position in range(count)
        ]
        return copy.deepcopy(selected)

    def _cached(self, cache: Dict[str, Dict[str, Any]], ex_name: str, symbol: str):
        entry = cache.get(f"{ex_name}:{symbol}")
        return (
            copy.deepcopy(entry["data"])
            if entry and time.time() - entry["updated_at"] <= self.ttl_seconds
            else None
        )

    def get_realtime_ticker(self, ex_name: str, symbol: str):
        return self._cached(self.live_tickers, ex_name, symbol)

    def get_realtime_trades(
        self, ex_name: str, symbol: str, *, now: float | None = None
    ) -> list[Dict[str, Any]]:
        observed_now = time.time() if now is None else float(now)
        if not math.isfinite(observed_now) or observed_now < 0:
            return []
        entry = self.live_trades.get(f"{ex_name}:{symbol}")
        rows = entry.get("data") if isinstance(entry, dict) else None
        if not isinstance(rows, list):
            return []
        cutoff_ms = (observed_now - self.trade_ttl_seconds) * 1000.0
        return [
            dict(row)
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("timestamp"), (int, float))
            and not isinstance(row.get("timestamp"), bool)
            and cutoff_ms <= float(row["timestamp"]) <= observed_now * 1000.0
        ][-self.trade_history_limit:]

    def runtime_diagnostics(self) -> Dict[str, int]:
        """Return bounded task/client/subscriber counts for fan-out telemetry."""
        task_ids = tuple(self.active_tasks)
        liquidation_task_ids = tuple(
            task_id for task_id in task_ids if task_id.endswith(":liquidations")
        )
        shared_evidence_task_ids = tuple(
            task_id for task_id in task_ids if task_id.startswith("shared-evidence:")
        )
        direct_exchanges = tuple(self.exchanges.values())
        liquidation_exchanges = (
            *self.liquidation_exchanges.values(),
            *self.shared_liquidation_exchanges.values(),
        )
        shared_evidence_exchanges = tuple(self.shared_evidence_exchanges.values())
        all_exchanges = (
            *direct_exchanges,
            *liquidation_exchanges,
            *shared_evidence_exchanges,
        )
        direct_ccxt_clients, direct_ccxt_subscriptions, direct_idle_ccxt_clients = (
            self._ccxt_transport_counts(direct_exchanges)
        )
        liquidation_ccxt_clients, liquidation_ccxt_subscriptions, _ = (
            self._ccxt_transport_counts(liquidation_exchanges)
        )
        shared_evidence_ccxt_clients, shared_evidence_ccxt_subscriptions, _ = (
            self._ccxt_transport_counts(shared_evidence_exchanges)
        )
        ccxt_clients, ccxt_subscriptions, _ = self._ccxt_transport_counts(all_exchanges)
        return {
            "active_tasks": len(task_ids),
            "liquidation_tasks": len(liquidation_task_ids),
            "shared_liquidation_tasks": sum(
                1 for task_id in liquidation_task_ids if task_id.count(":") == 1
            ),
            "shared_liquidation_subscribers": sum(
                len(symbols) for symbols in self.liquidation_subscribers.values()
            ),
            "shared_evidence_tasks": len(shared_evidence_task_ids),
            "shared_evidence_subscribers": sum(
                len(symbols) for symbols in self.shared_evidence_subscribers.values()
            ),
            "shared_evidence_active_subscribers": sum(
                len(symbols) for symbols in self.shared_evidence_active_symbols.values()
            ),
            "shared_evidence_exchange_instances": len(self.shared_evidence_exchanges),
            "shared_evidence_reconcile_tasks": sum(
                1
                for task in self._shared_evidence_reconcile_tasks.values()
                if not task.done()
            ),
            "shared_evidence_retirement_failures": sum(
                self.shared_evidence_retirement_failures.values()
            ),
            "shared_evidence_blocked_exchanges": len(
                self.shared_evidence_blocked_exchanges
            ),
            "shared_evidence_direct_fallbacks": len(self.shared_evidence_direct_fallbacks),
            "shared_evidence_generations": sum(self.shared_evidence_generation.values()),
            "direct_exchange_instances": len(self.exchanges),
            "liquidation_exchange_instances": (
                len(self.liquidation_exchanges) + len(self.shared_liquidation_exchanges)
            ),
            "direct_ccxt_clients": direct_ccxt_clients,
            "direct_ccxt_subscriptions": direct_ccxt_subscriptions,
            "direct_idle_ccxt_clients": direct_idle_ccxt_clients,
            "liquidation_ccxt_clients": liquidation_ccxt_clients,
            "liquidation_ccxt_subscriptions": liquidation_ccxt_subscriptions,
            "shared_evidence_ccxt_clients": shared_evidence_ccxt_clients,
            "shared_evidence_ccxt_subscriptions": shared_evidence_ccxt_subscriptions,
            "direct_exchange_retire_tasks": sum(
                1 for task in self._direct_symbol_retire_tasks.values() if not task.done()
            ),
            "liquidation_exchange_retire_tasks": sum(
                1 for task in self._liquidation_exchange_retire_tasks.values() if not task.done()
            ) + sum(
                1 for task in self._shared_liquidation_retire_tasks.values() if not task.done()
            ),
            "exchange_close_finalizer_tasks": sum(
                1 for task in self._exchange_close_finalizer_tasks if not task.done()
            ),
            "exchange_close_timeouts": self.exchange_close_timeouts,
            "ccxt_clients": ccxt_clients,
            "ccxt_subscriptions": ccxt_subscriptions,
        }

    @staticmethod
    def _prune_latest_cache(
        cache: Dict[str, Dict[str, Any]],
        *,
        cutoff: float,
    ) -> None:
        for key, entry in tuple(cache.items()):
            if entry.get("updated_at", 0) < cutoff:
                cache.pop(key, None)

    def _prune_orderbook_history(self, *, cutoff: float) -> None:
        for key, history in tuple(self.live_orderbook_history.items()):
            fresh = [
                snapshot
                for snapshot in history
                if isinstance(snapshot.get("_received_at"), (int, float))
                and not isinstance(snapshot.get("_received_at"), bool)
                and float(snapshot["_received_at"]) >= cutoff
            ]
            if fresh:
                self.live_orderbook_history[key] = fresh[-self.orderbook_history_limit:]
            else:
                self.live_orderbook_history.pop(key, None)

    def _prune_trade_history(self, *, cutoff_ms: float, now_ms: float) -> None:
        for key, entry in tuple(self.live_trades.items()):
            rows = entry.get("data") if isinstance(entry, dict) else None
            fresh = [
                row
                for row in (rows or [])
                if isinstance(row, dict)
                and isinstance(row.get("timestamp"), (int, float))
                and not isinstance(row.get("timestamp"), bool)
                and cutoff_ms <= float(row["timestamp"]) <= now_ms
            ]
            if fresh:
                entry["data"] = fresh[-self.trade_history_limit:]
            else:
                self.live_trades.pop(key, None)

    def _prune_liquidation_history(self, *, cutoff_ms: float, now_ms: float) -> None:
        for key, entry in tuple(self.live_liquidations.items()):
            events = [
                event
                for event in entry.get("events", [])
                if isinstance(event, dict)
                and cutoff_ms <= float(event.get("timestamp", 0)) <= now_ms
            ]
            if events:
                entry["events"] = events
            else:
                self.live_liquidations.pop(key, None)

    def prune_stale_cache(self):
        """Prune all process-local evidence caches to their causal TTL windows."""
        now = time.time()
        cutoff = now - self.ttl_seconds
        for cache in (self.live_orderbooks, self.live_tickers):
            self._prune_latest_cache(cache, cutoff=cutoff)
        self._prune_orderbook_history(cutoff=cutoff)
        self._prune_trade_history(
            cutoff_ms=(now - self.trade_ttl_seconds) * 1000.0,
            now_ms=now * 1000.0,
        )
        self._prune_liquidation_history(
            cutoff_ms=(now - self.liquidation_retention_seconds) * 1000.0,
            now_ms=now * 1000.0,
        )

    async def _settle_shutdown_tasks(
        self,
        tasks: tuple[asyncio.Task, ...],
        *,
        context: str,
        timeout: float,
    ) -> None:
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in done:
            self._consume_settled_task_result(task)
        if not pending:
            return
        for task in pending:
            task.cancel()
        await self._settle_cancelled_tasks(tuple(pending), context=context)

    async def _close_remaining_exchanges(self) -> None:
        exchange_groups = (
            self.exchanges,
            self.liquidation_exchanges,
            self.shared_liquidation_exchanges,
            self.shared_evidence_exchanges,
        )
        close_tasks = tuple(
            asyncio.create_task(
                self._close_exchange_instance(
                    "shutdown", str(stream_id), exchange, ()
                )
            )
            for exchanges in exchange_groups
            for stream_id, exchange in tuple(exchanges.items())
        )
        shutdown_close_timeout = min(
            self.retirement_timeout_seconds,
            max(0.05, self.exchange_close_timeout_seconds * 2.0),
        )
        await self._settle_shutdown_tasks(
            close_tasks,
            context="exchange-close-shutdown",
            timeout=shutdown_close_timeout,
        )

    async def _drain_exchange_retirements_for_shutdown(self) -> None:
        retire_tasks = tuple(
            [
                *self._direct_symbol_retire_tasks.values(),
                *self._liquidation_exchange_retire_tasks.values(),
                *self._shared_liquidation_retire_tasks.values(),
            ]
        )
        await self._settle_shutdown_tasks(
            retire_tasks,
            context="exchange-retire-shutdown",
            timeout=self.retirement_timeout_seconds,
        )
        self._direct_symbol_retire_tasks.clear()
        self._liquidation_exchange_retire_tasks.clear()
        self._shared_liquidation_retire_tasks.clear()

    async def close_all(self):
        reconcile_tasks = tuple(self._shared_evidence_reconcile_tasks.values())
        for task in reconcile_tasks:
            task.cancel()
        self._shared_evidence_reconcile_tasks.clear()
        self._shared_evidence_reconcile_dirty.clear()
        await self._settle_cancelled_tasks(
            reconcile_tasks, context="shared-evidence-reconcile-shutdown"
        )

        tasks = tuple(self.active_tasks.values())
        for task in tasks:
            task.cancel()
        self.active_tasks.clear()
        self.liquidation_subscribers.clear()
        self.shared_evidence_subscribers.clear()
        await self._settle_cancelled_tasks(tasks, context="websocket-shutdown")

        # Active stream ``finally`` blocks may schedule exchange retirement.
        # Drain those tasks after all active consumers are settled so no
        # retirement/finalizer escapes the bounded shutdown window.
        await self._drain_exchange_retirements_for_shutdown()

        await self._close_remaining_exchanges()
        self.exchanges.clear()
        self.liquidation_exchanges.clear()
        self.shared_liquidation_exchanges.clear()
        self.shared_evidence_exchanges.clear()
        self._direct_venue_locks.clear()
        self.shared_evidence_active_symbols.clear()
        self.shared_evidence_generation.clear()
        self.shared_evidence_retirement_failures.clear()
        self.shared_evidence_blocked_exchanges.clear()
        self.shared_evidence_direct_fallbacks.clear()
        self._shared_evidence_generation_started_at.clear()
        self._shared_evidence_reconcile_events.clear()
        self._shared_evidence_reconcile_locks.clear()
        self._shared_evidence_reconcile_dirty.clear()


def cb_recovery(breaker: CircuitBreaker, current_delay: float) -> float:
    """Pause length after a failure, honouring the breaker's OPEN timeout."""
    if breaker.state == "OPEN":
        return breaker.recovery_timeout
    return current_delay + random.uniform(0.1, 0.5)
