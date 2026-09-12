import asyncio
import sqlite3
import time

import pytest

from schema_test_support import migrate_test_database
from waterfallhunter.core.candle_analyzer import MultiTimeframeAnalyzer
from waterfallhunter.core.derivatives import DerivativesAnalyzer
from waterfallhunter.core.feature_replay import (
    EQUIVALENT,
    MISMATCH,
    NOT_REPLAYABLE,
    FeatureReplayEngine,
    FeatureReplayStore,
    FeatureReplayWorker,
)
from waterfallhunter.core.microstructure import MicrostructureAnalyzer
from waterfallhunter.core.multi_exchange_validator import MultiExchangeValidator
from waterfallhunter.core.position_calculator import PositionCalculator
from waterfallhunter.core.production_evidence import ProductionEvidenceRecorder


class Exchange:
    def __init__(self, book, trades):
        self.book = book
        self.trades = trades

    async def fetch_order_book(self, symbol, limit):
        return {**self.book, "timestamp": int(time.time() * 1000)}

    async def fetch_trades(self, symbol, limit):
        return [{**row, "timestamp": int(time.time() * 1000)} for row in self.trades]


async def _payload():
    analyzer = MultiTimeframeAnalyzer()
    gaps = analyzer.timeframe_ms
    rows = {
        timeframe: [[index * gap + gap, 10.0, 10.5, 9.5, 9.8, 100.0] for index in range(120)]
        for timeframe, gap in gaps.items()
    }
    details = {timeframe: analyzer._evaluate(series) for timeframe, series in rows.items()}
    confirmation = analyzer._evaluate(rows["15m"])
    cross = all(confirmation[name] for name in ("two_closed_candles", "lower_high", "bearish_close"))
    breakdown = sum(int(packet["is_bearish"]) for packet in details.values())
    candle_result = {
        "is_breakdown_confirmed": breakdown >= 2 and cross,
        "breakdown_score": breakdown,
        "cross_exchange_confirmed": cross,
        "details": details,
    }
    stages = analyzer.channel_stages(details)
    now_ms = int(time.time() * 1000)
    book = {"timestamp": now_ms, "bids": [[10.0, 100.0]], "asks": [[10.1, 100.0]]}
    trades = [{"timestamp": now_ms, "side": "sell", "price": 10.0, "amount": 1.0} for _ in range(20)]
    market = {"contractSize": 1.0, "limits": {"amount": {"min": 0.01}, "cost": {"min": 1.0}}, "precision": {}}
    micro = await MicrostructureAnalyzer(snapshot_delay_seconds=0).analyze(Exchange(book, trades), "TEST/USDT:USDT", dict(book), market)
    trade_source = micro.pop("source_capture")
    micro.pop("observed_at", None)
    funding_rows = [
        {"symbol": "TESTUSDT", "fundingTime": now_ms - offset, "fundingRate": rate}
        for offset, rate in ((57_600_000, "0.0003"), (28_800_000, "0.0002"), (0, "0.0001"))
    ]
    taker_rows = [
        {"symbol": "TESTUSDT", "timestamp": now_ms - (12 - index) * 300_000, "buySellRatio": "0.7"}
        for index in range(13)
    ]
    top_rows = [{"symbol": "TESTUSDT", "timestamp": now_ms, "longShortRatio": "1.5"}]
    oi_rows = [
        {
            "symbol": "TESTUSDT",
            "timestamp": now_ms - (12 - index) * 300_000,
            "sumOpenInterest": str(1000 - index),
            "sumOpenInterestValue": str(1_000_000 - index * 1000),
        }
        for index in range(13)
    ]
    retrieved_at = now_ms / 1000.0
    derivatives = DerivativesAnalyzer().evaluate_binance_rows(
        mapped_symbol="TEST/USDT:USDT",
        market_id="TESTUSDT",
        funding_rows=funding_rows,
        taker_rows=taker_rows,
        top_trader_rows=top_rows,
        open_interest_rows=oi_rows,
        retrieved_at=retrieved_at,
    )
    derivative_source = {
        "provider": "binance",
        "mapped_symbol": "TEST/USDT:USDT",
        "market_id": "TESTUSDT",
        "retrieved_at": retrieved_at,
        "funding_rows": funding_rows,
        "taker_rows": taker_rows,
        "top_trader_rows": top_rows,
        "open_interest_rows": oi_rows,
    }
    ticker = {"last": 9.8, "vwap": 10.0, "quoteVolume": 1_000_000.0}
    validator = object.__new__(MultiExchangeValidator)
    score = validator._merge_score_v2(
        candles=details,
        microstructure=micro,
        derivatives=derivatives,
        cross_exchange_confirmed=candle_result["is_breakdown_confirmed"],
        ticker=ticker,
        reference_price=9.8,
        strategy_stages=stages,
    )
    valid = score["is_valid"]
    return {
        "symbol": "TEST/USDT:USDT",
        "reference_price": 9.8,
        "result": {"is_valid": valid, "suggested_status": "REJECTED" if not valid else validator._suggested_status(score["score"], stages, micro["approved"], candle_result["is_breakdown_confirmed"])},
        "capture_limitations": {"feature_replay_ready": True},
        "metrics": {
            "mapped_symbol": "TEST/USDT:USDT",
            "orderbook": book,
            "ticker": ticker,
            "candle_analysis": candle_result,
            "microstructure": micro,
            "derivatives": derivatives,
            "strategy_stages": stages,
            "score_version": score["score_version"],
            "score": score["score"] if valid else None,
            "score_components": score["score_components"] if valid else {},
            "quality_gates": {"live_orderbook": True, **score["quality_gates"]},
            "analysis_reason": score["reason"],
            "source_capture": {
                "candles": {"primary_closed_ohlcv": rows, "confirmation_closed_ohlcv_15m": rows["15m"]},
                "trades": trade_source,
                "derivatives": {"selected": derivative_source, "fallback_attempts": []},
            },
        },
    }


def test_replay_uses_production_analyzers_and_marks_exact_packet_equivalent():
    result = asyncio.run(FeatureReplayEngine().replay(asyncio.run(_payload())))
    assert result["status"] == EQUIVALENT
    assert result["strategy_equivalent"] is True
    assert result["differences"] == {}


def test_code_hash_compatibility_requires_explicit_audited_hash():
    payload = asyncio.run(_payload())
    audited_hash = "a" * 64
    payload["decision_contract"] = {
        "application": {"source_tree_sha256": audited_hash}
    }

    strict = asyncio.run(FeatureReplayEngine().replay(payload))
    assert strict["status"] == NOT_REPLAYABLE

    audited = asyncio.run(
        FeatureReplayEngine(
            audited_compatible_code_hashes={audited_hash}
        ).replay(payload)
    )
    assert audited["status"] == EQUIVALENT


def test_replay_detects_production_score_mismatch():
    payload = asyncio.run(_payload())
    payload["metrics"]["score_version"] = "tampered"
    result = asyncio.run(FeatureReplayEngine().replay(payload))
    assert result["status"] == MISMATCH
    assert "score_version" in result["differences"]


def test_replay_matches_production_derivatives_short_circuit():
    payload = asyncio.run(_payload())
    reason = "no complete live derivatives data source in exchange waterfall"
    attempted = {
        "exchange": "binance",
        "mapped_symbol": "TEST/USDT:USDT",
        "market_id": "TESTUSDT",
        "retrieved_at": None,
        "reason": "price incompatible with reference",
    }
    payload["metrics"]["derivatives"] = {
        "available": False,
        "reason": reason,
        "source_exchange": None,
        "mapped_symbol": None,
        "market_id": None,
        "retrieved_at": None,
        "fallback_attempts": [attempted],
    }
    payload["metrics"]["source_capture"]["derivatives"] = {
        "selected": None,
        "fallback_attempts": [attempted],
    }
    payload["metrics"]["score"] = None
    payload["metrics"]["score_components"] = {}
    payload["metrics"]["quality_gates"] = {"complete_fresh_derivatives_packet": False}
    payload["metrics"]["analysis_reason"] = reason
    payload["result"]["is_valid"] = False
    payload["result"]["suggested_status"] = "REJECTED"

    result = asyncio.run(FeatureReplayEngine().replay(payload))
    assert result["status"] == EQUIVALENT
    assert result["differences"] == {}


def test_replay_distinguishes_trigger_candidate_from_persisted_trigger():
    payload = asyncio.run(_payload())
    evaluated_at_ms = int(time.time() * 1000)
    history = [
        [evaluated_at_ms - (1001 - index) * 300_000, 10.0, 10.5, 9.0, 9.8, 100.0]
        for index in range(1000)
    ]
    payload["metrics"]["ticker"]["mark"] = None
    payload["metrics"]["ticker"]["last"] = 9.8
    payload["metrics"]["source_capture"]["position"] = {
        "attempted": True,
        "timeframe": "5m",
        "requested_limit": 1000,
        "evaluated_at_ms": evaluated_at_ms,
        "raw_ohlcv": history,
    }
    micro = payload["metrics"]["microstructure"]
    market = payload["metrics"]["source_capture"]["trades"]["market"]
    position = PositionCalculator().calculate_short_position(
        micro["best_bid"],
        recent_high=max(row[2] for row in history[-24:]),
        market_info=market,
        mark_price=9.8,
        entry_slippage_pct=micro["entry_slippage_pct"],
        exit_slippage_pct=micro["exit_slippage_pct"],
    )
    payload["metrics"]["position_setup"] = position
    if str(position.get("status", "")).startswith("REJECTED"):
        payload["result"]["suggested_status"] = "WATCH"

    result = asyncio.run(FeatureReplayEngine().replay(payload))
    assert result["status"] == EQUIVALENT
    assert result["decision_path"] == "TRIGGER_CANDIDATE"

    payload["metrics"]["production_decision"] = {
        "final": True,
        "path": "TRIGGERED",
        "recorded_after_persistence": True,
        "signal_id": 42,
    }
    result = asyncio.run(FeatureReplayEngine().replay(payload))
    assert result["status"] == EQUIVALENT
    assert result["decision_path"] == "TRIGGERED"


def test_replay_results_are_idempotent_immutable_and_not_promotable(tmp_path):
    db_path = migrate_test_database(tmp_path / "replay.db")
    store = FeatureReplayStore(str(db_path))
    with store._connect() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "INSERT INTO production_feature_replay_results_v2 (snapshot_id,symbol,status,strategy_equivalent,differences_json,replay_version,replayed_at,observational_only,hard_gating_allowed,trade_eligible) VALUES (1,'TEST','EQUIVALENT',1,'{}',?,1,1,0,NULL)",
            (FeatureReplayEngine.VERSION,),
        )
    report = store.build_report()
    assert report["equivalent_count"] == 1
    assert report["strategy_equivalent"] is False
    assert report["triggered_equivalent_count"] == 0
    assert report["promotion_allowed"] is False
    with store._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM production_feature_replay_results_v2")


def test_v9_unavailable_replay_row_is_queued_and_reported_not_replayable(tmp_path):
    db_path = migrate_test_database(tmp_path / "replay-unavailable.db")
    recorder = ProductionEvidenceRecorder(str(db_path))
    contract = {
        "contract_schema_version": "production_decision_contract_v2",
        "application": {"source_tree_sha256": "a" * 64},
        "strategy": {},
        "microstructure": {},
        "derivatives": {},
        "position": {},
        "recorder": {},
        "runtime_settings": {},
    }
    assert recorder.record(
        "TEST/USDT:USDT",
        candidate_state="WATCH",
        reference_source=None,
        reference_price=None,
        result={"is_valid": False, "metrics": {"error": "reference unavailable"}},
        decision_contract=contract,
        observed_at=110.0,
        replay_context=None,
    )

    pending = FeatureReplayStore(str(db_path)).pending(limit=10)

    assert len(pending) == 1
    result = asyncio.run(FeatureReplayEngine().replay(pending[0]["payload"]))
    assert result["status"] == NOT_REPLAYABLE
    assert result["differences"] == {
        "replay_context": "REPLAY_CONTEXT_ABSENT",
    }

class _FrontierStore:
    def __init__(self, batches, next_snapshot_id=999):
        self.batches = list(batches)
        self.pending_starts = []
        self.appended = []
        self.next_id = next_snapshot_id

    def pending(self, limit=3, *, start_snapshot_id=None):
        self.pending_starts.append(start_snapshot_id)
        if not self.batches:
            return []
        return self.batches.pop(0)[:limit]

    def append(self, snapshot, result):
        self.appended.append((snapshot["id"], result["status"]))
        return True

    def next_snapshot_id(self):
        return self.next_id


class _EquivalentReplayEngine:
    async def replay(self, payload):
        return {
            "version": FeatureReplayEngine.VERSION,
            "status": EQUIVALENT,
            "strategy_equivalent": True,
            "differences": {},
            "decision_path": "UNKNOWN",
            "observational_only": True,
            "hard_gating_allowed": False,
        }


class _RejectingFrontierStore(_FrontierStore):
    def append(self, snapshot, result):
        self.appended.append((snapshot["id"], result["status"]))
        return False


def test_feature_replay_worker_does_not_advance_past_unpersisted_result():
    store = _RejectingFrontierStore(
        [[
            {"id": 100, "symbol": "A", "payload": {}},
            {"id": 101, "symbol": "B", "payload": {}},
        ]]
    )
    worker = FeatureReplayWorker(store, batch_size=2)
    worker.engine = _EquivalentReplayEngine()

    assert asyncio.run(worker.run_once()) == 0
    assert worker._next_snapshot_id == 100
    assert store.appended == [(100, EQUIVALENT)]


def test_feature_replay_worker_offloads_store_io(monkeypatch):
    store = _FrontierStore([[{"id": 100, "symbol": "A", "payload": {}}]])
    worker = FeatureReplayWorker(store, batch_size=1)
    worker.engine = _EquivalentReplayEngine()
    offloaded = []
    original_to_thread = asyncio.to_thread

    async def tracked_to_thread(func, /, *args, **kwargs):
        offloaded.append(getattr(func, "__name__", repr(func)))
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", tracked_to_thread)

    assert asyncio.run(worker.run_once()) == 1
    assert "pending" in offloaded
    assert "append" in offloaded


def test_feature_replay_worker_reuses_monotonic_frontier_after_initial_scan():
    store = _FrontierStore(
        [
            [
                {"id": 100, "symbol": "A", "payload": {}},
                {"id": 101, "symbol": "B", "payload": {}},
            ],
            [],
        ]
    )
    worker = FeatureReplayWorker(store, batch_size=2)
    worker.engine = _EquivalentReplayEngine()

    assert asyncio.run(worker.run_once()) == 2
    assert asyncio.run(worker.run_once()) == 0
    assert store.pending_starts == [None, 102]


def test_feature_replay_worker_avoids_repeating_full_scan_after_empty_initial_scan():
    store = _FrontierStore([[], []], next_snapshot_id=700)
    worker = FeatureReplayWorker(store, batch_size=2)
    worker.engine = _EquivalentReplayEngine()

    assert asyncio.run(worker.run_once()) == 0
    assert asyncio.run(worker.run_once()) == 0
    assert store.pending_starts == [None, 700]


def test_feature_replay_pending_supports_primary_key_frontier(tmp_path):
    db_path = migrate_test_database(tmp_path / "replay-frontier.db")
    recorder = ProductionEvidenceRecorder(str(db_path), bucket_seconds=60)
    contract = {
        "contract_schema_version": "production_decision_contract_v2",
        "application": {"source_tree_sha256": "a" * 64},
        "strategy": {},
        "microstructure": {},
        "derivatives": {},
        "position": {},
        "recorder": {},
        "runtime_settings": {},
    }
    for index in range(4):
        assert recorder.record(
            f"TEST{index}/USDT:USDT",
            candidate_state="WATCH",
            reference_source=None,
            reference_price=None,
            result={"is_valid": False, "metrics": {"error": "reference unavailable"}},
            decision_contract=contract,
            observed_at=100.0 + index * 60,
            replay_context=None,
        )

    store = FeatureReplayStore(str(db_path))
    all_pending = store.pending(limit=10)
    assert len(all_pending) == 4
    frontier = all_pending[2]["id"]

    pending = store.pending(limit=10, start_snapshot_id=frontier)

    assert [row["id"] for row in pending] == [
        all_pending[2]["id"],
        all_pending[3]["id"],
    ]
