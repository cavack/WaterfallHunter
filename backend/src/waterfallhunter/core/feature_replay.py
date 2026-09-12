import asyncio
import json
import logging
import math
import sqlite3
import time
import zlib
from typing import Any

from waterfallhunter.core.candle_analyzer import MultiTimeframeAnalyzer
from waterfallhunter.core.coinglass import CoinGlassDerivativesClient
from waterfallhunter.core.decision_provenance import source_tree_sha256
from waterfallhunter.core.derivatives import DerivativesAnalyzer
from waterfallhunter.core.managed_sqlite import connect_managed_sqlite
from waterfallhunter.core.microstructure import MicrostructureAnalyzer
from waterfallhunter.core.multi_exchange_validator import MultiExchangeValidator
from waterfallhunter.core.position_calculator import PositionCalculator
from waterfallhunter.core.schema_contract import require_managed_schema


logger = logging.getLogger("WaterfallHunter.FeatureReplay")

EQUIVALENT = "EQUIVALENT"
MISMATCH = "MISMATCH"
NOT_REPLAYABLE = "NOT_REPLAYABLE"
ERROR = "ERROR"


class _CapturedExchange:
    def __init__(self, snapshots: list[dict], trades: list[dict]):
        self.snapshots = [dict(item) for item in snapshots]
        self.trades = [dict(item) for item in trades]
        self.index = 1

    async def fetch_order_book(self, symbol: str, limit: int):
        item = dict(self.snapshots[self.index])
        self.index += 1
        item["timestamp"] = int(time.time() * 1000)
        return item

    async def fetch_trades(self, symbol: str, limit: int):
        now_ms = int(time.time() * 1000)
        return [{**item, "timestamp": now_ms} for item in self.trades]


class FeatureReplayEngine:
    VERSION = "feature_equivalent_replay_v11"

    def __init__(self, *, audited_compatible_code_hashes: set[str] | None = None):
        self.audited_compatible_code_hashes = frozenset(
            str(value)
            for value in (audited_compatible_code_hashes or set())
            if isinstance(value, str) and len(value) == 64
        )

    @classmethod
    def _normalized(cls, value: Any) -> Any:
        if isinstance(value, float):
            return round(value, 10) if math.isfinite(value) else None
        if isinstance(value, dict):
            return {str(key): cls._normalized(item) for key, item in sorted(value.items())}
        if isinstance(value, (list, tuple)):
            return [cls._normalized(item) for item in value]
        return value

    @classmethod
    def _diff(cls, expected: dict, actual: dict) -> dict:
        result = {}
        for key in sorted(set(expected) | set(actual)):
            left = cls._normalized(expected.get(key))
            right = cls._normalized(actual.get(key))
            if left != right:
                result[key] = {"production": left, "replay": right}
        return result

    @staticmethod
    def _without_runtime_fields(packet: dict) -> dict:
        result = dict(packet)
        result.pop("observed_at", None)
        result.pop("source_capture", None)
        return result

    @staticmethod
    def _derivatives_without_source(packet: dict) -> dict:
        result = dict(packet)
        result.pop("source_capture", None)
        attempts = []
        for attempt in result.get("fallback_attempts") or []:
            clean = dict(attempt)
            clean.pop("source_capture", None)
            attempts.append(clean)
        result["fallback_attempts"] = attempts
        return result

    @classmethod
    def _replay_derivatives(cls, captured: dict) -> dict:
        selected = captured.get("selected") or {}
        attempts = [
            {key: value for key, value in attempt.items() if key != "source_capture"}
            for attempt in (captured.get("fallback_attempts") or [])
            if isinstance(attempt, dict)
        ]
        provider = str(selected.get("provider") or "")
        analyzer = DerivativesAnalyzer()
        if provider == "binance":
            result = analyzer.evaluate_binance_rows(
                mapped_symbol=str(selected.get("mapped_symbol") or ""),
                market_id=str(selected.get("market_id") or ""),
                funding_rows=selected.get("funding_rows"),
                taker_rows=selected.get("taker_rows"),
                top_trader_rows=selected.get("top_trader_rows"),
                open_interest_rows=selected.get("open_interest_rows"),
                retrieved_at=float(selected.get("retrieved_at") or 0.0),
            )
        elif provider.startswith("coinglass:"):
            retrieved_at = float(selected.get("retrieved_at") or 0.0)
            funding = CoinGlassDerivativesClient._funding(
                selected.get("funding_payload"), retrieved_at
            )
            oi = CoinGlassDerivativesClient._open_interest(
                selected.get("open_interest_payload"), retrieved_at
            )
            taker = CoinGlassDerivativesClient._taker_ratio(
                selected.get("taker_payload"), retrieved_at
            )
            taker_change = CoinGlassDerivativesClient._taker_ratio_change(
                selected.get("taker_payload"), retrieved_at
            )
            top = CoinGlassDerivativesClient._top_trader_ratio(
                selected.get("top_accounts_payload"), retrieved_at
            )
            result = analyzer.evaluate_packet(
                exchange=provider,
                mapped_symbol=str(selected.get("mapped_symbol") or ""),
                market_id=str(selected.get("market_id") or ""),
                funding_history=funding[0] if funding else None,
                current_funding=funding[1] if funding else None,
                current_oi=oi[0] if oi else None,
                oi_one_hour_ago=oi[1] if oi else None,
                taker_buy_sell_ratio=taker,
                top_trader_long_short_ratio=top,
                retrieved_at=retrieved_at,
                taker_ratio_change_1h=taker_change,
            )
        else:
            result = {
                "available": False,
                "reason": "no complete live derivatives data source in exchange waterfall",
                "source_exchange": None,
                "mapped_symbol": None,
                "market_id": None,
                "retrieved_at": None,
            }
        result["fallback_attempts"] = attempts
        return result

    async def replay(self, payload: dict) -> dict:
        metrics = payload.get("metrics") or {}
        source = metrics.get("source_capture") or {}
        position_source = source.get("position") or {}
        position_attempted = position_source.get("attempted") is True
        production_decision = metrics.get("production_decision") or {}
        final_path = (
            str(production_decision.get("path") or "")
            if production_decision.get("final") is True
            else ""
        )
        decision_path = (
            final_path
            or (
                "TRIGGER_CANDIDATE"
                if position_attempted
                else str((payload.get("result") or {}).get("suggested_status") or "UNKNOWN")
            )
        )
        if (
            payload.get("schema_version") == "production_decision_evidence_v9"
            and payload.get("replay_complete") is not True
        ):
            unavailable_reason = payload.get("replay_unavailable_reason")
            if not isinstance(unavailable_reason, str) or not unavailable_reason:
                unavailable_reason = (
                    "REPLAY_CONTEXT_ABSENT"
                    if not payload.get("replay_context")
                    else "REPLAY_COMPLETENESS_UNDECLARED"
                )
            return self._packet(
                NOT_REPLAYABLE,
                {"replay_context": unavailable_reason},
                decision_path,
            )
        limitations = payload.get("capture_limitations") or {}
        evidence_code = str(
            (payload.get("decision_contract") or {})
            .get("application", {})
            .get("source_tree_sha256", "")
        )
        current_code = source_tree_sha256()[0]
        if (
            evidence_code
            and evidence_code != current_code
            and evidence_code not in self.audited_compatible_code_hashes
        ):
            return self._packet(
                NOT_REPLAYABLE,
                {"code_generation": {"evidence": evidence_code, "replay": current_code}},
                decision_path,
            )
        if limitations.get("feature_replay_ready") is not True:
            return self._packet(
                NOT_REPLAYABLE,
                {"capture": "feature replay source packet incomplete"},
                decision_path,
            )

        candle_source = source.get("candles") or {}
        trade_source = source.get("trades") or {}
        primary = candle_source.get("primary_closed_ohlcv") or {}
        confirmation_rows = candle_source.get("confirmation_closed_ohlcv_15m")
        snapshots = trade_source.get("orderbook_snapshots") or []
        trades = trade_source.get("fresh_trades") or []
        market = trade_source.get("market") or {}
        if (
            set(primary) != {"5m", "15m", "1h", "4h"}
            or not confirmation_rows
            or len(snapshots) != 3
            or len(trades) < 20
            or not market
        ):
            return self._packet(
                NOT_REPLAYABLE,
                {"capture": "required raw source fields missing"},
                decision_path,
            )

        candle_analyzer = MultiTimeframeAnalyzer()
        candle_result_with_source = candle_analyzer.evaluate_closed_sources(
            primary,
            confirmation_rows,
        )
        candle_result = dict(candle_result_with_source)
        candle_result.pop("source_capture", None)
        details = candle_result["details"]
        stages = candle_analyzer.channel_stages(details)

        replayed_derivatives = self._replay_derivatives(
            source.get("derivatives") or {}
        )
        expected_derivatives = self._derivatives_without_source(
            metrics.get("derivatives") or {}
        )

        now_ms = int(time.time() * 1000)
        fresh_snapshots = [
            {
                "timestamp": now_ms,
                "bids": item.get("bids") or [],
                "asks": item.get("asks") or [],
            }
            for item in snapshots
        ]
        expected_micro = metrics.get("microstructure") or {}
        notional = expected_micro.get("executable_notional")
        micro_analyzer = MicrostructureAnalyzer(
            executable_notional=float(notional if isinstance(notional, (int, float)) else 50.0),
            snapshot_delay_seconds=0.0,
        )
        replayed_micro = await micro_analyzer.analyze(
            _CapturedExchange(fresh_snapshots, trades),
            str(metrics.get("mapped_symbol") or payload.get("symbol")),
            fresh_snapshots[0],
            market,
        )
        replayed_micro = self._without_runtime_fields(replayed_micro)
        expected_micro = self._without_runtime_fields(expected_micro)

        validator = object.__new__(MultiExchangeValidator)
        ticker = metrics.get("ticker") or {}
        score_result = validator._merge_score_v2(
            candles=details,
            microstructure=replayed_micro,
            derivatives=replayed_derivatives,
            cross_exchange_confirmed=bool(candle_result["is_breakdown_confirmed"]),
            ticker=ticker,
            reference_price=float(payload.get("reference_price") or ticker.get("last") or 0.0),
            strategy_stages=stages,
        )
        quality_gates = {
            "live_orderbook": bool(metrics.get("orderbook")),
            **score_result["quality_gates"],
        }
        watch_score = validator._watch_score(
            candles=details,
            microstructure=replayed_micro,
            derivatives=replayed_derivatives,
            cross_exchange_confirmed=candle_result.get("is_breakdown_confirmed"),
            ticker=ticker,
        )
        observation_status = validator._observational_status(stages)
        strategy_contract = (payload.get("decision_contract") or {}).get("strategy") or {}
        experimental_trigger = bool(
            not score_result["is_valid"]
            and validator._experimental_pretrigger_eligible(
                enabled=bool(strategy_contract.get("experimental_pretrigger_enabled")),
                threshold=float(strategy_contract.get("experimental_pretrigger_threshold", 45.0)),
                observation_score=watch_score.get("score"),
                observation_status=observation_status,
                strategy_stages=stages,
                quality_gates=quality_gates,
                microstructure=replayed_micro,
                derivatives=replayed_derivatives,
            )
        )
        if experimental_trigger:
            replayed_status = "TRIGGERED"
            replay_score_version = watch_score.get("score_version")
            replay_score = watch_score.get("score")
            replay_components = watch_score.get("components") or {}
            replay_reason = "experimental pre-trigger threshold passed"
            replay_valid = True
        elif score_result["is_valid"]:
            replayed_status = validator._suggested_status(
                score_result["score"],
                stages,
                bool(replayed_micro.get("approved")),
                bool(candle_result["is_breakdown_confirmed"]),
            )
            replay_score_version = score_result["score_version"]
            replay_score = score_result["score"]
            replay_components = score_result["score_components"]
            replay_reason = score_result["reason"]
            replay_valid = True
        else:
            replayed_status = "REJECTED"
            replay_score_version = score_result["score_version"]
            replay_score = None
            replay_components = {}
            replay_reason = score_result["reason"]
            replay_valid = False
        replayed_core = {
            "candle_analysis": candle_result,
            "microstructure": replayed_micro,
            "derivatives": replayed_derivatives,
            "strategy_stages": stages,
            "score_version": replay_score_version,
            "score": replay_score,
            "score_components": replay_components,
            "quality_gates": quality_gates,
            "analysis_reason": replay_reason,
            "is_valid": replay_valid,
            "suggested_status": replayed_status,
        }
        if replayed_derivatives.get("available") is not True:
            derivative_reason = str(
                replayed_derivatives.get("reason")
                or "incomplete fresh derivatives packet"
            )
            replayed_core.update(
                {
                    "score": None,
                    "score_components": {},
                    "quality_gates": {"complete_fresh_derivatives_packet": False},
                    "analysis_reason": derivative_reason,
                    "is_valid": False,
                    "suggested_status": "REJECTED",
                }
            )
        if position_attempted:
            history = position_source.get("raw_ohlcv")
            evaluated_at_ms = position_source.get("evaluated_at_ms")
            if not isinstance(history, list) or not history or not isinstance(evaluated_at_ms, int):
                return self._packet(
                    NOT_REPLAYABLE,
                    {"position_setup": "triggered-path source packet incomplete"},
                    decision_path,
                )
            try:
                recent_high = max(
                    float(row[2])
                    for row in history[-24:]
                    if len(row) >= 6
                )
            except (TypeError, ValueError, IndexError):
                recent_high = None
            mark_price, _ = MultiExchangeValidator._position_reference_price(
                ticker,
                replayed_micro,
            )
            position_setup = PositionCalculator().calculate_short_position(
                replayed_micro.get("best_bid"),
                recent_high=recent_high,
                market_info=market,
                mark_price=mark_price,
                entry_slippage_pct=replayed_micro.get("entry_slippage_pct"),
                exit_slippage_pct=replayed_micro.get("exit_slippage_pct"),
            )
            replayed_core["position_setup"] = position_setup
            if str(position_setup.get("status", "")).startswith("REJECTED"):
                replayed_core["suggested_status"] = "WATCH"
        expected_core = {
            "candle_analysis": metrics.get("candle_analysis") or {},
            "microstructure": expected_micro,
            "derivatives": expected_derivatives,
            "strategy_stages": metrics.get("strategy_stages") or {},
            "score_version": metrics.get("score_version"),
            "score": metrics.get("score"),
            "score_components": metrics.get("score_components") or {},
            "quality_gates": metrics.get("quality_gates") or {},
            "analysis_reason": metrics.get("analysis_reason"),
            "is_valid": bool((payload.get("result") or {}).get("is_valid")),
            "suggested_status": (payload.get("result") or {}).get("suggested_status"),
        }
        if position_attempted:
            expected_core["position_setup"] = metrics.get("position_setup") or {}
        differences = self._diff(expected_core, replayed_core)
        return self._packet(
            EQUIVALENT if not differences else MISMATCH,
            differences,
            decision_path,
        )

    def _packet(self, status: str, differences: dict, decision_path: str = "UNKNOWN") -> dict:
        return {
            "version": self.VERSION,
            "status": status,
            "strategy_equivalent": status == EQUIVALENT,
            "differences": differences,
            "decision_path": str(decision_path),
            "observational_only": True,
            "hard_gating_allowed": False,
        }


class FeatureReplayStore:
    def __init__(
        self,
        db_path: str = "/app/data/waterfall_registry.db",
        *,
        verify_schema: bool = True,
    ):
        self.db_path = db_path
        if verify_schema:
            require_managed_schema(
                self.db_path,
                required_tables=frozenset(
                    {
                        "production_feature_replay_results_v2",
                        "production_evidence_snapshots",
                    }
                ),
            )

    def _connect(self):
        return connect_managed_sqlite(self.db_path, timeout=20.0)

    @staticmethod
    def _pending_select_sql(*, with_frontier: bool = False) -> str:
        frontier_clause = "AND s.id >= ?" if with_frontier else ""
        return f"""
            WITH candidate_ids AS (
                SELECT s.id
                FROM production_evidence_snapshots AS s
                WHERE s.schema_version = 'production_decision_evidence_v9'
                  {frontier_clause}
                  AND NOT EXISTS (
                    SELECT 1 FROM production_feature_replay_results_v2 AS r
                    WHERE r.snapshot_id = s.id AND r.replay_version = ?
                  )
                UNION ALL
                SELECT s.id
                FROM production_evidence_snapshots AS s
                WHERE s.schema_version = 'production_decision_evidence_v8'
                  {frontier_clause}
                  AND s.production_evidence_complete_v5 = 1
                  AND s.decision_packet_complete = 1
                  AND s.code_sha256_v5 = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM production_feature_replay_results_v2 AS r
                    WHERE r.snapshot_id = s.id AND r.replay_version = ?
                  )
            ),
            selected_ids AS (
                SELECT id FROM candidate_ids ORDER BY id LIMIT ?
            )
            SELECT s.id, s.symbol, s.evidence_zlib
            FROM production_evidence_snapshots AS s
            INNER JOIN selected_ids AS selected ON selected.id = s.id
            ORDER BY s.id
        """

    def pending(
        self,
        limit: int = 3,
        *,
        start_snapshot_id: int | None = None,
    ) -> list[dict]:
        frontier = (
            max(1, int(start_snapshot_id))
            if start_snapshot_id is not None
            else None
        )
        if frontier is None:
            parameters = (
                FeatureReplayEngine.VERSION,
                source_tree_sha256()[0],
                FeatureReplayEngine.VERSION,
                max(1, int(limit)),
            )
        else:
            parameters = (
                frontier,
                FeatureReplayEngine.VERSION,
                frontier,
                source_tree_sha256()[0],
                FeatureReplayEngine.VERSION,
                max(1, int(limit)),
            )
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                self._pending_select_sql(with_frontier=frontier is not None),
                parameters,
            ).fetchall()
        return [
            {
                "id": row["id"],
                "symbol": row["symbol"],
                "payload": json.loads(zlib.decompress(row["evidence_zlib"])),
            }
            for row in rows
        ]

    def next_snapshot_id(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM production_evidence_snapshots"
            ).fetchone()
        return max(1, int(row[0]))

    def append(self, snapshot: dict, result: dict) -> bool:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO production_feature_replay_results_v2 (
                        snapshot_id, symbol, decision_path, status, strategy_equivalent,
                        differences_json, replay_version, replayed_at,
                        observational_only, hard_gating_allowed, trade_eligible
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, NULL)
                    """,
                    (
                        int(snapshot["id"]), str(snapshot["symbol"]),
                        str(result.get("decision_path") or "UNKNOWN"), str(result["status"]),
                        int(result["strategy_equivalent"] is True),
                        json.dumps(result.get("differences") or {}, allow_nan=False, sort_keys=True, separators=(",", ":")),
                        str(result["version"]), time.time(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def build_report(self) -> dict:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT status, COUNT(*) count
                FROM production_feature_replay_results_v2
                WHERE replay_version = ? GROUP BY status
                """,
                (FeatureReplayEngine.VERSION,),
            ).fetchall()
            latest = conn.execute(
                "SELECT MAX(replayed_at) FROM production_feature_replay_results_v2 WHERE replay_version = ?",
                (FeatureReplayEngine.VERSION,),
            ).fetchone()[0]
            triggered_equivalent = int(conn.execute(
                """
                SELECT COUNT(*) FROM production_feature_replay_results_v2
                WHERE replay_version = ? AND decision_path = 'TRIGGERED'
                  AND status = 'EQUIVALENT'
                """,
                (FeatureReplayEngine.VERSION,),
            ).fetchone()[0])
        counts = {row["status"]: int(row["count"]) for row in rows}
        total = sum(counts.values())
        equivalent = counts.get(EQUIVALENT, 0)
        mismatch = counts.get(MISMATCH, 0)
        not_replayable = counts.get(NOT_REPLAYABLE, 0) + counts.get(ERROR, 0)
        return {
            "version": FeatureReplayEngine.VERSION,
            "operational": True,
            "observational_only": True,
            "hard_gating_allowed": False,
            "promotion_allowed": False,
            "report_scope": "current_replay_generation",
            "replayed_count": total,
            "equivalent_count": equivalent,
            "mismatch_count": mismatch,
            "not_replayable_count": not_replayable,
            "triggered_equivalent_count": triggered_equivalent,
            "equivalence_rate": round(equivalent / total, 6) if total else None,
            "latest_replayed_at": latest,
            "strategy_equivalent": bool(
                total >= 100
                and mismatch == 0
                and not_replayable == 0
                and triggered_equivalent >= 1
            ),
            "requirements": {
                "minimum_replays": 100,
                "zero_mismatches": True,
                "triggered_path_replay_required": True,
            },
        }


class FeatureReplayWorker:
    def __init__(self, store: FeatureReplayStore, *, batch_size: int = 3):
        self.store = store
        self.engine = FeatureReplayEngine()
        self.batch_size = max(1, int(batch_size))
        self._running = True
        self._next_snapshot_id: int | None = None

    def stop(self) -> None:
        self._running = False

    async def run_once(self) -> int:
        initial_next_snapshot_id = None
        if self._next_snapshot_id is None:
            initial_next_snapshot_id = await asyncio.to_thread(
                self.store.next_snapshot_id
            )
        snapshots = await asyncio.to_thread(
            self.store.pending,
            self.batch_size,
            start_snapshot_id=self._next_snapshot_id,
        )
        if not snapshots:
            if self._next_snapshot_id is None:
                self._next_snapshot_id = initial_next_snapshot_id
            return 0

        completed = 0
        for snapshot in snapshots:
            try:
                result = await self.engine.replay(snapshot["payload"])
            except Exception as exc:
                logger.exception("Feature replay failed for snapshot %s", snapshot["id"])
                result = self.engine._packet(ERROR, {"error": str(exc)})
            appended = await asyncio.to_thread(
                self.store.append,
                snapshot,
                result,
            )
            completed += int(appended)
            if not appended:
                self._next_snapshot_id = int(snapshot["id"])
                break
            self._next_snapshot_id = int(snapshot["id"]) + 1
        return completed

    async def run_forever(self, interval_seconds: float = 60.0) -> None:
        while self._running:
            await self.run_once()
            await asyncio.sleep(max(10.0, float(interval_seconds)))
