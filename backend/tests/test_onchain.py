import time

import httpx
import pytest

from waterfallhunter.discovery.onchain import OnChainIntelligence


@pytest.mark.asyncio
async def test_evm_context_uses_only_fresh_real_transfer_values():
    now = int(time.time())

    def handler(request):
        assert request.url.params["action"] == "tokentx"
        return httpx.Response(200, json={"status": "1", "result": [
            {"value": "500000000000000000", "tokenDecimal": "18", "timeStamp": str(now)},
            {"value": "900000000000000000", "tokenDecimal": "18", "timeStamp": str(now - 90000)},
        ]})

    client = OnChainIntelligence("key", None, large_transfer_usd=100.0, transport=httpx.MockTransport(handler))
    context = await client.fetch_context({"chain_id": "ethereum", "token_address": "0xabc", "price_usd": 1000.0})
    assert context["recent_transfer_sample_size"] == 1
    assert context["largest_transfer_usd"] == 500.0
    assert context["large_transfer_sample_count"] == 1


@pytest.mark.asyncio
async def test_unsupported_chain_returns_no_context():
    client = OnChainIntelligence("key", "key")
    assert await client.fetch_context({"chain_id": "ton", "token_address": "x", "price_usd": 1.0}) is None
