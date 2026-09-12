from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import waterfallhunter.main as main
from waterfallhunter.core.multi_exchange_validator import MultiExchangeValidator

SYMBOL = "WATCHFAST/USDT:USDT"


class _Exchange:
    def __init__(self) -> None:
        self.markets = {SYMBOL: {"linear": True, "swap": True, "settle": "USDT", "active": True}}
        self.ticker_calls = 0

    async def fetch_ticker(self, symbol: str) -> dict:
        self.ticker_calls += 1
        return {"symbol": symbol, "last": 1.0, "quoteVolume": 3_000_000.0}


def _details(*, hype: bool, valid: bool = True) -> dict:
    return {
        timeframe: {
            "valid": valid,
            "hype_context": hype if timeframe == "4h" else False,
            "support_broken": False,
            "setup": None,
            "two_closed_candles": False,
            "lower_high": False,
            "bearish_close": False,
            "volume_acceleration": False,
        }
        for timeframe in ("5m", "15m", "1h", "4h")
    }


def _validator(monkeypatch, *, hype: bool, valid: bool = True) -> MultiExchangeValidator:
    validator = MultiExchangeValidator()
    exchange = _Exchange()
    validator.gateway.priority_chain = ["binance"]
    validator.gateway._exchanges = {"binance": exchange}
    validator.gateway._markets_loaded = {"binance": True}
    monkeypatch.setattr(validator.gateway, "_get_exchange", lambda _name: asyncio.sleep(0, result=exchange))
    monkeypatch.setattr(validator.ws_manager, "get_realtime_ticker", lambda *_: None)
    monkeypatch.setattr(
        validator.candle_analyzer,
        "analyze_hype_context",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result={
                "valid": bool(valid),
                "hype_context": bool(hype),
                "details": _details(hype=hype, valid=valid)["4h"] if valid else None,
                "reason": None if valid else "incomplete 4h",
                "source_capture": {},
            },
        ),
    )
    return validator


def test_watch_prefilter_no_hype_is_conclusive_without_full_evidence(monkeypatch) -> None:
    validator = _validator(monkeypatch, hype=False)
    result = asyncio.run(
        validator.watch_prefilter(
            SYMBOL,
            1.0,
            exchange_name="binance",
            mapped_symbol=SYMBOL,
        )
    )
    assert result["conclusive"] is True
    assert result["requires_full_validation"] is False
    assert result["result"]["observation_status"] == "WATCH"
    metrics = result["result"]["metrics"]
    assert metrics["strategy_stages"]["hype"] is False
    assert metrics["trade_eligible"] is False
    assert "microstructure" not in metrics
    assert "derivatives" not in metrics


def test_watch_prefilter_hype_or_incomplete_data_requires_full(monkeypatch) -> None:
    hype = _validator(monkeypatch, hype=True)
    hype_result = asyncio.run(hype.watch_prefilter(SYMBOL, 1.0, exchange_name="binance", mapped_symbol=SYMBOL))
    assert hype_result["requires_full_validation"] is True
    assert hype_result["result"] is None

    incomplete = _validator(monkeypatch, hype=False, valid=False)
    incomplete_result = asyncio.run(incomplete.watch_prefilter(SYMBOL, 1.0, exchange_name="binance", mapped_symbol=SYMBOL))
    assert incomplete_result["conclusive"] is False
    assert incomplete_result["requires_full_validation"] is True
    assert incomplete_result["result"] is None


def test_progressive_watch_result_uses_fast_path_only_with_recent_full_nonactionable(monkeypatch) -> None:
    calls: list[str] = []
    async def watch_prefilter(*args, **kwargs):
        calls.append("probe")
        return {
            "conclusive": True,
            "requires_full_validation": False,
            "result": {
                "is_valid": False,
                "score": None,
                "suggested_status": "REJECTED",
                "observation_status": "WATCH",
                "observation_score": None,
                "metrics": {"strategy_stages": {"hype": False}, "trade_eligible": False},
                "_runtime_diagnostics": {"source_attempts": 1, "ws_evidence_hits": 0, "rest_evidence_fallbacks": 0, "outcome": "complete", "stage_durations_seconds": {"total": 0.01}},
            },
        }
    monkeypatch.setattr(main.validator, "watch_prefilter", watch_prefilter, raising=False)
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _s: {"decision": "NO_TRADE"})
    now = 1_788_700_000.0
    metrics = {
        "exchange": "binance",
        "mapped_symbol": SYMBOL,
        "full_analysis_observed_at": now - 60.0,
    }
    result = asyncio.run(
        main._progressive_watch_result(
            SYMBOL,
            reference_price=1.0,
            current_state="WATCH",
            lifecycle_id=1,
            previous_metrics=metrics,
            now=now,
        )
    )
    assert result is not None
    assert calls == ["probe"]
    assert result["metrics"]["full_analysis_observed_at"] == now - 60.0

    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _s: {"decision": "ACTIVE"})
    assert asyncio.run(main._progressive_watch_result(SYMBOL, reference_price=1.0, current_state="WATCH", lifecycle_id=1, previous_metrics=metrics, now=now)) is None
    stale = dict(metrics, full_analysis_observed_at=now - main._WATCH_FULL_REVALIDATION_SECONDS - 1.0)
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _s: {"decision": "NO_TRADE"})
    async def require_full_waterfall(*args, **kwargs):
        return {
            "conclusive": False,
            "requires_full_validation": True,
            "reason": "watch prefilter has no valid 4h coverage",
            "result": None,
        }
    monkeypatch.setattr(main.validator, "watch_prefilter_waterfall", require_full_waterfall, raising=False)
    assert asyncio.run(main._progressive_watch_result(SYMBOL, reference_price=1.0, current_state="WATCH", lifecycle_id=1, previous_metrics=stale, now=now)) is None



def test_progressive_watch_can_use_lifecycle_fenced_bootstrap_hint(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    async def watch_prefilter(*args, **kwargs):
        calls.append((kwargs["exchange_name"], kwargs["mapped_symbol"]))
        return {
            "conclusive": True,
            "requires_full_validation": False,
            "result": {
                "is_valid": False, "score": None, "suggested_status": "REJECTED",
                "observation_status": "WATCH", "observation_score": None,
                "metrics": {"strategy_stages": {"hype": False}, "trade_eligible": False},
                "_runtime_diagnostics": {"source_attempts": 1, "ws_evidence_hits": 0,
                    "rest_evidence_fallbacks": 0, "outcome": "complete",
                    "stage_durations_seconds": {"total": 0.01}},
            },
        }
    monkeypatch.setattr(main.validator, "watch_prefilter", watch_prefilter, raising=False)
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _s: {"decision": "NO_TRADE"})
    now = 1_788_700_000.0
    monkeypatch.setattr(main, "_watch_source_hint_cache", {
        SYMBOL: {
            "exchange": "okx", "mapped_symbol": SYMBOL, "lifecycle_id": 4,
            "full_analysis_observed_at": now - 30.0,
            "evidence_observed_at": now - 5.0,
        }
    })
    result = asyncio.run(main._progressive_watch_result(
        SYMBOL, reference_price=1.0, current_state="WATCH", lifecycle_id=4,
        previous_metrics={}, now=now,
    ))
    assert result is not None
    assert calls == [("okx", SYMBOL)]
    assert result["metrics"]["full_analysis_observed_at"] == now - 30.0

    calls.clear()
    async def require_full_waterfall(*args, **kwargs):
        return {
            "conclusive": False, "requires_full_validation": True,
            "reason": "watch prefilter has no valid 4h coverage", "result": None,
        }
    monkeypatch.setattr(main.validator, "watch_prefilter_waterfall", require_full_waterfall, raising=False)
    assert asyncio.run(main._progressive_watch_result(
        SYMBOL, reference_price=1.0, current_state="WATCH", lifecycle_id=5,
        previous_metrics={}, now=now,
    )) is None
    assert calls == []


def test_bootstrap_watch_source_hints_reads_current_catalog_lifecycles(monkeypatch) -> None:
    monkeypatch.setattr(main.db, "get_all_active_candidates", lambda: {
        SYMBOL: {"lifecycle_id": 4, "status": "WATCH"},
        "BAD/USDT:USDT": {"lifecycle_id": None, "status": "WATCH"},
    })
    observed = {}
    def latest_source_hints(lifecycles, **kwargs):
        observed.update(lifecycles)
        return {SYMBOL: {
            "exchange": "binance", "mapped_symbol": SYMBOL, "lifecycle_id": 4,
            "full_analysis_observed_at": 100.0, "evidence_observed_at": 110.0,
        }}
    monkeypatch.setattr(main.production_evidence_recorder, "latest_source_hints", latest_source_hints, raising=False)
    monkeypatch.setattr(main, "_watch_source_hint_cache", {})
    count = asyncio.run(main._bootstrap_watch_source_hints())
    assert count == 1
    assert observed == {SYMBOL: 4}
    assert main._watch_source_hint_cache[SYMBOL]["lifecycle_id"] == 4



def test_full_validation_refreshes_and_invalidates_watch_hint(monkeypatch) -> None:
    monkeypatch.setattr(main, "_watch_source_hint_cache", {})
    main._refresh_watch_source_hint_from_full_validation(
        SYMBOL,
        lifecycle_id=3,
        metrics={"exchange": "bybit", "mapped_symbol": SYMBOL},
        observed_at=123.0,
    )
    assert main._watch_source_hint_cache[SYMBOL] == {
        "exchange": "bybit",
        "mapped_symbol": SYMBOL,
        "lifecycle_id": 3,
        "full_analysis_observed_at": 123.0,
        "evidence_observed_at": 123.0,
    }
    main._refresh_watch_source_hint_from_full_validation(
        SYMBOL,
        lifecycle_id=3,
        metrics={"error": "no exchange source selected"},
        observed_at=124.0,
    )
    assert SYMBOL not in main._watch_source_hint_cache


def test_removed_candidate_clears_watch_hint(monkeypatch) -> None:
    monkeypatch.setattr(main, "_watch_source_hint_cache", {
        SYMBOL: {"exchange": "binance", "mapped_symbol": SYMBOL, "lifecycle_id": 2}
    })
    monkeypatch.setattr(main.validator.ws_manager, "unsubscribe_shared_evidence", lambda *args: None)
    monkeypatch.setattr(main.validator.ws_manager, "unsubscribe", lambda *args: None)
    main._retire_removed_candidate_websocket_sources({
        SYMBOL: {"metrics": {"exchange": "binance", "mapped_symbol": SYMBOL}}
    })
    assert SYMBOL not in main._watch_source_hint_cache



class _HypeExchange:
    def __init__(self, exchange_id: str) -> None:
        self.id = exchange_id


def test_watch_waterfall_prefilter_exhausts_compatible_sources_before_no_hype(monkeypatch) -> None:
    validator = MultiExchangeValidator()
    async def sources(*args, **kwargs):
        for name in ("binance", "bybit", "okx"):
            yield {
                "exchange": name,
                "mapped_symbol": SYMBOL,
                "data": {"last": 1.0},
                "exchange_instance": _HypeExchange(name),
            }
    monkeypatch.setattr(validator.gateway, "compatible_market_sources", sources)
    async def hype_context(exchange, mapped_symbol):
        return {"valid": True, "hype_context": False, "details": {"valid": True}}
    monkeypatch.setattr(validator.candle_analyzer, "analyze_hype_context", hype_context, raising=False)
    result = asyncio.run(validator.watch_prefilter_waterfall(SYMBOL, 1.0))
    assert result["conclusive"] is True
    assert result["requires_full_validation"] is False
    packet = result["result"]
    assert packet["observation_status"] == "WATCH"
    assert packet["metrics"]["watch_prefilter"]["valid_no_hype_sources"] == 3
    assert packet["metrics"]["watch_prefilter"]["sources_checked"] == 3
    assert "microstructure" not in packet["metrics"]
    assert "derivatives" not in packet["metrics"]


def test_watch_waterfall_prefilter_any_hype_requires_full_validation(monkeypatch) -> None:
    validator = MultiExchangeValidator()
    async def sources(*args, **kwargs):
        for name in ("binance", "bybit"):
            yield {
                "exchange": name,
                "mapped_symbol": SYMBOL,
                "data": {"last": 1.0},
                "exchange_instance": _HypeExchange(name),
            }
    monkeypatch.setattr(validator.gateway, "compatible_market_sources", sources)
    async def hype_context(exchange, mapped_symbol):
        return {
            "valid": True,
            "hype_context": exchange.id == "bybit",
            "details": {"valid": True, "hype_context": exchange.id == "bybit"},
        }
    monkeypatch.setattr(validator.candle_analyzer, "analyze_hype_context", hype_context, raising=False)
    result = asyncio.run(validator.watch_prefilter_waterfall(SYMBOL, 1.0))
    assert result["conclusive"] is True
    assert result["requires_full_validation"] is True
    assert result["result"] is None
    assert result["reason"] == "watch prefilter detected hype context"


def test_watch_waterfall_prefilter_without_valid_4h_fails_closed(monkeypatch) -> None:
    validator = MultiExchangeValidator()
    async def sources(*args, **kwargs):
        yield {
            "exchange": "binance", "mapped_symbol": SYMBOL,
            "data": {"last": 1.0}, "exchange_instance": _HypeExchange("binance"),
        }
    monkeypatch.setattr(validator.gateway, "compatible_market_sources", sources)
    async def hype_context(exchange, mapped_symbol):
        return {"valid": False, "hype_context": False, "reason": "incomplete 4h"}
    monkeypatch.setattr(validator.candle_analyzer, "analyze_hype_context", hype_context, raising=False)
    result = asyncio.run(validator.watch_prefilter_waterfall(SYMBOL, 1.0))
    assert result["requires_full_validation"] is True
    assert result["result"] is None


def test_progressive_watch_uses_4h_waterfall_when_full_hint_is_stale_or_absent(monkeypatch) -> None:
    calls: list[str] = []
    async def waterfall(*args, **kwargs):
        calls.append("waterfall")
        return {
            "conclusive": True,
            "requires_full_validation": False,
            "result": {
                "is_valid": False, "score": None, "suggested_status": "REJECTED",
                "observation_status": "WATCH", "observation_score": None,
                "metrics": {
                    "exchange": "bybit", "mapped_symbol": SYMBOL,
                    "strategy_stages": {"hype": False}, "trade_eligible": False,
                    "watch_prefilter": {"version": "watch_prefilter_v2_4h_waterfall"},
                },
                "_runtime_diagnostics": {
                    "source_attempts": 2, "ws_evidence_hits": 0,
                    "rest_evidence_fallbacks": 0, "outcome": "complete",
                    "stage_durations_seconds": {"total": 0.02},
                },
            },
        }
    monkeypatch.setattr(main.validator, "watch_prefilter_waterfall", waterfall, raising=False)
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _s: {"decision": "NO_TRADE"})
    monkeypatch.setattr(main, "_watch_source_hint_cache", {})
    monkeypatch.setattr(main, "_claim_watch_full_revalidation_slot", lambda _now: False)
    now = 1_788_700_000.0
    result = asyncio.run(main._progressive_watch_result(
        SYMBOL, reference_price=1.0, current_state="WATCH", lifecycle_id=3,
        previous_metrics={}, now=now,
    ))
    assert result is not None
    assert calls == ["waterfall"]
    assert result["metrics"]["watch_prefilter"]["version"] == "watch_prefilter_v2_4h_waterfall"
    assert "full_analysis_observed_at" not in result["metrics"]

    calls.clear()
    async def source_probe(*args, **kwargs):
        calls.append("source")
        return _source_no_hype_probe_result()
    monkeypatch.setattr(main.validator, "watch_prefilter", source_probe, raising=False)
    stale_metrics = {
        "exchange": "binance", "mapped_symbol": SYMBOL,
        "full_analysis_observed_at": now - main._WATCH_FULL_REVALIDATION_SECONDS - 1.0,
    }
    result = asyncio.run(main._progressive_watch_result(
        SYMBOL, reference_price=1.0, current_state="WATCH", lifecycle_id=3,
        previous_metrics=stale_metrics, now=now,
    ))
    assert result is not None
    assert calls == ["source"]
    assert result["metrics"]["watch_prefilter"]["full_revalidation_deferred"] is True


def test_progressive_watch_waterfall_hype_still_requires_canonical_full(monkeypatch) -> None:
    async def waterfall(*args, **kwargs):
        return {
            "conclusive": True, "requires_full_validation": True,
            "reason": "watch prefilter detected hype context", "result": None,
        }
    monkeypatch.setattr(main.validator, "watch_prefilter_waterfall", waterfall, raising=False)
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _s: {"decision": "NO_TRADE"})
    monkeypatch.setattr(main, "_watch_source_hint_cache", {})
    assert asyncio.run(main._progressive_watch_result(
        SYMBOL, reference_price=1.0, current_state="WATCH", lifecycle_id=3,
        previous_metrics={}, now=1_788_700_000.0,
    )) is None


def test_watch_waterfall_prefilter_detects_hype_after_two_no_hype_sources(monkeypatch) -> None:
    validator = MultiExchangeValidator()
    async def sources(*args, **kwargs):
        for name in ("binance", "bybit", "okx"):
            yield {
                "exchange": name, "mapped_symbol": SYMBOL,
                "data": {"last": 1.0}, "exchange_instance": _HypeExchange(name),
            }
    monkeypatch.setattr(validator.gateway, "compatible_market_sources", sources)
    async def hype_context(exchange, mapped_symbol):
        return {
            "valid": True,
            "hype_context": exchange.id == "okx",
            "details": {"valid": True, "hype_context": exchange.id == "okx"},
        }
    monkeypatch.setattr(validator.candle_analyzer, "analyze_hype_context", hype_context, raising=False)
    result = asyncio.run(validator.watch_prefilter_waterfall(SYMBOL, 1.0))
    assert result["requires_full_validation"] is True
    assert result["reason"] == "watch prefilter detected hype context"


def test_watch_waterfall_prefilter_invalid_compatible_source_fails_closed(monkeypatch) -> None:
    validator = MultiExchangeValidator()
    async def sources(*args, **kwargs):
        for name in ("binance", "bybit"):
            yield {
                "exchange": name, "mapped_symbol": SYMBOL,
                "data": {"last": 1.0}, "exchange_instance": _HypeExchange(name),
            }
    monkeypatch.setattr(validator.gateway, "compatible_market_sources", sources)
    async def hype_context(exchange, mapped_symbol):
        if exchange.id == "bybit":
            return {"valid": False, "hype_context": False, "reason": "incomplete 4h"}
        return {"valid": True, "hype_context": False, "details": {"valid": True}}
    monkeypatch.setattr(validator.candle_analyzer, "analyze_hype_context", hype_context, raising=False)
    result = asyncio.run(validator.watch_prefilter_waterfall(SYMBOL, 1.0))
    assert result["requires_full_validation"] is True
    assert result["result"] is None


def _source_no_hype_probe_result() -> dict:
    return {
        "conclusive": True,
        "requires_full_validation": False,
        "reason": "watch prefilter conclusively found no hype context",
        "result": {
            "is_valid": False, "score": None, "suggested_status": "REJECTED",
            "observation_status": "WATCH", "observation_score": None,
            "metrics": {
                "exchange": "bingx", "mapped_symbol": SYMBOL,
                "strategy_stages": {"hype": False, "damage": False, "setup": False, "setup_type": None, "trigger": False, "passed": False},
                "trade_eligible": False,
                "watch_prefilter": {"version": "watch_prefilter_v2_4h_source", "conclusive_no_hype": True},
            },
            "_runtime_diagnostics": {"source_attempts": 1, "ws_evidence_hits": 0, "rest_evidence_fallbacks": 0, "outcome": "complete", "stage_durations_seconds": {"total": 0.01}},
        },
    }


def test_stale_source_hint_no_hype_is_deferred_when_revalidation_slot_busy(monkeypatch) -> None:
    now = 1_788_700_000.0
    metrics = {"exchange": "bingx", "mapped_symbol": SYMBOL, "full_analysis_observed_at": now - 7200.0}
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _s: {"decision": "NO_TRADE"})
    monkeypatch.setattr(main.validator, "watch_prefilter", lambda *a, **k: asyncio.sleep(0, result=_source_no_hype_probe_result()))
    monkeypatch.setattr(main, "_claim_watch_full_revalidation_slot", lambda _now: False, raising=False)
    async def forbidden_waterfall(*args, **kwargs):
        raise AssertionError("lifecycle-fenced source hint should avoid all-venue waterfall")
    monkeypatch.setattr(main.validator, "watch_prefilter_waterfall", forbidden_waterfall, raising=False)

    result = asyncio.run(main._progressive_watch_result(
        SYMBOL, reference_price=1.0, current_state="WATCH", lifecycle_id=4,
        previous_metrics=metrics, now=now,
    ))
    assert result is not None
    pf = result["metrics"]["watch_prefilter"]
    assert pf["full_revalidation_due"] is True
    assert pf["full_revalidation_deferred"] is True
    assert result["metrics"]["full_analysis_observed_at"] == now - 7200.0


def test_stale_source_hint_claims_bounded_full_revalidation_slot(monkeypatch) -> None:
    now = 1_788_700_000.0
    metrics = {"exchange": "bingx", "mapped_symbol": SYMBOL, "full_analysis_observed_at": now - 7200.0}
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _s: {"decision": "NO_TRADE"})
    monkeypatch.setattr(main.validator, "watch_prefilter", lambda *a, **k: asyncio.sleep(0, result=_source_no_hype_probe_result()))
    claims: list[float] = []
    def claim(ts: float) -> bool:
        claims.append(ts); return True
    monkeypatch.setattr(main, "_claim_watch_full_revalidation_slot", claim, raising=False)
    assert asyncio.run(main._progressive_watch_result(
        SYMBOL, reference_price=1.0, current_state="WATCH", lifecycle_id=4,
        previous_metrics=metrics, now=now,
    )) is None
    assert claims == [now]


def test_source_hint_hype_forces_full_even_when_revalidation_slot_busy(monkeypatch) -> None:
    now = 1_788_700_000.0
    metrics = {"exchange": "bingx", "mapped_symbol": SYMBOL, "full_analysis_observed_at": now - 7200.0}
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _s: {"decision": "NO_TRADE"})
    async def hype(*args, **kwargs):
        return {"conclusive": True, "requires_full_validation": True, "reason": "watch prefilter detected hype context", "result": None}
    monkeypatch.setattr(main.validator, "watch_prefilter", hype)
    monkeypatch.setattr(main, "_claim_watch_full_revalidation_slot", lambda _now: False, raising=False)
    assert asyncio.run(main._progressive_watch_result(
        SYMBOL, reference_price=1.0, current_state="WATCH", lifecycle_id=4,
        previous_metrics=metrics, now=now,
    )) is None


def test_watch_full_revalidation_slot_is_globally_rate_limited(monkeypatch) -> None:
    monkeypatch.setattr(main, "_watch_full_revalidation_next_allowed_at", 0.0, raising=False)
    assert main._claim_watch_full_revalidation_slot(100.0) is True
    assert main._claim_watch_full_revalidation_slot(100.0 + main._WATCH_FULL_REVALIDATION_MIN_START_INTERVAL_SECONDS - 0.1) is False
    assert main._claim_watch_full_revalidation_slot(100.0 + main._WATCH_FULL_REVALIDATION_MIN_START_INTERVAL_SECONDS) is True


def test_no_source_waterfall_no_hype_claims_bounded_full_revalidation(monkeypatch) -> None:
    now = 1_788_700_000.0
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _s: {"decision": "NO_TRADE"})
    monkeypatch.setattr(main, "_watch_source_hint_cache", {})
    async def waterfall(*args, **kwargs):
        return {
            "conclusive": True,
            "requires_full_validation": False,
            "result": {
                "is_valid": False, "score": None, "suggested_status": "REJECTED",
                "observation_status": "WATCH", "observation_score": None,
                "metrics": {
                    "exchange": "binance", "mapped_symbol": SYMBOL,
                    "strategy_stages": {"hype": False}, "trade_eligible": False,
                    "watch_prefilter": {"version": "watch_prefilter_v2_4h_waterfall"},
                },
                "_runtime_diagnostics": {
                    "source_attempts": 2, "ws_evidence_hits": 0,
                    "rest_evidence_fallbacks": 0, "outcome": "complete",
                    "stage_durations_seconds": {"total": 0.02},
                },
            },
        }
    monkeypatch.setattr(main.validator, "watch_prefilter_waterfall", waterfall, raising=False)
    claims: list[float] = []
    monkeypatch.setattr(
        main, "_claim_watch_full_revalidation_slot",
        lambda ts: claims.append(ts) or True, raising=False,
    )
    assert asyncio.run(main._progressive_watch_result(
        SYMBOL, reference_price=1.0, current_state="WATCH", lifecycle_id=3,
        previous_metrics={}, now=now,
    )) is None
    assert claims == [now]


def test_previous_source_watch_prefilter_is_4h_only(monkeypatch) -> None:
    validator = MultiExchangeValidator()
    class Exchange:
        id = "binance"
        markets = {SYMBOL: {}}
        async def fetch_ticker(self, mapped_symbol):
            return {"last": 1.0}
    exchange = Exchange()
    async def get_exchange(_name):
        return exchange
    monkeypatch.setattr(validator.gateway, "_get_exchange", get_exchange)
    validator.gateway._markets_loaded["binance"] = True
    monkeypatch.setattr(validator.ws_manager, "get_realtime_ticker", lambda *a: {"last": 1.0})
    async def forbidden_full_candles(*args, **kwargs):
        raise AssertionError("previous-source WATCH probe must not load all timeframes")
    monkeypatch.setattr(validator.candle_analyzer, "analyze_candles", forbidden_full_candles)
    async def hype_context(*args, **kwargs):
        return {
            "valid": True, "hype_context": False,
            "details": {"valid": True, "hype_context": False},
            "source_capture": {"primary_closed_ohlcv": {"4h": [[1,1,1,1,1,1]]}},
        }
    monkeypatch.setattr(validator.candle_analyzer, "analyze_hype_context", hype_context)
    result = asyncio.run(validator.watch_prefilter(
        SYMBOL, 1.0, exchange_name="binance", mapped_symbol=SYMBOL,
    ))
    assert result["requires_full_validation"] is False
    packet = result["result"]
    assert packet["metrics"]["valid_candle_timeframes"] == 1
    assert packet["metrics"]["watch_prefilter"]["version"] == "watch_prefilter_v2_4h_source"
