import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(
    "WaterfallHunter.DexScreener"
)


@dataclass(frozen=True)
class TokenMapping:
    chain_id: str
    token_address: str


class DexScreenerClient:
    """
    Fetches live DEX context for explicitly mapped futures symbols only.
    """

    base_url = "https://api.dexscreener.com"

    def __init__(
        self,
        enabled: bool,
        token_map_json: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.enabled = enabled
        self._transport = transport
        self._token_map = self._parse_token_map(
            token_map_json
        )

    @staticmethod
    def _parse_token_map(
        raw: str,
    ) -> dict[str, TokenMapping]:
        try:
            value = json.loads(
                raw or "{}"
            )
        except json.JSONDecodeError:
            logger.error(
                "DEXSCREENER_TOKEN_MAP_JSON is not valid JSON; "
                "DEX enrichment is disabled"
            )
            return {}

        if not isinstance(
            value,
            dict,
        ):
            logger.error(
                "DEXSCREENER_TOKEN_MAP_JSON must be an object; "
                "DEX enrichment is disabled"
            )
            return {}

        mappings: dict[
            str,
            TokenMapping,
        ] = {}

        for symbol, mapping in value.items():
            if (
                not isinstance(
                    symbol,
                    str,
                )
                or not isinstance(
                    mapping,
                    dict,
                )
            ):
                continue

            chain_id = mapping.get(
                "chain_id"
            )

            token_address = mapping.get(
                "token_address"
            )

            if (
                isinstance(
                    chain_id,
                    str,
                )
                and chain_id
                and isinstance(
                    token_address,
                    str,
                )
                and token_address
            ):
                mappings[
                    symbol.upper()
                ] = TokenMapping(
                    chain_id=chain_id,
                    token_address=(
                        token_address
                    ),
                )

            else:
                logger.warning(
                    "Ignoring incomplete DEX mapping for %r",
                    symbol,
                )

        return mappings

    @property
    def mapped_symbols(
        self,
    ) -> set[str]:
        return set(
            self._token_map
        )

    async def fetch_context(
        self,
        symbol: str,
    ) -> dict[str, Any] | None:
        mapping = self._token_map.get(
            symbol.upper()
        )

        if (
            not self.enabled
            or mapping is None
        ):
            return None

        url = (
            f"{self.base_url}"
            f"/token-pairs/v1/"
            f"{mapping.chain_id}/"
            f"{mapping.token_address}"
        )

        try:
            async with httpx.AsyncClient(
                timeout=8.0,
                transport=self._transport,
            ) as client:
                response = await client.get(
                    url,
                    headers={
                        "Accept": (
                            "application/json"
                        )
                    },
                )

                response.raise_for_status()

                pairs = response.json()

        except (
            httpx.HTTPError,
            ValueError,
        ) as exc:
            logger.warning(
                "DEX context unavailable for %s: %s",
                symbol,
                exc,
            )
            return None

        if not isinstance(
            pairs,
            list,
        ):
            logger.warning(
                "DEX context rejected for %s: invalid response shape",
                symbol,
            )
            return None

        pair = self._select_pair(
            pairs,
            mapping,
        )

        if pair is None:
            logger.warning(
                "DEX context rejected for %s: no exact token pair",
                symbol,
            )
            return None

        return self._normalise_context(
            pair,
            mapping,
        )

    @staticmethod
    def _pair_liquidity_rank(
        pair: dict[str, Any],
    ) -> float:
        """
        Ranking-only liquidity value.

        Invalid or missing liquidity must sort below every valid
        non-negative liquidity value.

        This sentinel is used only for pair selection. It does not
        convert invalid liquidity into real market data.
        """
        liquidity = (
            pair.get(
                "liquidity"
            )
            if isinstance(
                pair.get(
                    "liquidity"
                ),
                dict,
            )
            else {}
        )

        value = (
            DexScreenerClient
            ._as_nonnegative_float(
                liquidity.get(
                    "usd"
                )
            )
        )

        if value is None:
            return -1.0

        return value

    @staticmethod
    def _select_pair(
        pairs: list[Any],
        mapping: TokenMapping,
    ) -> dict[str, Any] | None:
        expected = (
            mapping
            .token_address
            .lower()
        )

        exact_pairs = [
            pair
            for pair in pairs
            if (
                isinstance(
                    pair,
                    dict,
                )
                and isinstance(
                    pair.get(
                        "baseToken"
                    ),
                    dict,
                )
                and str(
                    pair[
                        "baseToken"
                    ].get(
                        "address",
                        "",
                    )
                ).lower()
                == expected
            )
        ]

        if not exact_pairs:
            return None

        return max(
            exact_pairs,
            key=(
                DexScreenerClient
                ._pair_liquidity_rank
            ),
        )

    @staticmethod
    def _as_nonnegative_float(
        value: Any,
    ) -> float | None:
        number = (
            DexScreenerClient
            ._as_finite_float(
                value
            )
        )

        return (
            number
            if (
                number is not None
                and number >= 0
            )
            else None
        )

    @staticmethod
    def _as_finite_float(
        value: Any,
    ) -> float | None:
        try:
            number = float(
                value
            )
        except (
            TypeError,
            ValueError,
        ):
            return None

        return (
            number
            if math.isfinite(
                number
            )
            else None
        )

    @classmethod
    def _normalise_context(
        cls,
        pair: dict[str, Any],
        mapping: TokenMapping,
    ) -> dict[str, Any] | None:
        liquidity = (
            pair.get(
                "liquidity"
            )
            if isinstance(
                pair.get(
                    "liquidity"
                ),
                dict,
            )
            else {}
        )

        volume = (
            pair.get(
                "volume"
            )
            if isinstance(
                pair.get(
                    "volume"
                ),
                dict,
            )
            else {}
        )

        txns = (
            pair.get(
                "txns"
            )
            if isinstance(
                pair.get(
                    "txns"
                ),
                dict,
            )
            else {}
        )

        h24_txns = (
            txns.get(
                "h24"
            )
            if isinstance(
                txns.get(
                    "h24"
                ),
                dict,
            )
            else {}
        )

        price_change = (
            pair.get(
                "priceChange"
            )
            if isinstance(
                pair.get(
                    "priceChange"
                ),
                dict,
            )
            else {}
        )

        boosts = (
            pair.get(
                "boosts"
            )
            if isinstance(
                pair.get(
                    "boosts"
                ),
                dict,
            )
            else {}
        )

        price_usd = (
            cls._as_nonnegative_float(
                pair.get(
                    "priceUsd"
                )
            )
        )

        liquidity_usd = (
            cls._as_nonnegative_float(
                liquidity.get(
                    "usd"
                )
            )
        )

        h24_volume = (
            cls._as_nonnegative_float(
                volume.get(
                    "h24"
                )
            )
        )

        if (
            price_usd is None
            or liquidity_usd is None
            or h24_volume is None
        ):
            return None

        return {
            "source": "dexscreener",
            "observed_at": int(
                time.time()
            ),
            "chain_id": (
                mapping.chain_id
            ),
            "token_address": (
                mapping.token_address
            ),
            "pair_address": (
                pair.get(
                    "pairAddress"
                )
            ),
            "dex_id": (
                pair.get(
                    "dexId"
                )
            ),
            "url": (
                pair.get(
                    "url"
                )
            ),
            "price_usd": price_usd,
            "liquidity_usd": (
                liquidity_usd
            ),
            "volume_h24_usd": (
                h24_volume
            ),
            "buys_h24": (
                cls._as_nonnegative_float(
                    h24_txns.get(
                        "buys"
                    )
                )
            ),
            "sells_h24": (
                cls._as_nonnegative_float(
                    h24_txns.get(
                        "sells"
                    )
                )
            ),
            "price_change_h24_pct": (
                cls._as_finite_float(
                    price_change.get(
                        "h24"
                    )
                )
            ),
            "pair_created_at": (
                pair.get(
                    "pairCreatedAt"
                )
            ),
            "active_boosts": (
                cls._as_nonnegative_float(
                    boosts.get(
                        "active"
                    )
                )
            ),
        }
