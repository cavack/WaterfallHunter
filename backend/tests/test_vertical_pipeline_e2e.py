from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import Response
import pytest

import waterfallhunter.main as main
from schema_test_support import migrate_test_database
from waterfallhunter.core.dashboard_stream import DashboardEventBuffer
from waterfallhunter.core.db import DBAdapter
from waterfallhunter.core.entry_decision import build_entry_decision
from waterfallhunter.core.entry_decision_store import EntryDecisionStore
from waterfallhunter.core.historical_outcome_store import HistoricalOutcomeStore
from waterfallhunter.core.lbank_signal_ledger import LBankSignalLedger
from waterfallhunter.core.lifecycle_v2_shadow_store import LifecycleV2ShadowStore
from waterfallhunter.core.multi_exchange import MultiExchangeGateway
from waterfallhunter.core.multi_exchange_validator import MultiExchangeValidator
from waterfallhunter.core.production_evidence import ProductionEvidenceRecorder
from waterfallhunter.core.risk_manager import recommend_signal_leverage
from waterfallhunter.core.stage_lifecycle import StageLifecycleStore
from waterfallhunter.discovery.lbank_scanner import LBankCatalogScanner


NOW = 1_788_200_000.0
SYMBOL = "TEST/USDT:USDT"
MARKET_ID = "TESTUSDT"
TIMEFRAME_MS = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}


def _raw_candles(timeframe: str) -> list[list[float]]:
    gap = TIMEFRAME_MS[timeframe]
    count = 120
    last_start = int(NOW * 1000) - gap
    first_start = last_start - (count - 1) * gap
    rows: list[list[float]] = []
    for index in range(count):
        if index < 60:
            opening, high, low, close, volume = 80.3, 80.6, 79.9, 80.1, 100.0
        elif index == 60:
            opening, high, low, close, volume = 100.0, 130.0, 99.0, 120.0, 320.0
        elif index < 97:
            opening, high, low, close, volume = 105.2, 105.5, 104.5, 105.0, 100.0
        elif index < 117:
            opening, high, low, close, volume = 100.3, 100.5, 99.0, 100.1, 100.0
        elif index == 117:
            opening, high, low, close, volume = 98.8, 99.0, 97.5, 98.0, 150.0
        elif index == 118:
            opening, high, low, close, volume = 98.7, 99.2, 97.8, 98.3, 220.0
        else:
            opening, high, low, close, volume = 98.2, 98.8, 97.0, 97.4, 500.0
        rows.append(
            [first_start + index * gap, opening, high, low, close, volume]
        )
    return rows


class _FakeExchange:
    def __init__(self, exchange_id: str, *, complete: bool = True) -> None:
        self.id = exchange_id
        self.complete = complete
        self.markets = {
            SYMBOL: {
                "id": MARKET_ID,
                "active": True,
                "linear": True,
                "swap": True,
                "settle": "USDT",
                "contractSize": 1.0,
                "limits": {
                    "amount": {"min": 0.01},
                    "cost": {"min": 5.0},
                    "leverage": {"max": 20},
                },
                "precision": {"amount": 0.01, "price": 0.01},
                "info": {"maxLeverage": "20"},
            }
        }

    async def fetch_ticker(self, symbol: str) -> dict:
        if not self.complete:
            raise RuntimeError("controlled ticker outage")
        return {
            "symbol": symbol,
            "last": 97.4,
            "mark": 97.4,
            "vwap": 110.0,
            "quoteVolume": 5_000_000.0,
        }

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> dict:
        del symbol, limit
        if not self.complete:
            raise RuntimeError("controlled orderbook outage")
        return {
            "timestamp": int(NOW * 1000),
            "bids": [[97.39, 1000.0], [97.38, 1000.0]],
            "asks": [[97.41, 1000.0], [97.42, 1000.0]],
        }

    async def fetch_trades(self, symbol: str, limit: int = 100) -> list[dict]:
        del limit
        if not self.complete:
            raise RuntimeError("controlled trades outage")
        trades = []
        for index in range(30):
            side = "sell" if index < 24 else "buy"
            price = 97.39 if side == "sell" else 97.41
            amount = 10.0 if side == "sell" else 2.0
            trades.append(
                {
                    "id": str(index),
                    "timestamp": int(NOW * 1000) - index * 1000,
                    "symbol": symbol,
                    "side": side,
                    "price": price,
                    "amount": amount,
                    "cost": price * amount,
                    "takerOrMaker": "taker",
                }
            )
        return trades

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "5m",
        limit: int = 120,
        **_: object,
    ) -> list[list[float]]:
        del symbol, limit
        if not self.complete:
            raise RuntimeError("controlled candle outage")
        return _raw_candles(timeframe)

    async def fapiPublicGetFundingRate(self, _: dict) -> list[dict]:
        return [
            {
                "symbol": MARKET_ID,
                "fundingTime": int((NOW - 8 * 3600) * 1000),
                "fundingRate": "0.0002",
            },
            {
                "symbol": MARKET_ID,
                "fundingTime": int((NOW - 60) * 1000),
                "fundingRate": "0.0008",
            },
        ]

    async def fapiDataGetTakerlongshortRatio(self, _: dict) -> list[dict]:
        return [
            {
                "symbol": MARKET_ID,
                "timestamp": int((NOW - 3600) * 1000),
                "buySellRatio": "1.2",
            },
            {
                "symbol": MARKET_ID,
                "timestamp": int((NOW - 60) * 1000),
                "buySellRatio": "0.7",
            },
        ]

    async def fapiDataGetTopLongShortAccountRatio(self, _: dict) -> list[dict]:
        return [
            {
                "symbol": MARKET_ID,
                "timestamp": int((NOW - 60) * 1000),
                "longShortRatio": "2.5",
            }
        ]

    async def fapiDataGetOpenInterestHist(self, _: dict) -> list[dict]:
        return [
            {
                "symbol": MARKET_ID,
                "timestamp": int((NOW - 3600) * 1000),
                "sumOpenInterestValue": "1000000",
                "sumOpenInterest": "10000",
            },
            {
                "symbol": MARKET_ID,
                "timestamp": int((NOW - 60) * 1000),
                "sumOpenInterestValue": "1200000",
                "sumOpenInterest": "12000",
            },
        ]


class _FakeWebSocketManager:
    def get_realtime_orderbook(self, *_: object) -> None:
        return None

    def get_realtime_liquidation_flow(self, *_: object, **__: object) -> None:
        return None

    def subscribe(self, *_: object) -> None:
        return None

    def unsubscribe(self, *_: object) -> None:
        return None

    def subscribe_shared_evidence(self, *_: object) -> bool:
        return True

    def unsubscribe_shared_evidence(self, *_: object) -> None:
        return None

    def has_direct_evidence_subscription(self, *_: object) -> bool:
        return False


class _ExecutionSuitability:
    def for_symbol(self, _: str) -> dict:
        return {
            "available": True,
            "status": "SUITABLE",
            "reason": "deterministic complete execution evidence",
            "evidence_status": "SUFFICIENT",
            "observed_samples": 40,
            "observation_span_hours": 48.0,
            "maximum_leverage": 20,
        }

    def for_symbols(self, symbols) -> dict[str, dict]:
        return {symbol: self.for_symbol(symbol) for symbol in symbols}


class _ExecutionDecisionLogger:
    @staticmethod
    def observe_evaluation(*_: object, **__: object) -> None:
        return None

    @staticmethod
    def volume_gate_passes(value: object) -> bool:
        return isinstance(value, (int, float)) and float(value) >= 2_000_000.0

    @staticmethod
    def comparison_kind(*_: object, **__: object) -> str:
        return "UNKNOWN"


def _validator(db_path: str, *, complete: bool) -> MultiExchangeValidator:
    validator = MultiExchangeValidator()
    validator.microstructure.snapshot_delay_seconds = 0.0
    validator.ws_manager = _FakeWebSocketManager()
    validator.stage_lifecycle_store = StageLifecycleStore(db_path)

    gateway = MultiExchangeGateway()
    gateway.priority_chain = ["binance", "bybit"]
    gateway._exchanges = {
        "binance": _FakeExchange("binance", complete=complete),
        "bybit": _FakeExchange("bybit", complete=complete),
    }
    gateway._markets_loaded = {"binance": True, "bybit": True}
    validator.gateway = gateway
    return validator


def _install_runtime(monkeypatch, tmp_path: Path, *, complete: bool) -> tuple[DBAdapter, LBankCatalogScanner, EntryDecisionStore, MultiExchangeValidator]:
    db_path = migrate_test_database(tmp_path / "vertical.db")
    db = DBAdapter(str(db_path))
    db.update_candidates(
        {
            SYMBOL: {
                "last_price": 97.4,
                "quote_volume": 5_000_000.0,
                "scan_eligible": True,
            }
        }
    )
    scanner = LBankCatalogScanner(
        db_adapter=db,
        max_price=1_000.0,
        min_volume_usdt=2_000_000.0,
    )
    scanner.active_candidates[SYMBOL] = {
        "last_price": 97.4,
        "reference_observed_at": NOW - 1.0,
        "quote_volume": 5_000_000.0,
        "analysis_status": "pending",
    }
    entry_store = EntryDecisionStore(str(db_path))
    validator = _validator(str(db_path), complete=complete)

    monkeypatch.setattr(time, "time", lambda: NOW)
    monkeypatch.setattr(main, "db", db)
    monkeypatch.setattr(main, "scanner", scanner)
    monkeypatch.setattr(main, "validator", validator)
    monkeypatch.setattr(main, "stage_lifecycle_store", validator.stage_lifecycle_store)
    monkeypatch.setattr(main, "entry_decision_store", entry_store)
    monkeypatch.setattr(main, "lifecycle_v2_shadow_store", LifecycleV2ShadowStore(str(db_path)))
    monkeypatch.setattr(main, "historical_outcome_store", HistoricalOutcomeStore(str(db_path)))
    monkeypatch.setattr(main, "production_evidence_recorder", ProductionEvidenceRecorder(str(db_path), bucket_seconds=900))
    monkeypatch.setattr(main, "signal_ledger", LBankSignalLedger(str(db_path)))
    monkeypatch.setattr(main, "execution_suitability_enricher", _ExecutionSuitability())
    monkeypatch.setattr(main, "execution_decision_logger", _ExecutionDecisionLogger())
    monkeypatch.setattr(main, "_dashboard_event_buffer", DashboardEventBuffer())
    monkeypatch.setattr(main, "_schedule_ai_advisory_observational", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_start_background_task", lambda coro: (coro.close() if hasattr(coro, "close") else None))
    return db, scanner, entry_store, validator


def _seed_entry_ready_predecessor(
    db: DBAdapter,
    entry_store: EntryDecisionStore,
    validator: MultiExchangeValidator,
) -> dict:
    result = asyncio.run(
        validator.cross_check_symbol(
            SYMBOL,
            97.4,
            reference_source="lbank",
            lifecycle_id=1,
        )
    )
    assert result["is_valid"] is True
    assert result["score"] == 100.0
    assert result["suggested_status"] == "TRIGGERED"
    metrics = result["metrics"]
    assert metrics["candle_analysis"]["details"]["5m"]["valid"] is True
    assert metrics["microstructure"]["approved"] is True
    assert metrics["derivatives"]["available"] is True
    assert metrics["strategy_stages"]["passed"] is True
    assert all(metrics["quality_gates"].values())
    assert metrics["position_setup"]["status"] == "READY"

    predecessor = build_entry_decision(
        metrics,
        "ARMED",
        evaluated_at=int(NOW) - 1,
        analysis_age_seconds=0.0,
        reference_age_seconds=0.0,
        lifecycle_id=1,
    )
    assert predecessor["decision"] == "ENTRY_READY"
    event_id = entry_store.append_if_changed(
        SYMBOL,
        predecessor,
        expected_lifecycle_id=1,
    )
    assert isinstance(event_id, int)
    assert db.update_candidate_state(SYMBOL, "ARMED") is True
    return predecessor


def _first_sse_payload() -> dict:
    async def read() -> dict:
        response = await main.stream_candidates(last_event_id=None)
        iterator = response.body_iterator
        try:
            chunk = await anext(iterator)
        finally:
            await iterator.aclose()
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        return json.loads(text.split("data: ", 1)[1])

    return asyncio.run(read())


def test_vertical_success_preserves_identity_plan_leverage_persistence_api_and_sse(monkeypatch, tmp_path: Path) -> None:
    db, scanner, entry_store, validator = _install_runtime(
        monkeypatch,
        tmp_path,
        complete=True,
    )
    predecessor = _seed_entry_ready_predecessor(db, entry_store, validator)
    candidate = db.get_all_active_candidates()[SYMBOL]
    assert candidate["lifecycle_id"] == 1

    original_to_thread = asyncio.to_thread
    offloaded_functions: list[object] = []

    async def tracked_to_thread(func, /, *args, **kwargs):
        offloaded_functions.append(func)
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(main.asyncio, "to_thread", tracked_to_thread)
    asyncio.run(main.evaluate_candidate(SYMBOL, candidate))
    assert any(
        getattr(func, "__self__", None) is main.execution_suitability_enricher
        and getattr(func, "__name__", "") == "for_symbol"
        for func in offloaded_functions
    ), "execution-suitability aggregation must run off the scanner event loop"

    latest = entry_store.latest_for_symbol(SYMBOL)
    assert latest is not None
    assert latest["lifecycle_id"] == predecessor["lifecycle_id"] == candidate["lifecycle_id"]
    assert latest["decision"] == "ACTIVE"
    assert latest["entry_readiness"] >= 78.0
    assert latest["trade_plan"] is not None
    assert latest["trade_plan"]["entry_price"] == 97.33
    assert latest["trade_plan"]["take_profit_1"] == 88.81
    assert latest["trade_plan"]["take_profit_2"] == 80.37
    assert latest["trade_plan"]["stop_loss"] == 105.71

    live_metrics = scanner.active_candidates[SYMBOL]["metrics"]
    assert live_metrics["score"] == 100.0
    assert live_metrics["full_analysis_observed_at"] == NOW
    assert live_metrics["entry_decision"]["lifecycle_id"] == 1
    canonical_leverage = recommend_signal_leverage(
        live_metrics,
        _ExecutionSuitability().for_symbol(SYMBOL),
    )
    assert 4 <= canonical_leverage <= 18
    assert live_metrics["applied_leverage"] == canonical_leverage
    assert live_metrics["leverage_advisory"]["status"] == "AVAILABLE"
    assert live_metrics["leverage_advisory"]["leverage"] == canonical_leverage
    assert live_metrics["leverage_advisory"]["execution_suitability_input"]["status"] == "SUITABLE"
    assert latest["leverage_advisory"]["execution_suitability_input"]["observed_samples"] == 40
    assert latest["trade_plan"]["leverage"] == canonical_leverage

    with sqlite3.connect(db.db_path) as connection:
        persisted_decision = connection.execute(
            "SELECT decision, packet_json FROM entry_decision_events WHERE symbol = ? ORDER BY id DESC LIMIT 1",
            (SYMBOL,),
        ).fetchone()
        signal_count = connection.execute(
            "SELECT COUNT(*) FROM lbank_signal_ledger WHERE symbol = ?",
            (SYMBOL,),
        ).fetchone()[0]
    assert persisted_decision is not None
    assert persisted_decision[0] == "ACTIVE"
    persisted_packet = json.loads(persisted_decision[1])
    assert persisted_packet["lifecycle_id"] == 1
    assert persisted_packet["trade_plan"]["leverage"] == canonical_leverage
    assert signal_count == 1

    snapshot = asyncio.run(main.get_candidates(Response()))
    api_candidate = snapshot.candidates[SYMBOL]
    assert api_candidate["lifecycle_id"] == 1
    assert api_candidate["score"] == 100.0
    assert api_candidate["metrics"]["entry_decision"]["trade_plan"]["leverage"] == canonical_leverage
    assert api_candidate["metrics"]["applied_leverage"] == canonical_leverage
    assert api_candidate["metrics"]["leverage_advisory"]["status"] == "AVAILABLE"

    main._dashboard_event_buffer.publish_snapshot(
        main.get_formatted_candidates(evaluation_time=NOW),
        generated_at=NOW,
        full_snapshot=True,
    )
    event = _first_sse_payload()
    sse_candidate = event["payload"]["candidates"][SYMBOL]
    assert sse_candidate["lifecycle_id"] == api_candidate["lifecycle_id"]
    assert sse_candidate["score"] == api_candidate["score"]
    assert sse_candidate["metrics"]["entry_decision"] == api_candidate["metrics"]["entry_decision"]


def test_vertical_missing_market_evidence_fails_closed_through_api_and_sse(monkeypatch, tmp_path: Path) -> None:
    db, scanner, entry_store, _ = _install_runtime(
        monkeypatch,
        tmp_path,
        complete=False,
    )
    candidate = db.get_all_active_candidates()[SYMBOL]

    asyncio.run(main.evaluate_candidate(SYMBOL, candidate))

    latest = entry_store.latest_for_symbol(SYMBOL)
    assert latest is not None
    assert latest["decision"] == "NO_TRADE"
    assert latest["trade_plan"] is None
    assert "EXECUTION_UNAVAILABLE" in latest["block_reasons"]

    live_metrics = scanner.active_candidates[SYMBOL]["metrics"]
    assert live_metrics.get("position_setup") is None
    assert live_metrics.get("applied_leverage") is None
    assert live_metrics["leverage_advisory"]["status"] == "NOT_RECOMMENDED"
    assert "NO_TRADE" in live_metrics["leverage_advisory"]["reason"]
    assert live_metrics["entry_decision"]["trade_plan"] is None
    assert live_metrics["analysis_reason"] == "no complete live USDT perpetual data source in exchange waterfall"

    snapshot = asyncio.run(main.get_candidates(Response()))
    api_candidate = snapshot.candidates[SYMBOL]
    assert api_candidate["score"] is None
    assert api_candidate["metrics"]["entry_decision"]["trade_plan"] is None
    assert api_candidate["metrics"].get("applied_leverage") is None
    assert api_candidate["metrics"]["leverage_advisory"]["status"] == "NOT_RECOMMENDED"
    assert "UNAVAILABLE" in api_candidate["metrics"]["analysis_reason"].upper() or "NO COMPLETE" in api_candidate["metrics"]["analysis_reason"].upper()

    main._dashboard_event_buffer.publish_snapshot(
        main.get_formatted_candidates(evaluation_time=NOW),
        generated_at=NOW,
        full_snapshot=True,
    )
    event = _first_sse_payload()
    sse_candidate = event["payload"]["candidates"][SYMBOL]
    assert sse_candidate["metrics"]["entry_decision"]["trade_plan"] is None
    assert sse_candidate["metrics"].get("applied_leverage") is None
    assert sse_candidate["metrics"]["leverage_advisory"]["status"] == "NOT_RECOMMENDED"


@pytest.mark.parametrize("status", ["UNAVAILABLE", "NOT_RECOMMENDED"])
def test_leverage_advisory_status_never_becomes_a_strategy_or_signal_gate(
    monkeypatch, tmp_path: Path, status: str
) -> None:
    db, scanner, entry_store, validator = _install_runtime(
        monkeypatch, tmp_path, complete=True
    )
    _seed_entry_ready_predecessor(db, entry_store, validator)
    candidate = db.get_all_active_candidates()[SYMBOL]

    monkeypatch.setattr(
        main,
        "build_signal_leverage_advisory",
        lambda metrics, execution_suitability=None, **kwargs: {
            "policy_version": "adaptive_signal_leverage_v1",
            "minimum": 4,
            "maximum": 18,
            "symbol_agnostic": True,
            "signal_only": True,
            "advisory_only": True,
            "status": status,
            "leverage": None,
            "reason": "controlled advisory outcome",
        },
    )

    asyncio.run(main.evaluate_candidate(SYMBOL, candidate))

    latest = entry_store.latest_for_symbol(SYMBOL)
    assert latest is not None
    assert latest["decision"] == "ACTIVE"
    assert latest["entry_readiness"] == 95.1
    assert latest["trade_plan"] is not None
    assert latest["trade_plan"]["entry_price"] == 97.33
    assert latest["trade_plan"]["take_profit_1"] == 88.81
    assert latest["trade_plan"]["take_profit_2"] == 80.37
    assert latest["trade_plan"]["stop_loss"] == 105.71
    assert latest["trade_plan"]["leverage"] is None

    live_metrics = scanner.active_candidates[SYMBOL]["metrics"]
    assert live_metrics["score"] == 100.0
    assert live_metrics["quality_gates"]["channel_stage_chain"] is True
    assert live_metrics["leverage_advisory"]["status"] == status
    assert live_metrics["applied_leverage"] is None

    with sqlite3.connect(db.db_path) as connection:
        signal_count = connection.execute(
            "SELECT COUNT(*) FROM lbank_signal_ledger WHERE symbol = ?", (SYMBOL,)
        ).fetchone()[0]
    assert signal_count == 1

    api_candidate = asyncio.run(main.get_candidates(Response())).candidates[SYMBOL]
    assert api_candidate["metrics"]["leverage_advisory"]["status"] == status
    assert api_candidate["metrics"]["entry_decision"]["decision"] == "ACTIVE"
    assert api_candidate["metrics"]["entry_decision"]["trade_plan"]["leverage"] is None


def test_progressive_watch_fast_path_is_fail_closed_and_skips_full_validator(monkeypatch, tmp_path: Path) -> None:
    db, scanner, entry_store, validator = _install_runtime(
        monkeypatch, tmp_path, complete=True
    )
    scanner.active_candidates[SYMBOL]["metrics"] = {
        "exchange": "binance",
        "mapped_symbol": SYMBOL,
        "full_analysis_observed_at": NOW - 60.0,
    }
    candidate = db.get_all_active_candidates()[SYMBOL]

    async def probe(*args, **kwargs):
        return {
            "conclusive": True,
            "requires_full_validation": False,
            "reason": "controlled no-hype probe",
            "result": {
                "is_valid": False,
                "score": None,
                "suggested_status": "REJECTED",
                "observation_status": "WATCH",
                "observation_score": None,
                "metrics": {
                    "exchange": "binance",
                    "mapped_symbol": SYMBOL,
                    "ticker": {"last": 97.4, "vwap": 100.0},
                    "candle_analysis": {"details": _details_for_fast_watch()},
                    "strategy_stages": {
                        "hype": False, "damage": False, "setup": False,
                        "setup_type": None, "trigger": False, "passed": False,
                    },
                    "trade_eligible": False,
                    "analysis_reason": "watch prefilter: no hype context",
                    "watch_prefilter": {
                        "version": "watch_prefilter_v1",
                        "observational_only": True,
                        "hard_gating_allowed": False,
                        "conclusive_no_hype": True,
                    },
                },
                "_runtime_diagnostics": {
                    "source_attempts": 1, "ws_evidence_hits": 0,
                    "rest_evidence_fallbacks": 0, "outcome": "complete",
                    "stage_durations_seconds": {"candles": 0.01, "total": 0.01},
                },
            },
        }

    async def forbidden_full(*args, **kwargs):
        raise AssertionError("full validator must not run for conclusive no-hype WATCH")

    subscribed: list[tuple[str, str]] = []
    monkeypatch.setattr(validator, "watch_prefilter", probe, raising=False)
    monkeypatch.setattr(validator, "cross_check_symbol", forbidden_full)
    monkeypatch.setattr(validator.ws_manager, "subscribe", lambda *args: subscribed.append(args))
    monkeypatch.setattr(validator.ws_manager, "subscribe_shared_evidence", lambda *args: subscribed.append(args) or True)

    asyncio.run(main.evaluate_candidate(SYMBOL, candidate))

    latest = entry_store.latest_for_symbol(SYMBOL)
    assert latest is not None
    assert latest["decision"] == "NO_TRADE"
    assert latest["trade_plan"] is None
    assert db.get_all_active_candidates()[SYMBOL]["status"] == "WATCH"
    live = scanner.active_candidates[SYMBOL]
    assert live["analysis_status"] == "observed"
    assert live["metrics"]["watch_prefilter"]["conclusive_no_hype"] is True
    assert live["metrics"]["full_analysis_observed_at"] == NOW - 60.0
    assert subscribed == []


def _details_for_fast_watch() -> dict:
    return {
        timeframe: {"valid": True}
        for timeframe in ("5m", "15m", "1h", "4h")
    }


def test_progressive_watch_cold_4h_waterfall_defers_when_revalidation_slot_busy(monkeypatch, tmp_path: Path) -> None:
    db, scanner, entry_store, validator = _install_runtime(
        monkeypatch, tmp_path, complete=True
    )
    monkeypatch.setattr(main, "_watch_source_hint_cache", {})
    candidate = db.get_all_active_candidates()[SYMBOL]

    async def cold_probe(*args, **kwargs):
        return {
            "conclusive": True,
            "requires_full_validation": False,
            "reason": "all compatible 4h sources no-hype",
            "result": {
                "is_valid": False,
                "score": None,
                "suggested_status": "REJECTED",
                "observation_status": "WATCH",
                "observation_score": None,
                "metrics": {
                    "exchange": "binance",
                    "mapped_symbol": SYMBOL,
                    "ticker": {"last": 97.4},
                    "candle_analysis": {
                        "details": {"4h": {"valid": True, "hype_context": False}},
                    },
                    "valid_candle_timeframes": 1,
                    "strategy_stages": {
                        "hype": False, "damage": False, "setup": False,
                        "setup_type": None, "trigger": False, "passed": False,
                    },
                    "trade_eligible": False,
                    "analysis_reason": "watch 4h waterfall prefilter: no hype context",
                    "watch_prefilter": {
                        "version": "watch_prefilter_v2_4h_waterfall",
                        "observational_only": True,
                        "hard_gating_allowed": False,
                        "conclusive_no_hype": True,
                        "valid_no_hype_sources": 2,
                        "sources_checked": 2,
                    },
                },
                "_runtime_diagnostics": {
                    "source_attempts": 2, "ws_evidence_hits": 0,
                    "rest_evidence_fallbacks": 0, "outcome": "complete",
                    "stage_durations_seconds": {"candles": 0.02, "total": 0.02},
                },
            },
        }

    async def forbidden_full(*args, **kwargs):
        raise AssertionError("cold no-hype WATCH must not run full validator")

    monkeypatch.setattr(validator, "watch_prefilter_waterfall", cold_probe, raising=False)
    monkeypatch.setattr(main, "_claim_watch_full_revalidation_slot", lambda _now: False)
    monkeypatch.setattr(validator, "cross_check_symbol", forbidden_full)
    asyncio.run(main.evaluate_candidate(SYMBOL, candidate))

    latest = entry_store.latest_for_symbol(SYMBOL)
    assert latest is not None
    assert latest["decision"] == "NO_TRADE"
    assert db.get_all_active_candidates()[SYMBOL]["status"] == "WATCH"
    live = scanner.active_candidates[SYMBOL]
    assert live["metrics"]["watch_prefilter"]["version"] == "watch_prefilter_v2_4h_waterfall"
    assert live["metrics"]["watch_prefilter"]["full_revalidation_deferred"] is True
    assert "full_analysis_observed_at" not in live["metrics"]
    assert live["metrics"]["entry_decision"]["decision"] == "NO_TRADE"



def test_progressive_watch_cold_4h_waterfall_claims_slot_and_runs_full_validator(monkeypatch, tmp_path: Path) -> None:
    db, scanner, _entry_store, validator = _install_runtime(
        monkeypatch, tmp_path, complete=True
    )
    monkeypatch.setattr(main, "_watch_source_hint_cache", {})
    candidate = db.get_all_active_candidates()[SYMBOL]

    async def cold_probe(*args, **kwargs):
        return {
            "conclusive": True,
            "requires_full_validation": False,
            "reason": "all compatible 4h sources no-hype",
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

    original_full = validator.cross_check_symbol
    calls: list[str] = []
    async def counted_full(*args, **kwargs):
        calls.append("full")
        return await original_full(*args, **kwargs)

    monkeypatch.setattr(validator, "watch_prefilter_waterfall", cold_probe, raising=False)
    monkeypatch.setattr(main, "_claim_watch_full_revalidation_slot", lambda _now: True)
    monkeypatch.setattr(validator, "cross_check_symbol", counted_full)
    asyncio.run(main.evaluate_candidate(SYMBOL, candidate))
    assert calls == ["full"]

def test_progressive_watch_cold_hype_forces_canonical_full_validator(monkeypatch, tmp_path: Path) -> None:
    db, scanner, entry_store, validator = _install_runtime(
        monkeypatch, tmp_path, complete=True
    )
    monkeypatch.setattr(main, "_watch_source_hint_cache", {})
    candidate = db.get_all_active_candidates()[SYMBOL]

    async def hype_probe(*args, **kwargs):
        return {
            "conclusive": True,
            "requires_full_validation": True,
            "reason": "watch prefilter detected hype context",
            "result": None,
        }

    original_full = validator.cross_check_symbol
    calls: list[str] = []
    async def counted_full(*args, **kwargs):
        calls.append("full")
        return await original_full(*args, **kwargs)

    monkeypatch.setattr(validator, "watch_prefilter_waterfall", hype_probe, raising=False)
    monkeypatch.setattr(validator, "cross_check_symbol", counted_full)
    asyncio.run(main.evaluate_candidate(SYMBOL, candidate))

    assert calls == ["full"]
    live = scanner.active_candidates[SYMBOL]
    assert live["metrics"]["full_analysis_observed_at"] == NOW
    assert live["metrics"].get("watch_prefilter") is None
    assert entry_store.latest_for_symbol(SYMBOL) is not None
