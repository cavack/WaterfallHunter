from waterfallhunter.core.multi_exchange_validator import MultiExchangeValidator


def _validator():
    instance = object.__new__(MultiExchangeValidator)
    instance.armed_threshold = 60
    instance.triggered_threshold = 85
    return instance


def _candles():
    return {
        "4h": {
            "valid": True, "hype_context": True, "support_broken": True,
            "lower_high": True, "setup": "FAILED_PULLBACK", "bearish_close": True,
            "volume_acceleration": True,
        },
        **{
            timeframe: {
                "valid": True, "two_closed_candles": True, "lower_high": True,
                "reclaim": True, "repump": False, "rsi_rollover": True,
                "bearish_close": True, "volume_acceleration": True,
            }
            for timeframe in ("1h", "15m", "5m")
        },
    }


def _microstructure():
    return {
        "approved": True, "spoofing_detected": False,
        "sell_flow_usdt": 60.0, "buy_flow_usdt": 40.0,
        "bid_depth_usdt": 1000.0, "ask_depth_usdt": 1000.0,
        "spread_pct": 0.05, "slippage_pct": 0.05,
        "footprint": {"available": True, "aggressive_selling": True},
    }


def _derivatives():
    return {
        "available": True, "funding_rate": 0.0005, "funding_percentile": 0.95,
        "oi_change_1h_pct": 1.0, "taker_buy_sell_ratio": 0.8,
        "top_trader_long_short_ratio": 2.0,
    }


def test_persisted_chain_can_replace_prior_snapshot_stages_only_with_current_trigger():
    result = _validator()._merge_score_v2(
        candles=_candles(), microstructure=_microstructure(), derivatives=_derivatives(),
        cross_exchange_confirmed=True, ticker={"last": 90.0, "vwap": 100.0},
        reference_price=100.0,
        strategy_stages={"hype": False, "damage": False, "setup": False, "trigger": True, "passed": False},
        persisted_stage_chain_complete=True,
    )
    assert result["is_valid"] is True
    assert result["quality_gates"]["channel_stage_chain"] is True


def test_persisted_chain_never_substitutes_for_current_trigger():
    result = _validator()._merge_score_v2(
        candles=_candles(), microstructure=_microstructure(), derivatives=_derivatives(),
        cross_exchange_confirmed=True, ticker={"last": 90.0, "vwap": 100.0},
        reference_price=100.0,
        strategy_stages={"hype": False, "damage": False, "setup": False, "trigger": False, "passed": False},
        persisted_stage_chain_complete=True,
    )
    assert result["is_valid"] is False
    assert result["quality_gates"]["channel_stage_chain"] is False


def test_persisted_chain_status_requires_current_trigger_and_secondary_confirmation():
    validator = _validator()
    stages = {"hype": False, "damage": False, "setup": False, "trigger": True, "passed": False}
    assert validator._suggested_status(90.0, stages, True, True, persisted_stage_chain_complete=True) == "TRIGGERED"
    assert validator._suggested_status(90.0, {**stages, "trigger": False}, True, True, persisted_stage_chain_complete=True) == "WATCH"
    assert validator._suggested_status(90.0, stages, True, False, persisted_stage_chain_complete=True) == "WATCH"


def test_stage_lifecycle_persistence_failure_is_observational_and_fail_open():
    import asyncio

    class FailingStore:
        def advance(self, *args, **kwargs):
            raise RuntimeError("sqlite unavailable")

    validator = _validator()
    validator.stage_lifecycle_store = FailingStore()
    lifecycle, persisted = asyncio.run(
        validator._advance_stage_lifecycle(
            "FAIL/USDT:USDT",
            7,
            {"hype": True, "damage": True, "setup": True, "trigger": True},
        )
    )
    assert persisted is False
    assert lifecycle is not None
    assert lifecycle["available"] is False
    assert lifecycle["hard_gating_allowed"] is False
    assert lifecycle["error_type"] == "RuntimeError"


def test_observational_lifecycle_packet_cannot_satisfy_strict_gate():
    import asyncio

    class ObservationalStore:
        def advance(self, *args, **kwargs):
            return {
                "available": True,
                "stale": False,
                "lifecycle_id": 7,
                "confirmed": {"passed": True},
                "observational_only": True,
                "hard_gating_allowed": False,
            }

    validator = _validator()
    validator.stage_lifecycle_store = ObservationalStore()
    _, persisted = asyncio.run(
        validator._advance_stage_lifecycle(
            "OBSERVE/USDT:USDT",
            7,
            {"hype": False, "damage": False, "setup": False, "trigger": True},
        )
    )

    assert persisted is False
