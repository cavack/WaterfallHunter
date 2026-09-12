from waterfallhunter.core.coinglass import CoinGlassDerivativesClient


def _ohlc(time_ms: int, close: float) -> dict:
    return {"time": time_ms, "open": str(close), "high": str(close), "low": str(close), "close": str(close)}


def test_coinglass_normalizes_complete_same_pair_packet_without_defaults():
    retrieved_at = 1_700_000_000.0
    latest = int(retrieved_at * 1000) - 5 * 60 * 1000
    client = CoinGlassDerivativesClient("test", "https://example.test")
    funding = {"code": "0", "data": [_ohlc(latest - 5 * 60 * 1000, 0.0001), _ohlc(latest, 0.0002)]}
    oi = {"code": "0", "data": [_ohlc(latest - 60 * 60 * 1000, 1_000_000), _ohlc(latest, 900_000)]}
    taker = {"code": "0", "data": [{"time": latest, "taker_buy_volume_usd": "80", "taker_sell_volume_usd": "100"}]}
    top_accounts = {"code": "0", "data": [{"time": latest, "top_account_long_short_ratio": "1.3"}]}

    assert client._funding(funding, retrieved_at) == ([0.0001, 0.0002], 0.0002)
    assert client._open_interest(oi, retrieved_at) == (900000.0, 1000000.0)
    assert client._taker_ratio(taker, retrieved_at) == 0.8
    assert client._top_trader_ratio(top_accounts, retrieved_at) == 1.3


def test_coinglass_rejects_stale_or_non_sequential_rows():
    retrieved_at = 1_700_000_000.0
    stale = int((retrieved_at - 16 * 60) * 1000)
    client = CoinGlassDerivativesClient("test", "https://example.test")
    assert client._funding({"code": "0", "data": [_ohlc(stale - 300_000, 1), _ohlc(stale, 2)]}, retrieved_at) is None
    assert client._taker_ratio({"code": "0", "data": [{"time": stale, "taker_buy_volume_usd": "1", "taker_sell_volume_usd": "1"}]}, retrieved_at) is None


def test_coinglass_taker_change_requires_a_real_one_hour_history():
    retrieved_at = 1_700_000_000.0
    latest = int(retrieved_at * 1000) - 5 * 60 * 1000
    client = CoinGlassDerivativesClient("test", "https://example.test")
    payload = {
        "code": "0",
        "data": [
            {"time": latest - 60 * 60 * 1000, "taker_buy_volume_usd": "150", "taker_sell_volume_usd": "100"},
            {"time": latest, "taker_buy_volume_usd": "80", "taker_sell_volume_usd": "100"},
        ],
    }

    assert client._taker_ratio_change(payload, retrieved_at) == -0.7
