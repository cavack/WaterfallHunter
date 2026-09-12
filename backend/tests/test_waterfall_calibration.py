from scripts.calibrate_waterfall import CONFIGURATIONS, calibrate, trades_by_configuration


def _trade(timestamp, net_r, *, symbol="TESTUSDT", two_15=False, volume_15=False, two_5=False):
    return {
        "symbol": symbol,
        "timestamp": timestamp,
        "exit_timestamp": timestamp + 1,
        "realized_r": net_r + 0.02,
        "net_realized_r": net_r,
        "evidence": {
            "15m": {"two_bearish": two_15, "volume_acceleration": volume_15},
            "5m": {"two_bearish": two_5},
        },
        "execution_costs": {
            "complete": True, "basis": "modeled", "fee_r": 0.01,
            "funding_r": 0.0, "slippage_r": 0.01,
            "provenance": {
                "fee": "https://developers.binance.com/commission",
                "funding": "https://fapi.binance.com/funding",
                "slippage": "https://data.binance.vision/slippage",
            },
        },
    }


def test_configuration_replay_filters_geometry_before_symbol_cooldown():
    report = {
        "cooldown_hours": 1,
        "trades": [
            _trade(1_000, 0.1),
            _trade(2_000, 0.1, two_15=True),
        ],
    }

    replay = trades_by_configuration(report)

    assert [trade["timestamp"] for trade in replay["channel_v1_baseline"]] == [1_000]
    assert [trade["timestamp"] for trade in replay["trigger_15m_two_bearish"]] == [2_000]


def test_configuration_space_is_small_frozen_and_only_tightens_trigger_evidence():
    assert len(CONFIGURATIONS) == 6
    assert CONFIGURATIONS[0]["requirements"] == ()
    assert all(configuration["complexity"] == len(configuration["requirements"]) for configuration in CONFIGURATIONS)
    assert all(timeframe in {"5m", "15m"} for configuration in CONFIGURATIONS for timeframe, _ in configuration["requirements"])


def test_calibration_refuses_a_report_without_research_only_promotion_guardrail():
    try:
        calibrate({"window": {"start_ms": 0, "end_ms": 1_000}, "candidate_pool_complete": True})
    except ValueError as exc:
        assert "research-only net-EV guardrail" in str(exc)
    else:
        raise AssertionError("unsafe report was accepted")


def test_calibration_never_applies_a_challenger_to_production():
    trades = []
    end_ms = 120 * 3_600_000
    for timestamp in range(0, end_ms, 3_600_000):
        trades.append(_trade(
            timestamp, 0.2 if (timestamp // 3_600_000) % 3 else -0.1,
            symbol=f"T{timestamp}USDT", two_15=True, volume_15=True, two_5=True,
        ))
    report = {
        "window": {"start_ms": 0, "end_ms": end_ms},
        "candidate_pool_complete": True,
        "net_ev_contract": {"promotion_permitted": False},
        "max_hold_hours": 1,
        "cooldown_hours": 0,
        "trades": trades,
    }

    result = calibrate(report, minimum_validation_trades=2, minimum_fold_trades=2)

    assert result["selection_contract"]["holdout_used_for_selection"] is False
    assert result["selected"]["research_only"] is True
    assert result["selected"]["production_applied"] is False
    assert result["promotion_eligibility"]["eligible"] is False
