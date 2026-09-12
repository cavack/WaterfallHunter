import asyncio
import time
from unittest.mock import AsyncMock

from waterfallhunter import main
from waterfallhunter.core.contracts import SignalClass
from waterfallhunter.core.signal_metadata import STRICT_STRATEGY_PROFILE, SignalMetadataInput


def test_experimental_signal_profile_suppresses_telegram_delivery():
    assert main._signal_alert_allowed({}) is True
    assert main._signal_alert_allowed(
        {"strategy_profile": "experimental_pretrigger_v1"}
    ) is False


def test_stale_trigger_persistence_failure_suppresses_telegram(
    monkeypatch,
):
    symbol = "STALE/USDT:USDT"
    reference_observed_at = 1_699_999_990.75

    monkeypatch.setattr(
        main.scanner,
        "active_candidates",
        {symbol: {}},
    )
    observe_decision = []
    monkeypatch.setattr(
        main.execution_decision_logger,
        "observe_evaluation",
        lambda *args, **kwargs: (
            observe_decision.append(
                (args, kwargs)
            )
            or True
        ),
    )
    monkeypatch.setattr(
        main.scanner,
        "get_live_reference",
        lambda requested_symbol: (
            0.01,
            reference_observed_at,
        ),
    )

    async def valid_trigger(*args, **kwargs):
        return {
            "is_valid": True,
            "score": 90.0,
            "suggested_status": "TRIGGERED",
            "metrics": {
                "exchange": "test-exchange",
                "mapped_symbol": symbol,
                "orderbook": {},
                "ticker": {},
                "strategy_profile": STRICT_STRATEGY_PROFILE,
                "score_version": "score_v2",
            },
        }

    monkeypatch.setattr(
        main.validator,
        "cross_check_symbol",
        valid_trigger,
    )
    monkeypatch.setattr(
        main.ai_veto,
        "evaluate_deterministic",
        lambda *args, **kwargs: (
            False,
            {
                "deterministic_veto": False,
                "deterministic_reason": "test pass",
                "ai_advice": "PENDING",
                "ai_confidence": 0,
                "ai_reasoning": "observational only",
                "ai_provider": "none",
                "ai_observational_only": True,
                "ai_decision_critical": False,
            },
        ),
    )
    monkeypatch.setattr(
        main,
        "build_signal_leverage_advisory",
        lambda metrics, execution_suitability=None, **kwargs: {
            "policy_version": "adaptive_signal_leverage_v1",
            "minimum": 4, "maximum": 18, "symbol_agnostic": True,
            "signal_only": True, "advisory_only": True,
            "status": "AVAILABLE", "leverage": 4, "reason": None,
        },
    )
    monkeypatch.setattr(
        main.execution_suitability_enricher,
        "for_symbol",
        lambda requested_symbol: {
            "symbol": requested_symbol,
            "status": "UNKNOWN",
            "observational_only": True,
            "trade_eligible": None,
        },
    )
    # This test isolates the legacy signal-ledger persistence failure. The
    # synthetic symbol is intentionally not in the catalogue, so bypass the
    # independent canonical lifecycle CAS covered by entry-decision-store tests.
    monkeypatch.setattr(
        main.entry_decision_store,
        "append_if_changed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        main.entry_decision_store,
        "latest_for_symbol",
        lambda _symbol: None,
    )
    persisted = []

    def reject_persistence(*args, **kwargs):
        persisted.append((args, kwargs))

    monkeypatch.setattr(
        main.signal_ledger,
        "persist_trigger",
        reject_persistence,
    )

    evidence_packets = []
    monkeypatch.setattr(
        main.production_evidence_recorder,
        "record",
        lambda *args, **kwargs: evidence_packets.append(kwargs["result"]) or True,
    )

    send_alert = AsyncMock()
    monkeypatch.setattr(
        main.notifier,
        "send_signal_alert",
        send_alert,
    )

    before = int(time.time())
    asyncio.run(
        main.evaluate_candidate(
            symbol,
            {"status": "ARMED"},
        )
    )
    after = int(time.time())

    send_alert.assert_not_awaited()
    assert len(observe_decision) == 1
    assert len(persisted) == 1
    metadata = persisted[0][1].get("metadata")
    assert isinstance(metadata, SignalMetadataInput)
    assert metadata.signal_class is SignalClass.STRICT
    assert metadata.strategy_profile == STRICT_STRATEGY_PROFILE
    assert metadata.score_version == "score_v2"
    assert before <= metadata.analysis_observed_at <= after
    assert metadata.reference_observed_at == int(reference_observed_at)
    assert evidence_packets[-1]["metrics"]["production_decision"]["path"] == "PERSISTENCE_REJECTED"


def test_missing_reference_persists_invalidation_after_entry_ready(monkeypatch):
    symbol = "NREF/USDT:USDT"
    monkeypatch.setattr(main.scanner, "active_candidates", {symbol: {}})
    monkeypatch.setattr(main.scanner, "get_live_reference", lambda _: (None, None))
    monkeypatch.setattr(main.execution_decision_logger, "observe_evaluation", lambda *a, **k: True)
    monkeypatch.setattr(main.production_evidence_recorder, "record", lambda *a, **k: True)

    async def no_reference(_symbol):
        return {}

    monkeypatch.setattr(main.validator, "resolve_live_reference", no_reference)
    monkeypatch.setattr(main.db, "update_candidate_state", lambda *a, **k: True)
    stored_metrics = []
    monkeypatch.setattr(main, "_store_live_metrics", lambda _symbol, metrics: stored_metrics.append(metrics))
    monkeypatch.setattr(
        main.entry_decision_store,
        "latest_for_symbol",
        lambda _symbol: {
            "contract_version": "entry_decision_v1",
            "policy_version": "entry_policy_v1",
            "decision": "ENTRY_READY",
            "lifecycle_state": "PRE-TRIGGER",
            "entry_readiness": 85.0,
            "evidence_coverage_pct": 90.0,
            "evaluated_at": 1_788_000_000,
        },
    )
    persisted = []
    monkeypatch.setattr(
        main.entry_decision_store,
        "append_if_changed",
        lambda _symbol, packet, **_kwargs: persisted.append(packet) or 777,
    )

    asyncio.run(main.evaluate_candidate(symbol, {"status": "ARMED"}))

    assert len(persisted) == 1
    assert persisted[0]["decision"] == "INVALIDATED"
    assert "STALE_REFERENCE" in persisted[0]["block_reasons"]
    assert "ENTRY_CONDITIONS_LOST" in persisted[0]["block_reasons"]
    assert stored_metrics[-1]["entry_decision"]["decision"] == "INVALIDATED"
    assert stored_metrics[-1]["entry_decision"]["event_id"] == 777
