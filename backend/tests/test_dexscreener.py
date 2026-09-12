import httpx
import pytest

from waterfallhunter.discovery.dexscreener import (
    DexScreenerClient,
)


def test_token_mapping_requires_chain_and_contract():
    client = DexScreenerClient(
        True,
        (
            '{"PEPE":{'
            '"chain_id":"solana",'
            '"token_address":"abc"'
            '},'
            '"BAD":{'
            '"chain_id":"solana"'
            '}}'
        ),
    )

    assert (
        client.mapped_symbols
        == {
            "PEPE",
        }
    )


@pytest.mark.asyncio
async def test_context_uses_exact_base_contract_and_highest_liquidity_pair():
    def handler(
        request,
    ):
        assert (
            request.url.path
            == "/token-pairs/v1/solana/exact"
        )

        return httpx.Response(
            200,
            json=[
                {
                    "baseToken": {
                        "address": "wrong",
                    },
                    "liquidity": {
                        "usd": 9000,
                    },
                },
                {
                    "baseToken": {
                        "address": "EXACT",
                    },
                    "pairAddress": "low",
                    "dexId": "raydium",
                    "priceUsd": "0.10",
                    "liquidity": {
                        "usd": 100,
                    },
                    "volume": {
                        "h24": 50,
                    },
                },
                {
                    "baseToken": {
                        "address": "exact",
                    },
                    "pairAddress": "best",
                    "dexId": "raydium",
                    "priceUsd": "0.11",
                    "liquidity": {
                        "usd": 200,
                    },
                    "volume": {
                        "h24": 75,
                    },
                    "txns": {
                        "h24": {
                            "buys": 4,
                            "sells": 7,
                        },
                    },
                    "priceChange": {
                        "h24": -2.5,
                    },
                    "boosts": {
                        "active": 1,
                    },
                },
            ],
        )

    client = DexScreenerClient(
        True,
        (
            '{"PEPE":{'
            '"chain_id":"solana",'
            '"token_address":"exact"'
            '}}'
        ),
        transport=httpx.MockTransport(
            handler
        ),
    )

    context = await client.fetch_context(
        "PEPE"
    )

    assert context is not None

    assert (
        context["pair_address"]
        == "best"
    )

    assert (
        context["liquidity_usd"]
        == 200.0
    )

    assert (
        context["sells_h24"]
        == 7.0
    )

    assert (
        context[
            "price_change_h24_pct"
        ]
        == -2.5
    )


@pytest.mark.asyncio
async def test_pair_with_missing_liquidity_does_not_break_pair_ranking():
    def handler(
        request,
    ):
        assert (
            request.url.path
            == "/token-pairs/v1/solana/exact"
        )

        return httpx.Response(
            200,
            json=[
                {
                    "baseToken": {
                        "address": "exact",
                    },
                    "pairAddress": (
                        "missing-liquidity"
                    ),
                    "dexId": "raydium",
                    "priceUsd": "0.10",
                    "volume": {
                        "h24": 50,
                    },
                },
                {
                    "baseToken": {
                        "address": "exact",
                    },
                    "pairAddress": (
                        "valid-liquidity"
                    ),
                    "dexId": "raydium",
                    "priceUsd": "0.11",
                    "liquidity": {
                        "usd": 200,
                    },
                    "volume": {
                        "h24": 75,
                    },
                },
            ],
        )

    client = DexScreenerClient(
        True,
        (
            '{"PEPE":{'
            '"chain_id":"solana",'
            '"token_address":"exact"'
            '}}'
        ),
        transport=httpx.MockTransport(
            handler
        ),
    )

    context = await client.fetch_context(
        "PEPE"
    )

    assert context is not None

    assert (
        context["pair_address"]
        == "valid-liquidity"
    )

    assert (
        context["liquidity_usd"]
        == 200.0
    )


@pytest.mark.asyncio
async def test_pair_with_invalid_liquidity_does_not_beat_valid_pair():
    def handler(
        request,
    ):
        return httpx.Response(
            200,
            json=[
                {
                    "baseToken": {
                        "address": "exact",
                    },
                    "pairAddress": (
                        "invalid-liquidity"
                    ),
                    "priceUsd": "0.12",
                    "liquidity": {
                        "usd": "not-a-number",
                    },
                    "volume": {
                        "h24": 100,
                    },
                },
                {
                    "baseToken": {
                        "address": "exact",
                    },
                    "pairAddress": (
                        "valid-liquidity"
                    ),
                    "priceUsd": "0.11",
                    "liquidity": {
                        "usd": 10,
                    },
                    "volume": {
                        "h24": 75,
                    },
                },
            ],
        )

    client = DexScreenerClient(
        True,
        (
            '{"PEPE":{'
            '"chain_id":"solana",'
            '"token_address":"exact"'
            '}}'
        ),
        transport=httpx.MockTransport(
            handler
        ),
    )

    context = await client.fetch_context(
        "PEPE"
    )

    assert context is not None

    assert (
        context["pair_address"]
        == "valid-liquidity"
    )

    assert (
        context["liquidity_usd"]
        == 10.0
    )


@pytest.mark.asyncio
async def test_all_exact_pairs_with_invalid_liquidity_are_rejected_cleanly():
    def handler(
        request,
    ):
        return httpx.Response(
            200,
            json=[
                {
                    "baseToken": {
                        "address": "exact",
                    },
                    "pairAddress": "missing",
                    "priceUsd": "0.10",
                    "volume": {
                        "h24": 50,
                    },
                },
                {
                    "baseToken": {
                        "address": "exact",
                    },
                    "pairAddress": "invalid",
                    "priceUsd": "0.11",
                    "liquidity": {
                        "usd": None,
                    },
                    "volume": {
                        "h24": 75,
                    },
                },
            ],
        )

    client = DexScreenerClient(
        True,
        (
            '{"PEPE":{'
            '"chain_id":"solana",'
            '"token_address":"exact"'
            '}}'
        ),
        transport=httpx.MockTransport(
            handler
        ),
    )

    context = await client.fetch_context(
        "PEPE"
    )

    assert context is None


@pytest.mark.asyncio
async def test_context_rejects_a_symbol_without_an_explicit_mapping():
    client = DexScreenerClient(
        True,
        "{}",
    )

    assert (
        await client.fetch_context(
            "PEPE"
        )
        is None
    )
