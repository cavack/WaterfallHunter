import asyncio
import logging
import ccxt.async_support as ccxt
from typing import Dict, Any, Optional

# تغییر کلیدی در این دو خط: استفاده از مسیر کامل Absolute Import به جای Relative
from waterfallhunter.core.provider_registry import ProviderRegistry, ProviderRole, ProviderMetadata
from waterfallhunter.core.models import OrderBook

logger = logging.getLogger("WaterfallHunter.Gateway")

class ExchangeGateway:
    def __init__(self, registry: ProviderRegistry):
        self.registry = registry
        self._exchange_instances: Dict[str, ccxt.Exchange] = {}
        self.default_timeout = 8000

    async def _get_or_create_exchange(self, upstream_identity: str) -> ccxt.Exchange:
        identity = upstream_identity.lower()
        if identity not in self._exchange_instances:
            try:
                exchange_class = getattr(ccxt, identity)
                exchange = exchange_class({
                    'enableRateLimit': True,
                    'timeout': self.default_timeout,
                    'options': {
                        'defaultType': 'swap',
                    }
                })
                self._exchange_instances[identity] = exchange
                logger.debug(f"Initialized CCXT client for {identity}")
            except AttributeError:
                logger.error(f"Exchange {identity} is not supported by CCXT.")
                raise ValueError(f"UNSUPPORTED_EXCHANGE: {identity}")

        return self._exchange_instances[identity]

    async def fetch_orderbook(self, symbol: str) -> Optional[OrderBook]:
        async def fetch_factory(provider: ProviderMetadata):
            exchange = await self._get_or_create_exchange(provider.upstream_identity)
            logger.info(f"Fetching Orderbook for {symbol} via {provider.upstream_identity}")
            raw_ob = await exchange.fetch_order_book(symbol, limit=20)

            return OrderBook(
                bids=raw_ob.get('bids', []),
                asks=raw_ob.get('asks', []),
                timestamp=raw_ob.get('timestamp', 0)
            )

        try:
            orderbook = await self.registry.execute_with_failover(
                role=ProviderRole.MARKET_DATA,
                capability="orderbook",
                fetch_factory=fetch_factory
            )
            return orderbook

        except RuntimeError as e:
            logger.error(f"Failed to fetch orderbook for {symbol}: Pool exhausted. {e}")
            return None
        except Exception as e:
             logger.error(f"Unexpected error in Gateway fetch_orderbook: {e}")
             return None

    async def close_all_sessions(self):
        for name, exchange in self._exchange_instances.items():
            logger.info(f"Closing session for {name}")
            await exchange.close()
        self._exchange_instances.clear()
