from waterfallhunter.core.channel_strategy import channel_stages
from waterfallhunter.core.candle_analyzer import MultiTimeframeAnalyzer


def _checks():
    return {
        "4h": {
            "hype_context": True,
            "support_broken": True,
            "failed_pullback": False,
            "flags": {"lower_high": True, "bearish_close": True, "volume_acceleration": True},
        },
        "1h": {
            "flags": {"two_bearish": True, "lower_high": True, "bearish_close": True},
        },
        "15m": {"flags": {"lower_high": True, "bearish_close": True}},
        "5m": {"flags": {"lower_high": True, "bearish_close": True}},
    }


def test_channel_strategy_requires_hype_damage_setup_and_two_timeframe_trigger():
    stages = channel_stages(_checks())

    assert stages == {
        "hype": True,
        "damage": True,
        "setup": True,
        "setup_type": "BREAKDOWN",
        "trigger": True,
    }


def test_channel_strategy_rejects_a_missing_trigger_even_when_higher_timeframes_match():
    checks = _checks()
    checks["5m"]["flags"]["bearish_close"] = False

    assert channel_stages(checks)["trigger"] is False


def test_live_adapter_uses_the_same_channel_stages_as_historical_research():
    checks = _checks()
    details = {
        timeframe: {
            "valid": True,
            "hype_context": check.get("hype_context", False),
            "support_broken": check.get("support_broken", False),
            "setup": "FAILED_PULLBACK" if check.get("failed_pullback") else None,
            "two_closed_candles": check["flags"].get("two_bearish", False),
            "lower_high": check["flags"]["lower_high"],
            "bearish_close": check["flags"]["bearish_close"],
            "volume_acceleration": check["flags"].get("volume_acceleration", False),
        }
        for timeframe, check in checks.items()
    }

    live = MultiTimeframeAnalyzer.channel_stages(details)
    historical = channel_stages(checks)

    assert {name: live[name] for name in historical} == historical
    assert live["passed"] is True
