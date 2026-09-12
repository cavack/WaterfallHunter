import asyncio
import logging
import math
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

logger = logging.getLogger("WaterfallHunter.OnChain")


class OnChainIntelligence:
    etherscan_url = "https://api.etherscan.io/v2/api"
    solscan_url = "https://pro-api.solscan.io/v2.0"
    evm_chain_ids = {"ethereum": "1", "bsc": "56", "base": "8453", "arbitrum": "42161"}

    def __init__(self, etherscan_api_key: str | None, solscan_api_key: str | None,
                 large_transfer_usd: float = 100_000.0, transport: httpx.AsyncBaseTransport | None = None):
        self.etherscan_api_key = etherscan_api_key
        self.solscan_api_key = solscan_api_key
        self.large_transfer_usd = large_transfer_usd
        self._transport = transport

    async def fetch_context(self, dex_context: dict[str, Any]) -> dict[str, Any] | None:
        chain_id = dex_context.get("chain_id")
        token_address = dex_context.get("token_address")
        price_usd = dex_context.get("price_usd")
        if not isinstance(chain_id, str) or not isinstance(token_address, str) or not self._is_positive_finite(price_usd):
            return None
        if chain_id in self.evm_chain_ids and self.etherscan_api_key:
            return await self._fetch_evm_transfers(chain_id, token_address, float(price_usd))
        if chain_id == "solana" and self.solscan_api_key:
            return await self._fetch_solana_holders(token_address)
        return None

    async def _fetch_evm_transfers(self, chain_name: str, token_address: str, price_usd: float) -> dict[str, Any] | None:
        params = {
            "chainid": self.evm_chain_ids[chain_name],
            "module": "account",
            "action": "tokentx",
            "contractaddress": token_address,
            "page": "1",
            "offset": "100",
            "sort": "desc",
            "apikey": self.etherscan_api_key,
        }
        payload = await self._get_json(self.etherscan_url, params=params)
        transfers = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(transfers, list):
            logger.warning("Etherscan transfers unavailable for %s", token_address)
            return None

        now = int(time.time())
        values = []
        for transfer in transfers:
            value_usd = self._transfer_value_usd(transfer, price_usd)
            timestamp = self._as_int(transfer.get("timeStamp")) if isinstance(transfer, dict) else None
            if value_usd is not None and timestamp is not None and now - 86400 <= timestamp <= now + 60:
                values.append(value_usd)
        if not values:
            return None

        large_values = [value for value in values if value >= self.large_transfer_usd]
        return {
            "source": "etherscan",
            "observed_at": now,
            "window_seconds": 86400,
            "recent_transfer_sample_size": len(values),
            "largest_transfer_usd": round(max(values), 2),
            "recent_transfer_sample_volume_usd": round(sum(values), 2),
            "large_transfer_threshold_usd": self.large_transfer_usd,
            "large_transfer_sample_count": len(large_values),
            "large_transfer_sample_volume_usd": round(sum(large_values), 2),
        }

    async def _fetch_solana_holders(self, token_address: str) -> dict[str, Any] | None:
        payload = await self._get_json(
            f"{self.solscan_url}/token/holders",
            params={"address": token_address, "page": "1", "page_size": "10"},
            headers={"token": self.solscan_api_key},
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        items = data.get("items") if isinstance(data, dict) else None
        if not payload.get("success") or not isinstance(items, list) or not items:
            logger.warning("Solscan holders unavailable for %s", token_address)
            return None
        percentages = [self._as_nonnegative_float(item.get("percentage")) for item in items if isinstance(item, dict)]
        percentages = [value for value in percentages if value is not None]
        if not percentages:
            return None
        return {
            "source": "solscan",
            "observed_at": int(time.time()),
            "holder_count": data.get("total"),
            "top_holders_sample_size": len(percentages),
            "top_holders_concentration_pct": round(sum(percentages), 4),
        }

    async def _get_json(self, url: str, *, params: dict[str, str], headers: dict[str, str] | None = None) -> Any:
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("On-chain provider request failed: %s", exc)
            return None

    @staticmethod
    def _transfer_value_usd(transfer: dict[str, Any], price_usd: float) -> float | None:
        try:
            raw_value = Decimal(str(transfer["value"]))
            decimals = int(transfer["tokenDecimal"])
            value_usd = float(raw_value / Decimal(10 ** decimals) * Decimal(str(price_usd)))
        except (KeyError, ValueError, InvalidOperation, OverflowError):
            return None
        return value_usd if math.isfinite(value_usd) and value_usd >= 0 else None

    @staticmethod
    def _as_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_nonnegative_float(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number >= 0 else None

    @staticmethod
    def _is_positive_finite(value: Any) -> bool:
        return isinstance(value, (int, float)) and math.isfinite(value) and value > 0
