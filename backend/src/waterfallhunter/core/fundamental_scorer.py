"""
WaterfallHunter — Fundamental Scorer
====================================

Fetches fundamental data from multiple sources (DexScreener, CoinGecko,
LunarCrush, X/Twitter) and computes a weighted fundamental score (0-100)
for crypto tokens.

Usage
-----
    import asyncio
    from waterfallhunter.core.fundamental_scorer import FundamentalScorer

    scorer = FundamentalScorer(api_keys={
        "lunarcrush": "your-key",
        "twitter": "optional-key",
    })

    token_map = {
        "BTC": {
            "chain_id": "bitcoin",
            "token_address": "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",
            "coingecko_id": "bitcoin",
        },
    }

    result = asyncio.run(scorer.score("BTC", token_map=token_map))
    # {
    #     "fundamental_score": 72.5,
    #     "confidence": 0.75,
    #     "breakdown": {
    #         "dexscreener": {"sub_score": 65.0, "weight": 0.20, "available": True, ...},
    #         "coingecko":   {"sub_score": 80.0, "weight": 0.25, "available": True, ...},
    #         "lunarcrush": {"sub_score": 70.0, "weight": 0.35, "available": True, ...},
    #         "twitter":     {"sub_score": 60.0, "weight": 0.20, "available": False, ...},
    #     },
    #     "symbol": "BTC",
    # }

Data Sources
------------
1. **DexScreener** — free, no key. On-chain liquidity, volume, price change, txns.
2. **CoinGecko**   — free tier, no key for basic market data.
3. **LunarCrush**  — requires LUNARCRUSH_API_KEY env var or api_keys["lunarcrush"].
4. **X/Twitter**    — optional; uses Nitter public endpoints or Twitter API v2.
                      Falls back gracefully if no key / endpoint unreachable.

Scoring Weights (configurable via constructor ``weights`` dict)
--------------------------------------------------------------
    DexScreener : 20 %  (liquidity + volume)
    CoinGecko   : 25 %  (market cap rank + community + developer activity)
    LunarCrush  : 35 %  (social sentiment + galaxy score + alt_rank)
    X/Twitter   : 20 %  (mention count + sentiment)

Caching
-------
A simple in-memory TTL cache (default 5 minutes per symbol) avoids
repeated API calls within the TTL window.

Error Handling
--------------
Each source fetch is wrapped in a try/except. A failure in one source
does not block the others. The ``confidence`` field reflects how many
sources returned data (0-1).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("waterfallhunter.fundamental_scorer")

# ---------------------------------------------------------------------------
# Constants & defaults
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, float] = {
    "dexscreener": 0.20,
    "coingecko": 0.25,
    "lunarcrush": 0.35,
    "twitter": 0.20,
}

DEFAULT_CACHE_TTL: int = 300  # 5 minutes in seconds
DEFAULT_TIMEOUT: float = 15.0  # per-request timeout in seconds

# API base URLs
DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex/tokens"
COINGECKO_BASE = "https://api.coingecko.com/api/v3/coins/markets"
LUNARCRUSH_BASE = "https://lunarcrush.com/api4/public/coins"
NITTER_SEARCH_URL = "https://nitter.net/search"
TWITTER_API_BASE = "https://api.twitter.com/2/tweets/search/recent"

# Score normalisation thresholds for mapping raw metrics → 0-100
LIQUIDITY_FLOOR = 50_000.0       # $50k liquidity → score ~0
LIQUIDITY_CEIL = 5_000_000.0    # $5M+ liquidity → score ~100
VOLUME_FLOOR = 10_000.0         # $10k 24h vol → score ~0
VOLUME_CEIL = 2_000_000.0       # $2M+ 24h vol → score ~100
MCAP_RANK_FLOOR = 1000          # rank 1000+ → score ~0
MCAP_RANK_CEIL = 1              # rank 1 → score ~100
MENTION_FLOOR = 5               # 5 mentions → score ~0
MENTION_CEIL = 200              # 200+ mentions → score ~100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Clamp ``value`` into [lo, hi]."""
    return max(lo, min(hi, value))


def _linear_scale(
    raw: float | int | None,
    floor: float,
    ceil: float,
) -> float:
    """
    Linearly interpolate *raw* from [floor, ceil] → [0, 100].

    Supports both ascending (floor < ceil, e.g. liquidity) and descending
    (floor > ceil, e.g. market cap rank where lower number is better)
    scales. Returns 0 when ``raw`` is None or outside the "low" bound,
    100 when at or beyond the "high" bound.
    """
    if raw is None:
        return 0.0
    raw = float(raw)
    if floor == ceil:
        return 100.0 if raw >= floor else 0.0
    if floor < ceil:
        # Ascending: higher raw → higher score
        if raw <= floor:
            return 0.0
        if raw >= ceil:
            return 100.0
        ratio = (raw - floor) / (ceil - floor)
        return _clamp(ratio * 100.0)
    else:
        # Descending: lower raw → higher score (e.g. rank 1 = best)
        if raw >= floor:
            return 0.0
        if raw <= ceil:
            return 100.0
        ratio = (floor - raw) / (floor - ceil)
        return _clamp(ratio * 100.0)


def _log_scale(
    raw: float | int | None,
    floor: float,
    ceil: float,
) -> float:
    """
    Logarithmic scale for metrics that span orders of magnitude
    (liquidity, volume, mentions).
    """
    if raw is None or raw <= 0:
        return 0.0
    import math

    if ceil <= floor:
        return 100.0 if raw >= ceil else 0.0
    log_raw = math.log10(max(float(raw), floor))
    log_floor = math.log10(floor)
    log_ceil = math.log10(ceil)
    if log_ceil <= log_floor:
        return 100.0 if raw >= ceil else 0.0
    ratio = (log_raw - log_floor) / (log_ceil - log_floor)
    return _clamp(ratio * 100.0)


# ---------------------------------------------------------------------------
# TTL Cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    value: dict[str, Any]
    expires_at: float


class TTLCache:
    """Simple per-key TTL cache (thread-safe via asyncio.Lock)."""

    def __init__(self, ttl: int = DEFAULT_CACHE_TTL) -> None:
        self._ttl = ttl
        self._store: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> dict[str, Any] | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                return None
            return entry.value

    async def set(self, key: str, value: dict[str, Any]) -> None:
        async with self._lock:
            self._store[key] = _CacheEntry(
                value=value,
                expires_at=time.monotonic() + self._ttl,
            )

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count removed."""
        now = time.monotonic()
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            del self._store[k]
        return len(expired)


# ---------------------------------------------------------------------------
# Token mapping
# ---------------------------------------------------------------------------

@dataclass
class TokenInfo:
    """Resolved token metadata from the token_map."""
    symbol: str
    chain_id: str | None = None
    token_address: str | None = None
    coingecko_id: str | None = None

    @classmethod
    def from_map_entry(cls, symbol: str, entry: dict[str, Any]) -> "TokenInfo":
        return cls(
            symbol=symbol.upper(),
            chain_id=entry.get("chain_id"),
            token_address=entry.get("token_address"),
            coingecko_id=entry.get("coingecko_id"),
        )


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SourceResult:
    """Per-source sub-score and raw data."""
    source: str
    available: bool
    sub_score: float = 0.0
    weight: float = 0.0
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "sub_score": round(self.sub_score, 2),
            "weight": self.weight,
            "data": self.data,
            **({"error": self.error} if self.error else {}),
        }


@dataclass
class FundamentalResult:
    """Final aggregated fundamental score."""
    symbol: str
    fundamental_score: float
    confidence: float
    breakdown: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "fundamental_score": round(self.fundamental_score, 2),
            "confidence": round(self.confidence, 4),
            "breakdown": self.breakdown,
        }


# ---------------------------------------------------------------------------
# Per-source fetchers
# ---------------------------------------------------------------------------

class _BaseFetcher:
    """Base class for source fetchers — handles HTTP and error isolation."""

    source_name: str = "base"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _get_json(self, url: str, **kwargs) -> Any:
        """GET URL, return parsed JSON, raise on error."""
        resp = await self._client.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def fetch(self, token: TokenInfo) -> dict[str, Any]:
        """
        Fetch data for *token*. Must be overridden by subclasses.
        Returns a dict of raw metrics.
        """
        raise NotImplementedError

    def compute_sub_score(self, data: dict[str, Any]) -> float:
        """Map raw data → 0-100 sub-score. Override in subclasses."""
        raise NotImplementedError


class DexScreenerFetcher(_BaseFetcher):
    """Fetch on-chain liquidity & volume from DexScreener (free, no key)."""

    source_name = "dexscreener"

    async def fetch(self, token: TokenInfo) -> dict[str, Any]:
        if not token.token_address:
            raise ValueError("DexScreener requires a token_address in token_map")

        url = f"{DEXSCREENER_BASE}/{token.token_address}"
        json_data = await self._get_json(url)

        pairs = json_data.get("pairs") or []
        if not pairs:
            raise ValueError("No trading pairs found on DexScreener")

        # Aggregate across all pairs for the token
        total_liquidity = 0.0
        total_volume_24h = 0.0
        total_txns_24h = 0
        price_changes: list[float] = []

        for pair in pairs:
            liquidity = pair.get("liquidity") or {}
            total_liquidity += float(liquidity.get("usd", 0) or 0)

            volume = pair.get("volume") or {}
            total_volume_24h += float(volume.get("h24", 0) or 0)

            txns = pair.get("txns") or {}
            h24_txns = txns.get("h24") or {}
            total_txns_24h += int(h24_txns.get("buys", 0) or 0) + int(h24_txns.get("sells", 0) or 0)

            price_change = pair.get("priceChange") or {}
            m5 = price_change.get("m5")
            h1 = price_change.get("h1")
            h6 = price_change.get("h6")
            h24 = price_change.get("h24")
            if h24 is not None:
                price_changes.append(float(h24))
            elif h6 is not None:
                price_changes.append(float(h6))
            elif h1 is not None:
                price_changes.append(float(h1))
            elif m5 is not None:
                price_changes.append(float(m5))

        avg_price_change = sum(price_changes) / len(price_changes) if price_changes else 0.0

        return {
            "liquidity_usd": total_liquidity,
            "volume_24h_usd": total_volume_24h,
            "transactions_24h": total_txns_24h,
            "price_change_24h_pct": avg_price_change,
            "pair_count": len(pairs),
        }

    def compute_sub_score(self, data: dict[str, Any]) -> float:
        """
        Sub-score from DexScreener:
          - Liquidity  : 40 % (log-scaled)
          - Volume 24h : 40 % (log-scaled)
          - Price chg  : 20 % (|change| mapped to 0-100, momentum signal)
        """
        liq_score = _log_scale(
            data.get("liquidity_usd"), LIQUIDITY_FLOOR, LIQUIDITY_CEIL
        )
        vol_score = _log_scale(
            data.get("volume_24h_usd"), VOLUME_FLOOR, VOLUME_CEIL
        )

        # Price change: map absolute change to 0-100.
        # |Δ| of 0% → 0 (stagnant), |Δ| >= 15% → 100 (high activity/momentum)
        price_chg = abs(data.get("price_change_24h_pct", 0.0) or 0.0)
        price_score = _clamp(price_chg / 15.0 * 100.0)

        return _clamp(
            liq_score * 0.40 + vol_score * 0.40 + price_score * 0.20
        )


class CoinGeckoFetcher(_BaseFetcher):
    """Fetch market-cap & community data from CoinGecko (free tier)."""

    source_name = "coingecko"

    async def fetch(self, token: TokenInfo) -> dict[str, Any]:
        if not token.coingecko_id:
            raise ValueError("CoinGecko requires a coingecko_id in token_map")

        params = {
            "vs_currency": "usd",
            "ids": token.coingecko_id,
            "sparkline": "false",
            "price_change_percentage": "24h",
        }
        results = await self._get_json(COINGECKO_BASE, params=params)

        if not results or not isinstance(results, list):
            raise ValueError("CoinGecko returned no data")

        coin = results[0]

        # community_score and developer_score require a separate /coins/{id} call
        community_score: int | None = None
        developer_score: int | None = None
        sentiment_up_pct: float | None = None

        try:
            detail_url = f"https://api.coingecko.com/api/v3/coins/{token.coingecko_id}"
            detail = await self._get_json(
                detail_url,
                params={"localization": "false", "tickers": "false",
                        "market_data": "false", "community_data": "true",
                        "developer_data": "true"},
            )
            community_score = detail.get("community_score")
            developer_score = detail.get("developer_score")
            sentiment = detail.get("sentiment_votes_up_percentage")
            if sentiment is not None:
                sentiment_up_pct = float(sentiment)
        except Exception as exc:
            logger.debug("CoinGecko detail fetch failed for %s: %s", token.symbol, exc)

        return {
            "market_cap": coin.get("market_cap"),
            "market_cap_rank": coin.get("market_cap_rank"),
            "total_volume": coin.get("total_volume"),
            "price_change_24h": coin.get("price_change_24h"),
            "community_score": community_score,
            "developer_score": developer_score,
            "sentiment_votes_up_pct": sentiment_up_pct,
        }

    def compute_sub_score(self, data: dict[str, Any]) -> float:
        """
        Sub-score from CoinGecko:
          - Market cap rank : 30 % (rank 1 → 100, rank 1000+ → 0)
          - Community score : 30 % (already 0-100 from CoinGecko)
          - Developer score : 20 % (already 0-100 from CoinGecko)
          - Sentiment up %  : 20 % (map 0-100 → 0-100, 50% neutral → 50)
        """
        rank_score = _linear_scale(
            data.get("market_cap_rank"), MCAP_RANK_FLOOR, MCAP_RANK_CEIL
        )

        community = data.get("community_score")
        comm_score = _clamp(float(community)) if community is not None else 0.0

        developer = data.get("developer_score")
        dev_score = _clamp(float(developer)) if developer is not None else 0.0

        sentiment = data.get("sentiment_votes_up_pct")
        if sentiment is not None:
            # Map 0-100% → 0-100 score (50% neutral = 50)
            sent_score = _clamp(float(sentiment))
        else:
            sent_score = 0.0

        return _clamp(
            rank_score * 0.30
            + comm_score * 0.30
            + dev_score * 0.20
            + sent_score * 0.20
        )


class LunarCrushFetcher(_BaseFetcher):
    """Fetch social sentiment & galaxy score from LunarCrush (needs API key)."""

    source_name = "lunarcrush"

    def __init__(self, client: httpx.AsyncClient, api_key: str | None) -> None:
        super().__init__(client)
        self._api_key = api_key

    async def fetch(self, token: TokenInfo) -> dict[str, Any]:
        if not self._api_key:
            raise ValueError("LunarCrush requires an API key (LUNARCRUSH_API_KEY)")

        symbol = token.symbol.upper()
        url = f"{LUNARCRUSH_BASE}/{symbol}/v1"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        json_data = await self._get_json(url, headers=headers)

        # LunarCrush v4 response shape: {"data": {...}, "meta": {...}}
        data = json_data.get("data") if isinstance(json_data, dict) else json_data
        if not data:
            raise ValueError("LunarCrush returned no data")

        return {
            "social_dominance": data.get("social_dominance"),
            "social_volume_24h": data.get("social_volume_24h"),
            "sentiment": data.get("sentiment"),
            "galaxy_score": data.get("galaxy_score"),
            "alt_rank": data.get("alt_rank"),
        }

    def compute_sub_score(self, data: dict[str, Any]) -> float:
        """
        Sub-score from LunarCrush:
          - Social dominance : 20 % (0-100% → 0-100)
          - Social volume 24h: 20 % (log-scaled, floor=100, ceil=10k)
          - Sentiment        : 20 % (0-100 → 0-100)
          - Galaxy score     : 20 % (0-100 → 0-100)
          - Alt rank         : 20 % (rank 1 → 100, rank 500+ → 0)
        """
        social_dom = data.get("social_dominance")
        dom_score = _clamp(float(social_dom)) if social_dom is not None else 0.0

        social_vol = data.get("social_volume_24h")
        vol_score = _log_scale(social_vol, 100.0, 10_000.0)

        sentiment = data.get("sentiment")
        sent_score = _clamp(float(sentiment)) if sentiment is not None else 0.0

        galaxy = data.get("galaxy_score")
        galaxy_score = _clamp(float(galaxy)) if galaxy is not None else 0.0

        alt_rank = data.get("alt_rank")
        rank_score = _linear_scale(alt_rank, 500, 1)

        return _clamp(
            dom_score * 0.20
            + vol_score * 0.20
            + sent_score * 0.20
            + galaxy_score * 0.20
            + rank_score * 0.20
        )


class TwitterFetcher(_BaseFetcher):
    """
    Fetch X/Twitter mention count and sentiment.

    Strategy:
      1. If a Twitter API v2 bearer token is provided, use the official
         /tweets/search/recent endpoint.
      2. Otherwise, attempt Nitter's public search page (no key needed).
      3. If both fail, mark source as unavailable.
    """

    source_name = "twitter"

    def __init__(
        self,
        client: httpx.AsyncClient,
        bearer_token: str | None,
    ) -> None:
        super().__init__(client)
        self._bearer_token = bearer_token

    async def fetch(self, token: TokenInfo) -> dict[str, Any]:
        if self._bearer_token:
            return await self._fetch_twitter_api(token)
        return await self._fetch_nitter(token)

    # -- Twitter API v2 --------------------------------------------------

    async def _fetch_twitter_api(self, token: TokenInfo) -> dict[str, Any]:
        """Use Twitter API v2 recent search (requires bearer token)."""
        symbol = token.symbol.upper()
        query = f"${symbol} OR #{symbol}"
        headers = {"Authorization": f"Bearer {self._bearer_token}"}
        params = {
            "query": query,
            "max_results": 100,
            "tweet.fields": "created_at,lang",
        }

        data = await self._get_json(TWITTER_API_BASE, headers=headers, params=params)

        meta = data.get("meta") or {}
        total_results = int(meta.get("result_count", 0))

        # Rough sentiment heuristic: we can't get sentiment from the free tier
        # without a separate model. Use a neutral 50% positive as placeholder.
        # In production, pipe tweet text through a sentiment model.
        positive_ratio = 0.5  # neutral baseline

        return {
            "mention_count": total_results,
            "positive_ratio": positive_ratio,
            "source": "twitter_api",
        }

    # -- Nitter fallback --------------------------------------------------

    async def _fetch_nitter(self, token: TokenInfo) -> dict[str, Any]:
        """
        Scrape Nitter's search page for mention count.
        Nitter returns HTML; we parse the approximate tweet count from
        the page. This is a best-effort fallback.
        """
        import re

        symbol = token.symbol.upper()
        params = {"f": "tweets", "q": f"${symbol}"}

        try:
            resp = await self._client.get(NITTER_SEARCH_URL, params=params)
            resp.raise_for_status()
            html = resp.text

            # Count tweet article elements as a proxy for mention volume
            mention_count = html.count('class="tweet-content')
            if mention_count == 0:
                # Fallback: count user links
                mention_count = html.count('class="username')

            # Rough sentiment: look for emoji / keyword signals
            positive_keywords = ["bullish", "moon", "pump", "buy", "long", "🚀", "📈", "🔥"]
            negative_keywords = ["bearish", "dump", "sell", "short", "scam", "rug", "📉", "💀"]

            lower_html = html.lower()
            positive_count = sum(lower_html.count(kw) for kw in positive_keywords)
            negative_count = sum(lower_html.count(kw) for kw in negative_keywords)

            total_sentiment = positive_count + negative_count
            if total_sentiment > 0:
                positive_ratio = positive_count / total_sentiment
            else:
                positive_ratio = 0.5  # neutral

            return {
                "mention_count": mention_count,
                "positive_ratio": positive_ratio,
                "source": "nitter",
            }
        except Exception as exc:
            raise ValueError(f"Nitter fetch failed: {exc}") from exc

    def compute_sub_score(self, data: dict[str, Any]) -> float:
        """
        Sub-score from X/Twitter:
          - Mention count : 60 % (log-scaled)
          - Sentiment     : 40 % (positive_ratio 0-1 → 0-100)
        """
        mention_score = _log_scale(
            data.get("mention_count"), MENTION_FLOOR, MENTION_CEIL
        )

        positive_ratio = data.get("positive_ratio", 0.5)
        if positive_ratio is not None:
            sent_score = _clamp(float(positive_ratio) * 100.0)
        else:
            sent_score = 50.0

        return _clamp(mention_score * 0.60 + sent_score * 0.40)


# ---------------------------------------------------------------------------
# Main Scorer
# ---------------------------------------------------------------------------

class FundamentalScorer:
    """
    Aggregates fundamental data from DexScreener, CoinGecko, LunarCrush,
    and X/Twitter into a weighted 0-100 score.

    Parameters
    ----------
    api_keys:
        Dict of API keys. Recognised keys:
          - ``lunarcrush``: LunarCrush API key (required for that source)
          - ``twitter``:    Twitter API v2 bearer token (optional)
        If omitted, keys are read from env vars ``LUNARCRUSH_API_KEY``
        and ``TWITTER_BEARER_TOKEN`` respectively.
    weights:
        Override default scoring weights. Keys must match source names:
        ``dexscreener``, ``coingecko``, ``lunarcrush``, ``twitter``.
        Weights are normalised to sum to 1.0.
    cache_ttl:
        Per-symbol cache TTL in seconds (default 300 = 5 minutes).
    timeout:
        Per-request HTTP timeout in seconds (default 15).
    enable_cache:
        Whether to use the TTL cache (default True).
    """

    SOURCE_NAMES = ("dexscreener", "coingecko", "lunarcrush", "twitter")

    def __init__(
        self,
        api_keys: dict[str, str | None] | None = None,
        weights: dict[str, float] | None = None,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        timeout: float = DEFAULT_TIMEOUT,
        enable_cache: bool = True,
    ) -> None:
        api_keys = api_keys or {}

        self._lunarcrush_key = api_keys.get("lunarcrush") or os.getenv("LUNARCRUSH_API_KEY")
        self._twitter_token = api_keys.get("twitter") or os.getenv("TWITTER_BEARER_TOKEN")

        # Merge user weights with defaults, then normalise
        merged = dict(DEFAULT_WEIGHTS)
        if weights:
            for k, v in weights.items():
                if k in merged:
                    merged[k] = v
        total_w = sum(merged.values())
        if total_w <= 0:
            raise ValueError("Sum of weights must be > 0")
        self._weights = {k: v / total_w for k, v in merged.items()}

        self._timeout = timeout
        self._enable_cache = enable_cache
        self._cache = TTLCache(ttl=cache_ttl)

        self._client: httpx.AsyncClient | None = None
        self._fetchers: dict[str, _BaseFetcher] = {}

        logger.info(
            "FundamentalScorer initialised — weights=%s, cache_ttl=%ds, "
            "lunarcrush=%s, twitter=%s",
            {k: round(v, 4) for k, v in self._weights.items()},
            cache_ttl,
            "yes" if self._lunarcrush_key else "no",
            "yes" if self._twitter_token else "no",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "FundamentalScorer":
        await self._ensure_client()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout),
                headers={"User-Agent": "WaterfallHunter/1.0"},
                follow_redirects=True,
            )
            self._setup_fetchers()
        return self._client

    def _setup_fetchers(self) -> None:
        assert self._client is not None
        self._fetchers = {
            "dexscreener": DexScreenerFetcher(self._client),
            "coingecko": CoinGeckoFetcher(self._client),
            "lunarcrush": LunarCrushFetcher(self._client, self._lunarcrush_key),
            "twitter": TwitterFetcher(self._client, self._twitter_token),
        }

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def score(
        self,
        symbol: str,
        token_map: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Compute the fundamental score for *symbol*.

        Parameters
        ----------
        symbol:
            Token symbol (e.g. ``"BTC"``, ``"ETH"``).
        token_map:
            Dict mapping symbol → {chain_id, token_address, coingecko_id}.
            If the symbol isn't in the map, only CoinGecko (by symbol search)
            and LunarCrush (by symbol) will be attempted.

        Returns
        -------
        dict with keys: fundamental_score, confidence, breakdown, symbol
        """
        symbol = symbol.upper()
        token_map = token_map or {}

        # Cache lookup
        cache_key = self._cache_key(symbol, token_map)
        if self._enable_cache:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for %s", symbol)
                return cached

        # Resolve token info
        entry = token_map.get(symbol) or token_map.get(symbol.lower())
        if entry:
            token = TokenInfo.from_map_entry(symbol, entry)
        else:
            token = TokenInfo(symbol=symbol)

        await self._ensure_client()

        # Fetch all sources concurrently — each isolated
        source_names = list(self._fetchers.keys())
        gathered = await asyncio.gather(
            *[self._fetch_source(name, self._fetchers[name], token) for name in source_names],
            return_exceptions=False,
        )
        results: dict[str, SourceResult] = {
            name: result for name, result in zip(source_names, gathered)
        }

        # Compute final score
        return await self._aggregate(symbol, results, cache_key)

    async def score_batch(
        self,
        symbols: list[str],
        token_map: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Score multiple symbols concurrently.

        Returns a dict mapping symbol → result dict.
        """
        token_map = token_map or {}
        tasks = [self.score(s, token_map) for s in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        output: dict[str, dict[str, Any]] = {}
        for sym, res in zip(symbols, results):
            if isinstance(res, Exception):
                logger.error("Batch score failed for %s: %s", sym, res)
                output[sym.upper()] = {
                    "symbol": sym.upper(),
                    "fundamental_score": 0.0,
                    "confidence": 0.0,
                    "breakdown": {},
                    "error": str(res),
                }
            else:
                output[sym.upper()] = res
        return output

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _fetch_source(
        self,
        name: str,
        fetcher: _BaseFetcher,
        token: TokenInfo,
    ) -> SourceResult:
        """Fetch a single source with full error isolation."""
        weight = self._weights.get(name, 0.0)
        try:
            data = await fetcher.fetch(token)
            sub_score = fetcher.compute_sub_score(data)
            logger.debug(
                "Source %s for %s: sub_score=%.2f, data=%s",
                name, token.symbol, sub_score, data,
            )
            return SourceResult(
                source=name,
                available=True,
                sub_score=sub_score,
                weight=weight,
                data=data,
            )
        except Exception as exc:
            logger.warning(
                "Source %s failed for %s: %s", name, token.symbol, exc
            )
            return SourceResult(
                source=name,
                available=False,
                sub_score=0.0,
                weight=weight,
                error=str(exc),
            )

    async def _aggregate(
        self,
        symbol: str,
        results: dict[str, SourceResult],
        cache_key: str,
    ) -> dict[str, Any]:
        """Compute weighted average and confidence."""
        available_sources = [r for r in results.values() if r.available]
        total_available = len(available_sources)
        total_sources = len(results)

        if total_available == 0:
            logger.error("All sources unavailable for %s", symbol)
            result_dict = FundamentalResult(
                symbol=symbol,
                fundamental_score=0.0,
                confidence=0.0,
                breakdown={name: r.to_dict() for name, r in results.items()},
            ).to_dict()
        else:
            # Weighted sum — only available sources contribute.
            # Normalise weights among available sources.
            available_weight_sum = sum(r.weight for r in available_sources)
            if available_weight_sum <= 0:
                weighted_score = 0.0
            else:
                weighted_score = (
                    sum(r.sub_score * r.weight for r in available_sources)
                    / available_weight_sum
                )

            # Confidence = fraction of sources that returned data,
            # weighted by their relative importance.
            confidence = total_available / total_sources if total_sources > 0 else 0.0

            result_dict = FundamentalResult(
                symbol=symbol,
                fundamental_score=_clamp(weighted_score),
                confidence=confidence,
                breakdown={name: r.to_dict() for name, r in results.items()},
            ).to_dict()

        # Cache
        if self._enable_cache:
            await self._cache.set(cache_key, result_dict)

        logger.info(
            "Fundamental score for %s: %.2f (confidence=%.2f, sources=%d/%d)",
            symbol,
            result_dict["fundamental_score"],
            result_dict["confidence"],
            total_available,
            total_sources,
        )

        return result_dict

    def _cache_key(self, symbol: str, token_map: dict[str, Any]) -> str:
        """Generate a stable cache key from symbol + token_map entry."""
        entry = token_map.get(symbol) or token_map.get(symbol.lower(), {})
        raw = f"{symbol}:{entry}"
        return hashlib.md5(raw.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    async def clear_cache(self) -> None:
        """Clear the TTL cache."""
        await self._cache.clear()
        logger.info("Fundamental cache cleared")

    def get_weights(self) -> dict[str, float]:
        """Return the normalised weight dict (copy)."""
        return dict(self._weights)

    def set_weight(self, source: str, weight: float) -> None:
        """
        Override a single source weight and re-normalise.

        Raises ValueError if *source* is unknown.
        """
        if source not in self._weights:
            raise ValueError(
                f"Unknown source '{source}'. Known: {list(self._weights.keys())}"
            )
        self._weights[source] = weight
        total = sum(self._weights.values())
        self._weights = {k: v / total for k, v in self._weights.items()}
        logger.info("Weights updated: %s", self._weights)
