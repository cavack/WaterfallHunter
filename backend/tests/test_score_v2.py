import pytest

from waterfallhunter.core.score_v2 import ScoreV2


def complete_packet():
    return {
        "candles": {
            "4h": {
                "valid": True,
                "hype_context": True,
                "support_broken": True,
                "lower_high": True,
                "setup": "FAILED_PULLBACK",
                "bearish_close": True,
                "volume_acceleration": True,
            },
            "1h": {
                "valid": True,
                "two_closed_candles": True,
                "lower_high": True,
                "reclaim": True,
                "repump": False,
                "rsi_rollover": True,
                "bearish_close": True,
                "volume_acceleration": True,
            },
            "15m": {
                "valid": True,
                "two_closed_candles": True,
                "lower_high": True,
                "reclaim": True,
                "repump": False,
                "rsi_rollover": True,
                "bearish_close": True,
                "volume_acceleration": True,
            },
            "5m": {
                "valid": True,
                "two_closed_candles": True,
                "lower_high": True,
                "reclaim": True,
                "repump": False,
                "rsi_rollover": True,
                "bearish_close": True,
                "volume_acceleration": True,
            },
        },
        "microstructure": {
            "approved": True,
            "spoofing_detected": False,
            "sell_flow_usdt": 60,
            "buy_flow_usdt": 40,
            "footprint": {"available": True, "aggressive_selling": True},
            "bid_depth_usdt": 1000,
            "ask_depth_usdt": 1000,
            "spread_pct": 0.05,
            "slippage_pct": 0.05,
        },
        "derivatives": {
            "available": True,
            "funding_rate": 0.0005,
            "funding_percentile": 0.95,
            "oi_change_1h_pct": 1,
            "taker_buy_sell_ratio": 0.8,
            "top_trader_long_short_ratio": 2.0,
        },
    }


def test_complete_score_v2_packet_has_fixed_component_total():
    packet = complete_packet()

    result = ScoreV2().evaluate(
        packet["candles"],
        packet["microstructure"],
        packet["derivatives"],
        True,
        {"below_vwap": True},
    )

    assert result["is_valid"] is True
    assert result["score_version"] == "score_v2"
    assert result["components"] == {
        "structural_post_pump": 35.0,
        "entry_timing": 20.0,
        "execution_microstructure": 20.0,
        "derivatives_confirmation": 15.0,
        "cross_exchange_confirmation": 5.0,
        "same_contract_price_location": 5.0,
    }
    assert result["score"] == 100.0


def test_incomplete_derivative_packet_is_unavailable_not_a_zero_component():
    packet = complete_packet()
    packet["derivatives"] = {"available": False, "reason": "missing valid funding rate"}

    result = ScoreV2().evaluate(
        packet["candles"],
        packet["microstructure"],
        packet["derivatives"],
        True,
        {"below_vwap": True},
    )

    assert result["is_valid"] is False
    assert result["score"] is None
    assert result["reason"] == "incomplete fresh derivatives packet"


@pytest.mark.parametrize("price_location", [{}, {"below_vwap": None}])
def test_unavailable_price_location_is_not_scored_as_zero(price_location):
    packet = complete_packet()

    result = ScoreV2().evaluate(
        packet["candles"],
        packet["microstructure"],
        packet["derivatives"],
        True,
        price_location,
    )

    assert result["is_valid"] is False
    assert result["score"] is None
    assert result["components"] == {}
    assert result["gates"]["complete_price_location"] is False
    assert result["reason"] == "incomplete same-contract price-location packet"


def test_watch_score_redistributes_only_missing_evidence_weight():
    packet = complete_packet()
    packet["derivatives"] = {"available": False, "reason": "missing valid funding rate"}

    result = ScoreV2().evaluate_watch(
        packet["candles"], packet["microstructure"], packet["derivatives"], True, {"below_vwap": True}
    )

    assert result["score_version"] == "score_v2_watch_v1"
    assert result["trade_eligible"] is False
    assert result["components"]["derivatives_confirmation"] is None
    assert result["component_maximums"]["derivatives_confirmation"] == 0.0
    assert result["available_weight"] == 85.0
    assert result["coverage_pct"] == 85.0
    assert result["score"] == 100.0


def test_watch_score_keeps_known_negative_evidence_in_its_denominator():
    packet = complete_packet()
    packet["microstructure"]["spoofing_detected"] = True
    packet["microstructure"]["approved"] = False

    result = ScoreV2().evaluate_watch(
        packet["candles"], packet["microstructure"], packet["derivatives"], False, {"below_vwap": False}
    )

    assert result["components"]["execution_microstructure"] == 0.0
    assert result["component_maximums"]["execution_microstructure"] == 20.0
    assert result["components"]["cross_exchange_confirmation"] == 0.0
    assert result["component_maximums"]["cross_exchange_confirmation"] == 5.0
    assert result["components"]["same_contract_price_location"] == 0.0
    assert result["component_maximums"]["same_contract_price_location"] == 5.0
    assert result["available_weight"] == 100.0
    assert result["score"] == 70.0


def test_derivatives_short_pressure_is_continuous_and_penalizes_aggressive_buying():
    candles = complete_packet()["candles"]
    scorer = ScoreV2()
    funding_oi_divergence = {
        "funding_rate": 0.000506, "funding_percentile": 0.90, "oi_change_1h_pct": -0.650,
        "taker_buy_sell_ratio": 1.0233, "top_trader_long_short_ratio": 1.2573,
    }
    crowded_longs = {
        "funding_rate": 0.000050, "funding_percentile": 0.80, "oi_change_1h_pct": -0.505,
        "taker_buy_sell_ratio": 1.2887, "top_trader_long_short_ratio": 1.6903,
    }
    active_buyers = {
        "funding_rate": 0.000010, "funding_percentile": 0.8556, "oi_change_1h_pct": -0.260,
        "taker_buy_sell_ratio": 2.0184, "top_trader_long_short_ratio": 1.9078,
    }

    first = scorer._derivatives(funding_oi_divergence, candles)
    third = scorer._derivatives(crowded_longs, candles)
    second = scorer._derivatives(active_buyers, candles)

    assert first > third > second
    assert first - second >= 4.0
    assert second < 5.0


def test_falling_real_taker_ratio_improves_watch_pressure_without_overriding_current_ratio():
    candles = complete_packet()["candles"]
    base = {
        "funding_rate": 0.0001, "funding_percentile": 0.90, "oi_change_1h_pct": -0.50,
        "taker_buy_sell_ratio": 0.95, "top_trader_long_short_ratio": 1.5,
    }
    scorer = ScoreV2()

    falling = scorer._derivatives({**base, "taker_ratio_change_1h": -0.45}, candles)
    rising = scorer._derivatives({**base, "taker_ratio_change_1h": 0.45}, candles)

    assert falling > rising


def test_primary_score_rejects_active_taker_buying_even_with_complete_data():
    packet = complete_packet()
    packet["candles"]["4h"].update(
        {"hype_context": False, "support_broken": False, "lower_high": False, "setup": "CONTINUATION"}
    )
    packet["candles"]["1h"].update({"reclaim": False, "repump": False})
    packet["microstructure"]["footprint"]["aggressive_selling"] = False
    packet["derivatives"].update(
        {
            "funding_rate": -0.0001,
            "funding_percentile": 0.1,
            "oi_change_1h_pct": 1,
            "taker_buy_sell_ratio": 1.2,
            "top_trader_long_short_ratio": 0.8,
        }
    )

    result = ScoreV2().evaluate(
        packet["candles"],
        packet["microstructure"],
        packet["derivatives"],
        True,
        {"below_vwap": False},
    )

    assert result["is_valid"] is False
    assert result["score"] is None
    assert result["gates"]["taker_sell_dominance"] is False
    assert result["reason"] == "taker buy/sell has not confirmed sell dominance"
