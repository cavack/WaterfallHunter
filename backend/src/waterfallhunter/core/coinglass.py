"""Strict CoinGlass V4 adapter for same-venue USDT perpetual derivatives."""

import asyncio
import math
import time
from typing import Any

import httpx

from waterfallhunter.core.derivatives import DerivativesAnalyzer


class CoinGlassDerivativesClient:
    """Fetch only fields that are semantically equivalent to the score packet."""

    _EXCHANGE_NAMES = {
        "binance": "Binance",
        "bybit": "Bybit",
        "okx": "OKX",
        "gateio": "Gate",
        "htx": "HTX",
    }
    _MAX_LIVE_AGE_SECONDS = 15 * 60
    _MIN_OI_SPAN_SECONDS = 55 * 60
    _MAX_OI_SPAN_SECONDS = 75 * 60

    def __init__(self, api_key: str | None, base_url: str, timeout_seconds: float = 10.0):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(4)

    @staticmethod
    def _finite(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @classmethod
    def _rows(cls, payload: Any) -> list[dict[str, Any]] | None:
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            return None
        data = payload.get("data")
        return data if isinstance(data, list) and all(isinstance(row, dict) for row in data) else None

    @classmethod
    def _timestamped(cls, rows: list[dict[str, Any]], retrieved_at: float) -> list[tuple[int, dict[str, Any]]] | None:
        parsed: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            timestamp = cls._finite(row.get("time"))
            if timestamp is None or timestamp <= 0:
                return None
            parsed.append((int(timestamp), row))
        if not parsed or any(later[0] <= earlier[0] for earlier, later in zip(parsed, parsed[1:])):
            return None
        latest_age = retrieved_at - parsed[-1][0] / 1000.0
        if latest_age < -60 or latest_age > cls._MAX_LIVE_AGE_SECONDS:
            return None
        return parsed

    @classmethod
    def _funding(cls, payload: Any, retrieved_at: float) -> tuple[list[float], float] | None:
        rows = cls._rows(payload)
        parsed = cls._timestamped(rows, retrieved_at) if rows else None
        if parsed is None or len(parsed) < 2:
            return None
        rates = [cls._finite(row.get("close")) for _, row in parsed]
        if any(rate is None for rate in rates):
            return None
        values = [float(rate) for rate in rates if rate is not None]
        return values, values[-1]

    @classmethod
    def _open_interest(cls, payload: Any, retrieved_at: float) -> tuple[float, float] | None:
        rows = cls._rows(payload)
        parsed = cls._timestamped(rows, retrieved_at) if rows else None
        if parsed is None or len(parsed) < 2:
            return None
        span_seconds = (parsed[-1][0] - parsed[0][0]) / 1000.0
        if not cls._MIN_OI_SPAN_SECONDS <= span_seconds <= cls._MAX_OI_SPAN_SECONDS:
            return None
        values = [cls._finite(row.get("close")) for _, row in parsed]
        if any(value is None or value <= 0 for value in values):
            return None
        return float(values[-1]), float(values[0])

    @classmethod
    def _taker_ratio(cls, payload: Any, retrieved_at: float) -> float | None:
        rows = cls._rows(payload)
        parsed = cls._timestamped(rows, retrieved_at) if rows else None
        if parsed is None:
            return None
        latest = parsed[-1][1]
        buy = cls._finite(latest.get("taker_buy_volume_usd"))
        sell = cls._finite(latest.get("taker_sell_volume_usd"))
        if buy is None or sell is None or buy <= 0 or sell <= 0:
            return None
        return buy / sell

    @classmethod
    def _taker_ratio_change(cls, payload: Any, retrieved_at: float) -> float | None:
        rows = cls._rows(payload)
        parsed = cls._timestamped(rows, retrieved_at) if rows else None
        if parsed is None or len(parsed) < 2:
            return None
        span_seconds = (parsed[-1][0] - parsed[0][0]) / 1000.0
        if not cls._MIN_OI_SPAN_SECONDS <= span_seconds <= cls._MAX_OI_SPAN_SECONDS:
            return None
        ratios: list[float] = []
        for _, row in parsed:
            buy = cls._finite(row.get("taker_buy_volume_usd"))
            sell = cls._finite(row.get("taker_sell_volume_usd"))
            if buy is None or sell is None or buy <= 0 or sell <= 0:
                return None
            ratios.append(buy / sell)
        return round(ratios[-1] - ratios[0], 4)

    @classmethod
    def _top_trader_ratio(cls, payload: Any, retrieved_at: float) -> float | None:
        rows = cls._rows(payload)
        parsed = cls._timestamped(rows, retrieved_at) if rows else None
        if parsed is None:
            return None
        ratio = cls._finite(parsed[-1][1].get("top_account_long_short_ratio"))
        return ratio if ratio is not None and ratio > 0 else None

    async def _request(self, client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        try:
            async with self._semaphore:
                response = await client.get(path, params=params)
            if response.status_code != 200:
                return None
            payload = response.json()
            return payload if isinstance(payload, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    async def fetch_packet(
        self,
        *,
        exchange: str,
        mapped_symbol: str,
        market_id: str,
        analyzer: DerivativesAnalyzer,
    ) -> dict[str, Any]:
        retrieved_at = time.time()
        provider_exchange = self._EXCHANGE_NAMES.get(exchange)
        context = {
            "source_exchange": f"coinglass:{exchange}",
            "mapped_symbol": mapped_symbol,
            "market_id": market_id,
            "retrieved_at": retrieved_at,
            "fallback_attempts": [],
        }
        if not self._api_key:
            return {"available": False, "reason": "CoinGlass API key unavailable", **context}
        if provider_exchange is None:
            return {"available": False, "reason": f"CoinGlass unsupported exchange: {exchange}", **context}
        if not isinstance(market_id, str) or not market_id:
            return {"available": False, "reason": "missing canonical market id", **context}

        params = {"exchange": provider_exchange, "symbol": market_id, "interval": "5m"}
        headers = {"accept": "application/json", "CG-API-KEY": self._api_key}
        timeout = httpx.Timeout(self._timeout_seconds)
        async with httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=timeout) as client:
            funding, oi, taker, top_accounts = await asyncio.gather(
                self._request(client, "/api/futures/funding-rate/history", {**params, "limit": 90}),
                self._request(client, "/api/futures/open-interest/history", {**params, "limit": 13}),
                self._request(client, "/api/futures/v2/taker-buy-sell-volume/history", {**params, "limit": 13}),
                self._request(client, "/api/futures/top-long-short-account-ratio/history", {**params, "limit": 1}),
            )

        retrieved_at = time.time()
        context["retrieved_at"] = retrieved_at
        source_capture = {
            "provider": f"coinglass:{exchange}",
            "mapped_symbol": mapped_symbol,
            "market_id": market_id,
            "retrieved_at": retrieved_at,
            "funding_payload": funding,
            "open_interest_payload": oi,
            "taker_payload": taker,
            "top_accounts_payload": top_accounts,
        }
        funding_values = self._funding(funding, retrieved_at)
        oi_values = self._open_interest(oi, retrieved_at)
        taker_ratio = self._taker_ratio(taker, retrieved_at)
        taker_ratio_change = self._taker_ratio_change(taker, retrieved_at)
        top_ratio = self._top_trader_ratio(top_accounts, retrieved_at)
        if funding_values is None or oi_values is None or taker_ratio is None or top_ratio is None:
            return {
                "available": False,
                "reason": "incomplete fresh CoinGlass derivatives packet",
                **context,
                "source_capture": source_capture,
            }
        result = analyzer.evaluate_packet(
            exchange=f"coinglass:{exchange}",
            mapped_symbol=mapped_symbol,
            market_id=market_id,
            funding_history=funding_values[0],
            current_funding=funding_values[1],
            current_oi=oi_values[0],
            oi_one_hour_ago=oi_values[1],
            taker_buy_sell_ratio=taker_ratio,
            top_trader_long_short_ratio=top_ratio,
            retrieved_at=retrieved_at,
            taker_ratio_change_1h=taker_ratio_change,
        )
        result["source_capture"] = source_capture
        return result
