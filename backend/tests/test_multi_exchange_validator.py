import copy

import pytest

from waterfallhunter.core.multi_exchange_validator import MultiExchangeValidator
from waterfallhunter.core.entry_decision import build_entry_decision
from waterfallhunter.core.multi_exchange import MultiExchangeGateway
from waterfallhunter.core.position_calculator import PositionCalculator


def validator():
    instance = object.__new__(MultiExchangeValidator)
    instance.armed_threshold = 60
    instance.triggered_threshold = 85
    return instance


def test_regime_without_trigger_cannot_arm():
    status = validator()._suggested_status(
        score=95.0,
        stages={"regime": True, "setup": "FAILED_PULLBACK", "trigger": False},
        microstructure_approved=True,
        cross_exchange_confirmed=True,
    )

    assert status == "WATCH"


def test_legacy_regime_setup_trigger_flags_cannot_arm_without_full_channel_chain():
    status = validator()._suggested_status(
        score=70.0,
        stages={"regime": True, "setup": "FAILED_PULLBACK", "trigger": True},
        microstructure_approved=True,
        cross_exchange_confirmed=True,
    )

    assert status == "WATCH"


def test_complete_channel_strategy_stage_chain_can_arm():
    status = validator()._suggested_status(
        score=70.0,
        stages={"hype": True, "damage": True, "setup": True, "setup_type": "BREAKDOWN", "trigger": True, "passed": True},
        microstructure_approved=True,
        cross_exchange_confirmed=True,
    )

    assert status == "ARMED"


def test_passed_flag_without_full_channel_stage_chain_cannot_arm():
    status = validator()._suggested_status(
        score=95.0,
        stages={"passed": True},
        microstructure_approved=True,
        cross_exchange_confirmed=True,
    )

    assert status == "WATCH"


def test_passed_flag_with_missing_trigger_cannot_arm():
    status = validator()._suggested_status(
        score=95.0,
        stages={"hype": True, "damage": True, "setup": True, "trigger": False, "passed": True},
        microstructure_approved=True,
        cross_exchange_confirmed=True,
    )

    assert status == "WATCH"


def test_cross_exchange_price_guard_rejects_symbol_collision():
    assert not MultiExchangeGateway._price_is_compatible(
        {"last": 0.0000556}, reference_price=0.002592, max_deviation_pct=5.0
    )


def test_cross_exchange_price_guard_keeps_small_real_discrepancy():
    assert MultiExchangeGateway._price_is_compatible(
        {"last": 0.04502}, reference_price=0.04495, max_deviation_pct=5.0
    )


def _complete_candles():
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


def _complete_microstructure():
    return {
        "approved": True, "spoofing_detected": False, "sell_flow_usdt": 60.0,
        "buy_flow_usdt": 40.0, "bid_depth_usdt": 1_000.0, "ask_depth_usdt": 1_000.0,
        "spread_pct": 0.05, "slippage_pct": 0.05,
        "footprint": {"available": True, "aggressive_selling": True},
    }


def _complete_derivatives():
    return {
        "available": True, "funding_rate": 0.0005, "funding_percentile": 0.95,
        "oi_change_1h_pct": 1.0, "taker_buy_sell_ratio": 0.8,
        "top_trader_long_short_ratio": 2.0,
    }


def test_score_v2_replaces_price_dislocation_and_uses_selected_contract_vwap():
    result = validator()._merge_score_v2(
        candles=_complete_candles(),
        microstructure=_complete_microstructure(),
        derivatives=_complete_derivatives(),
        cross_exchange_confirmed=True,
        ticker={"last": 90.0, "vwap": 100.0},
        reference_price=100.0,
        strategy_stages={"hype": True, "damage": True, "setup": True, "trigger": True, "passed": True},
    )

    assert result["score_version"] == "score_v2"
    assert result["is_valid"] is True
    assert "price_dislocation" not in result["score_components"]
    assert result["score_components"]["same_contract_price_location"] == 5.0
    assert result["score"] == 100.0


def test_score_v2_rejects_missing_stage_chain_without_a_zero_score():
    result = validator()._merge_score_v2(
        candles=_complete_candles(),
        microstructure=_complete_microstructure(),
        derivatives=_complete_derivatives(),
        cross_exchange_confirmed=True,
        ticker={"last": 90.0, "vwap": 100.0},
        reference_price=100.0,
        strategy_stages={"hype": True, "damage": True, "setup": True, "trigger": False, "passed": False},
    )

    assert result["is_valid"] is False
    assert result["score"] is None
    assert result["reason"] == "channel stage chain incomplete"
    assert result["quality_gates"]["channel_stage_chain"] is False


def test_score_v2_rejects_missing_selected_contract_vwap_honestly():
    result = validator()._merge_score_v2(
        candles=_complete_candles(),
        microstructure=_complete_microstructure(),
        derivatives=_complete_derivatives(),
        cross_exchange_confirmed=True,
        ticker={"last": 90.0},
        reference_price=100.0,
        strategy_stages={"hype": True, "damage": True, "setup": True, "trigger": True, "passed": True},
    )

    assert result["is_valid"] is False
    assert result["score"] is None
    assert result["reason"] == "incomplete same-contract price-location packet"


def test_watch_score_is_available_without_promoting_an_incomplete_live_setup():
    result = validator()._watch_score(
        candles=_complete_candles(),
        microstructure=_complete_microstructure(),
        derivatives={"available": False, "reason": "missing valid funding rate"},
        cross_exchange_confirmed=True,
        ticker={"last": 90.0, "vwap": 100.0},
    )

    assert result["trade_eligible"] is False
    assert result["score"] == 100.0
    assert result["coverage_pct"] == 85.0


def test_experimental_pretrigger_uses_fixed_threshold_and_keeps_core_safety_gates():
    stages = {
        "hype": True,
        "damage": False,
        "setup": False,
        "trigger": True,
        "passed": False,
    }
    gates = {
        "all_timeframes_valid": True,
        "complete_candle_packet": True,
        "complete_microstructure_packet": True,
        "complete_fresh_derivatives_packet": True,
        "taker_sell_dominance": True,
        "complete_price_location": True,
        "live_orderbook": True,
        "channel_stage_chain": False,
        "cross_exchange_confirmed": False,
    }
    kwargs = {
        "enabled": True,
        "threshold": 45.0,
        "observation_score": 48.0,
        "observation_status": "PRE-TRIGGER",
        "strategy_stages": stages,
        "quality_gates": gates,
        "microstructure": {"approved": True},
        "derivatives": {"available": True},
    }

    assert MultiExchangeValidator._experimental_pretrigger_eligible(**kwargs) is True
    assert MultiExchangeValidator._experimental_pretrigger_eligible(
        **{**kwargs, "observation_score": 44.99}
    ) is False
    assert MultiExchangeValidator._experimental_pretrigger_eligible(
        **{**kwargs, "quality_gates": {**gates, "taker_sell_dominance": False}}
    ) is False


def test_position_reference_price_uses_live_ticker_fallbacks():
    assert MultiExchangeValidator._position_reference_price(
        {"mark": None, "last": 0.0003799},
        {"best_bid": 0.0003798, "best_ask": 0.0003800},
    ) == (0.0003799, "ticker.last")

    assert MultiExchangeValidator._position_reference_price(
        {},
        {"best_bid": 10.0, "best_ask": 10.2},
    ) == (10.1, "orderbook.mid")


def test_position_reference_price_rejects_non_live_values():
    assert MultiExchangeValidator._position_reference_price(
        {"mark": None, "last": 0.0},
        {"best_bid": 1.0, "best_ask": None},
    ) == (None, None)


def test_price_location_packet_preserves_selected_contract_evidence():
    packet = validator()._price_location_packet({"last": 90.0, "vwap": 100.0})
    assert packet == {"available": True, "last": 90.0, "vwap": 100.0, "below_vwap": True}
    assert validator()._price_location_packet({"last": 90.0}) == {
        "available": False,
        "reason": "same-contract price location unavailable",
    }


def test_position_setup_reuses_captured_5m_candles_without_a_new_fetch():
    instance = validator()
    instance.position_calculator = PositionCalculator()
    history = [
        [1_788_000_000_000 + i * 300_000, 100.0, 101.0, 99.0, 100.0, 1_000.0]
        for i in range(30)
    ]
    candle_results = {
        "source_capture": {"primary_closed_ohlcv": {"5m": history}}
    }
    microstructure = {
        "best_bid": 100.0,
        "best_ask": 100.1,
        "entry_slippage_pct": 0.05,
        "exit_slippage_pct": 0.05,
    }
    market_info = {
        "precision": {"price": 0.01, "amount": 0.001},
        "contractSize": 1.0,
        "limits": {"cost": {"min": 5.0}},
    }
    setup, capture, reference = instance._position_setup_from_candle_capture(
        candle_results=candle_results,
        ticker={"last": 100.0},
        microstructure=microstructure,
        market_info=market_info,
    )
    assert setup["status"] == "READY"
    assert capture["source"] == "candle_analysis.primary_closed_ohlcv.5m"
    assert capture["reused_existing_capture"] is True
    assert capture["sample_count"] == 30
    assert reference == {"price": 100.0, "source": "ticker.last"}


def _technical_shadow_metrics():
    history = [
        [1_788_000_000_000 + i * 300_000, 100.0, 101.0, 99.0, 100.0, 1_000.0]
        for i in range(30)
    ]
    market = {
        "precision": {"price": 0.01, "amount": 0.001},
        "contractSize": 1.0,
        "limits": {"cost": {"min": 5.0}},
    }
    return {
        "candle_analysis": {"source_capture": {"primary_closed_ohlcv": {"5m": history}}},
        "ticker": {"last": 100.0},
        "microstructure": {
            "best_bid": 100.0,
            "best_ask": 100.1,
            "entry_slippage_pct": 0.05,
            "exit_slippage_pct": 0.05,
            "source_capture": {"market": market},
        },
    }


def test_technical_trade_plan_shadow_reuses_canonical_calculator_without_mutating_metrics():
    instance = validator()
    instance.position_calculator = PositionCalculator()
    metrics = _technical_shadow_metrics()
    market = metrics["microstructure"]["source_capture"]["market"]
    before = copy.deepcopy(metrics)
    canonical, _, _ = instance._position_setup_from_candle_capture(
        candle_results=metrics["candle_analysis"],
        ticker=metrics["ticker"],
        microstructure=metrics["microstructure"],
        market_info=market,
    )
    shadow = instance.build_technical_trade_plan_shadow(metrics)
    assert metrics == before
    assert shadow["observational_only"] is True
    assert shadow["hard_gating_allowed"] is False
    assert shadow["setup"] == canonical
    assert shadow["available"] is True
    assert shadow["feasible"] is True
    assert shadow["status"] == "FEASIBLE"


@pytest.mark.parametrize(
    "missing_path",
    [
        "history",
        "entry_price",
        "reference_price",
        "entry_slippage",
        "exit_slippage",
        "market_filters",
    ],
)
def test_technical_trade_plan_shadow_missing_causal_input_is_unavailable(missing_path):
    instance = validator()
    instance.position_calculator = PositionCalculator()
    metrics = _technical_shadow_metrics()
    if missing_path == "history":
        metrics["candle_analysis"]["source_capture"]["primary_closed_ohlcv"].pop("5m")
    elif missing_path == "entry_price":
        metrics["microstructure"].pop("best_bid")
    elif missing_path == "reference_price":
        metrics["ticker"].clear()
        metrics["microstructure"].pop("best_ask")
    elif missing_path == "entry_slippage":
        metrics["microstructure"].pop("entry_slippage_pct")
    elif missing_path == "exit_slippage":
        metrics["microstructure"].pop("exit_slippage_pct")
    elif missing_path == "market_filters":
        metrics["microstructure"]["source_capture"].pop("market")

    shadow = instance.build_technical_trade_plan_shadow(metrics)

    assert shadow["available"] is False
    assert shadow["feasible"] is None
    assert shadow["status"] == "UNAVAILABLE"
    assert missing_path.upper() in shadow["unavailable_reasons"]


def test_technical_trade_plan_shadow_calculator_rejection_is_infeasible(monkeypatch):
    instance = validator()
    instance.position_calculator = PositionCalculator()
    monkeypatch.setattr(
        instance.position_calculator,
        "calculate_short_position",
        lambda *args, **kwargs: {"status": "REJECTED: Invalid take-profit geometry"},
    )
    metrics = _technical_shadow_metrics()

    shadow = instance.build_technical_trade_plan_shadow(metrics)

    assert shadow["available"] is True
    assert shadow["feasible"] is False
    assert shadow["status"] == "INFEASIBLE"
    assert shadow["setup"]["status"].startswith("REJECTED")


def test_live_position_setup_does_not_invent_trade_plan_expiry(monkeypatch):
    import waterfallhunter.core.multi_exchange_validator as validator_module
    monkeypatch.setattr(validator_module.time, "time", lambda: 1_788_000_000.0)
    instance = validator()
    instance.position_calculator = PositionCalculator()
    history = [
        [1_787_990_000_000 + i * 300_000, 100.0, 101.0, 99.0, 100.0, 1_000.0]
        for i in range(30)
    ]
    setup, _, _ = instance._position_setup_from_candle_capture(
        candle_results={"source_capture": {"primary_closed_ohlcv": {"5m": history}}},
        ticker={"last": 100.0},
        microstructure={
            "best_bid": 100.0,
            "best_ask": 100.1,
            "entry_slippage_pct": 0.05,
            "exit_slippage_pct": 0.05,
        },
        market_info={
            "precision": {"price": 0.01, "amount": 0.001},
            "contractSize": 1.0,
            "limits": {"cost": {"min": 5.0}},
        },
    )
    assert setup["status"] == "READY"
    assert "expires_at" not in setup


def test_validator_attaches_observed_liquidation_flow_to_metrics():
    instance = validator()
    flow = {
        "available": True,
        "observed_at": 100.0,
        "long_liquidation_notional_1m": 700.0,
        "short_liquidation_notional_1m": 100.0,
        "liquidation_velocity_usd_per_min": 800.0,
        "burst_ratio": 4.0,
    }

    class LiquidationCache:
        @staticmethod
        def get_realtime_liquidation_flow(exchange, symbol, now=None):
            assert exchange == "binance"
            assert symbol == "TEST/USDT:USDT"
            assert now == 100.0
            return flow

    instance.ws_manager = LiquidationCache()
    metrics = {}
    instance._attach_live_liquidation_flow(
        metrics, exchange_name="binance", mapped_symbol="TEST/USDT:USDT", now=100.0
    )
    assert metrics["liquidation_flow"] == flow


def test_validator_drops_stale_liquidation_flow_when_cache_is_unavailable():
    instance = validator()

    class EmptyCache:
        @staticmethod
        def get_realtime_liquidation_flow(exchange, symbol, now=None):
            return None

    instance.ws_manager = EmptyCache()
    metrics = {"liquidation_flow": {"available": True}, "data_sources": {}}
    instance._attach_live_liquidation_flow(
        metrics, exchange_name="binance", mapped_symbol="TEST/USDT:USDT", now=100.0
    )
    assert "liquidation_flow" not in metrics
    assert "liquidations" not in metrics["data_sources"]


@pytest.mark.asyncio
async def test_cross_check_preserves_entry_decision_candle_contract(monkeypatch):
    instance = validator()
    instance.max_cross_exchange_deviation_pct = 5.0

    class FakeExchange:
        markets = {
            "TEST/USDT:USDT": {
                "precision": {"price": 0.01, "amount": 0.001},
                "contractSize": 1.0,
                "limits": {"cost": {"min": 5.0}},
            }
        }

        async def fetch_order_book(self, symbol, limit=20):
            return {"bids": [[99.9, 10.0]], "asks": [[100.1, 10.0]]}

    exchange = FakeExchange()

    class FakeGateway:
        async def compatible_market_sources(self, symbol, reference_price, max_deviation_pct, **kwargs):
            yield {
                "data": {"last": 100.0, "vwap": 101.0, "quoteVolume": 1_000_000.0},
                "exchange": "binance",
                "mapped_symbol": "TEST/USDT:USDT",
                "exchange_instance": exchange,
            }

        async def get_confirmation_exchange(self, symbol, exchange_name, reference_price, max_deviation_pct):
            return None, None

    class FakeWebsocket:
        @staticmethod
        def get_realtime_orderbook(exchange_name, symbol):
            return {"bids": [[99.9, 10.0]], "asks": [[100.1, 10.0]]}

        @staticmethod
        def get_realtime_liquidation_flow(exchange_name, symbol, now=None):
            return None

    details = _complete_candles()
    candle_results = {
        "details": details,
        "breakdown_score": 0,
        "cross_exchange_confirmed": True,
        "is_breakdown_confirmed": False,
    }

    class FakeCandleAnalyzer:
        timeframes = ("4h", "1h", "15m", "5m")

        async def analyze_candles(self, *args, **kwargs):
            return candle_results

        @staticmethod
        def channel_stages(candles):
            return {
                "hype": False,
                "damage": False,
                "setup": False,
                "trigger": False,
                "passed": False,
            }

    class FakeMicrostructure:
        async def analyze(self, *args, **kwargs):
            return _complete_microstructure()

    async def unavailable_derivatives(*args, **kwargs):
        return {"available": False, "reason": "test derivatives unavailable"}

    async def unavailable_benchmark(*args, **kwargs):
        return {"available": False, "reason": "test benchmark unavailable"}

    async def no_stage_lifecycle(*args, **kwargs):
        return None, False

    instance.gateway = FakeGateway()
    instance.ws_manager = FakeWebsocket()
    instance.candle_analyzer = FakeCandleAnalyzer()
    instance.microstructure = FakeMicrostructure()
    instance._derivatives_context = unavailable_derivatives
    instance._benchmark_context = unavailable_benchmark
    instance._advance_stage_lifecycle = no_stage_lifecycle
    monkeypatch.setattr(
        "waterfallhunter.core.multi_exchange_validator.build_cascade_evidence",
        lambda metrics, evaluated_at: {
            "status": "UNAVAILABLE",
            "readiness_points": None,
            "maximum_available": None,
        },
    )

    result = await instance.cross_check_symbol(
        "TEST/USDT:USDT",
        reference_price=100.0,
        reference_source="test",
    )
    features = result["metrics"]["candle_features"]

    assert features["4h"]["valid"] is True
    assert features["4h"]["hype_context"] is True
    assert features["4h"]["bearish_close"] is True
    assert features["4h"]["volume_acceleration"] is True
    for timeframe in ("1h", "15m", "5m"):
        assert features[timeframe]["valid"] is True
        assert features[timeframe]["reclaim"] is True
        assert features[timeframe]["repump"] is False
        assert features[timeframe]["rsi_rollover"] is True
        assert features[timeframe]["bearish_close"] is True
        assert features[timeframe]["volume_acceleration"] is True

    decision = build_entry_decision(
        result["metrics"],
        "WATCH",
        evaluated_at=1_788_000_000,
        analysis_age_seconds=1.0,
        reference_age_seconds=1.0,
    )
    assert "STRUCTURE_UNAVAILABLE" not in decision["reason_codes"]
    assert "TIMING_UNAVAILABLE" not in decision["reason_codes"]


@pytest.mark.asyncio
async def test_cross_check_rejects_bad_microstructure_before_fetching_candles():
    instance = validator()
    instance.max_cross_exchange_deviation_pct = 5.0

    class FakeExchange:
        markets = {
            "TEST/USDT:USDT": {
                "precision": {"price": 0.01, "amount": 0.001},
                "contractSize": 1.0,
                "limits": {"cost": {"min": 5.0}},
            }
        }

    exchange = FakeExchange()

    class FakeGateway:
        async def compatible_market_sources(self, symbol, reference_price, max_deviation_pct, **kwargs):
            yield {
                "data": {"last": 100.0, "vwap": 101.0, "quoteVolume": 1_000_000.0},
                "exchange": "binance",
                "mapped_symbol": "TEST/USDT:USDT",
                "exchange_instance": exchange,
            }

        async def get_confirmation_exchange(self, *args, **kwargs):
            raise AssertionError("confirmation lookup must not run for a rejected microstructure source")

    class FakeWebsocket:
        @staticmethod
        def get_realtime_orderbook(exchange_name, symbol):
            return {"bids": [[99.9, 10.0]], "asks": [[100.1, 10.0]]}

    class FakeMicrostructure:
        async def analyze(self, *args, **kwargs):
            return {"approved": False, "reason": "stale orderbook snapshot"}

    class FakeCandleAnalyzer:
        timeframes = ("4h", "1h", "15m", "5m")

        async def analyze_candles(self, *args, **kwargs):
            raise AssertionError("candle collection must not run for a rejected microstructure source")

    instance.gateway = FakeGateway()
    instance.ws_manager = FakeWebsocket()
    instance.microstructure = FakeMicrostructure()
    instance.candle_analyzer = FakeCandleAnalyzer()

    result = await instance.cross_check_symbol(
        "TEST/USDT:USDT", reference_price=100.0, reference_source="test"
    )

    assert result["is_valid"] is False
    assert result["metrics"]["source_failures"] == [
        {"exchange": "binance", "reason": "stale orderbook snapshot"}
    ]


@pytest.mark.asyncio
async def test_cross_check_reuses_fresh_ws_ticker_and_hot_microstructure_packet():
    instance = validator()
    exchange = type("Exchange", (), {"markets": {"TEST/USDT:USDT": {
        "precision": {}, "contractSize": 1.0,
        "limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}},
    }}})()
    cached_ticker = {"last": 100.0, "quoteVolume": 1_000_000.0}
    snapshots = [{"timestamp": 1000 + index, "_received_at": 1.0 + index,
                  "bids": [[99.9, 10.0]], "asks": [[100.1, 10.0]]}
                 for index in range(3)]
    trades = [{"timestamp": 3000, "side": "sell", "price": 100.0, "amount": 1.0}
              for _ in range(20)]

    class FakeWebsocket:
        def get_realtime_ticker(self, ex_name, mapped):
            assert (ex_name, mapped) == ("binance", "TEST/USDT:USDT")
            return cached_ticker
        def get_realtime_orderbook(self, ex_name, mapped):
            return snapshots[-1]
        def get_realtime_orderbook_samples(self, ex_name, mapped, **kwargs):
            return snapshots
        def get_realtime_trades(self, ex_name, mapped):
            return trades

    class FakeGateway:
        async def compatible_market_sources(
            self, symbol, reference_price, max_deviation_pct,
            realtime_ticker_getter=None,
        ):
            assert realtime_ticker_getter is not None
            assert realtime_ticker_getter("binance", "TEST/USDT:USDT") is cached_ticker
            yield {
                "data": cached_ticker,
                "exchange": "binance",
                "mapped_symbol": "TEST/USDT:USDT",
                "exchange_instance": exchange,
            }
        async def get_confirmation_exchange(self, *args, **kwargs):
            raise AssertionError("rejected microstructure must stop before confirmation")

    captured = {}
    class FakeMicrostructure:
        async def analyze(self, *args, **kwargs):
            captured.update(kwargs)
            return {"approved": False, "reason": "stale orderbook snapshot"}

    instance.gateway = FakeGateway()
    instance.ws_manager = FakeWebsocket()
    instance.microstructure = FakeMicrostructure()

    result = await instance.cross_check_symbol(
        "TEST/USDT:USDT", reference_price=100.0, reference_source="test"
    )

    assert result["is_valid"] is False
    assert captured["preloaded_snapshots"] is snapshots
    assert captured["preloaded_trades"] is trades


@pytest.mark.asyncio
async def test_cross_check_emits_bounded_runtime_diagnostics_on_unavailable_path():
    instance = validator()

    class Exchange:
        markets = {"TEST/USDT:USDT": {"contractSize": 1.0, "limits": {}}}

    class Gateway:
        async def compatible_market_sources(self, *args, **kwargs):
            yield {
                "data": {"last": 100.0}, "exchange": "binance",
                "mapped_symbol": "TEST/USDT:USDT", "exchange_instance": Exchange(),
            }

    class WS:
        def get_realtime_ticker(self, *args): return None
        def get_realtime_orderbook(self, *args):
            return {"bids": [[99.9, 10.0]], "asks": [[100.1, 10.0]]}
        def get_realtime_orderbook_samples(self, *args, **kwargs): return []
        def get_realtime_trades(self, *args): return []

    class Micro:
        snapshot_delay_seconds = 0.25
        async def analyze(self, *args, **kwargs):
            return {"approved": False, "reason": "stale orderbook snapshot"}

    instance.gateway = Gateway()
    instance.ws_manager = WS()
    instance.microstructure = Micro()

    result = await instance.cross_check_symbol(
        "TEST/USDT:USDT", reference_price=100.0, reference_source="test"
    )
    runtime = result.get("_runtime_diagnostics")

    assert runtime is not None
    assert runtime["source_attempts"] == 1
    assert runtime["ws_evidence_hits"] == 0
    assert runtime["rest_evidence_fallbacks"] == 1
    assert runtime["outcome"] == "unavailable"
    assert runtime["stage_durations_seconds"]["microstructure"] >= 0.0
    assert runtime["stage_durations_seconds"]["total"] >= 0.0
