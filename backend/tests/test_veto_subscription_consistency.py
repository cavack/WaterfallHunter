from __future__ import annotations

import asyncio
import time

from waterfallhunter import main
from waterfallhunter.core.signal_metadata import STRICT_STRATEGY_PROFILE


def _prepare_veto_evaluation(monkeypatch, *, symbol: str, state_persisted: bool):
    monkeypatch.setattr(main.scanner, "active_candidates", {symbol: {}})
    monkeypatch.setattr(
        main.execution_decision_logger, "observe_evaluation", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        main.scanner,
        "get_live_reference",
        lambda requested_symbol: (0.01, int(time.time()) - 1),
    )

    async def valid_trigger(*args, **kwargs):
        return {
            "is_valid": True,
            "score": 91.0,
            "suggested_status": "TRIGGERED",
            "metrics": {
                "exchange": "binance",
                "mapped_symbol": symbol,
                "orderbook": {},
                "ticker": {},
                "strategy_profile": STRICT_STRATEGY_PROFILE,
                "score_version": "score_v2",
            },
        }

    monkeypatch.setattr(main.validator, "cross_check_symbol", valid_trigger)
    monkeypatch.setattr(
        main.ai_veto,
        "evaluate_deterministic",
        lambda *args, **kwargs: (
            True,
            {
                "deterministic_veto": True,
                "deterministic_reason": "unsafe market data",
            },
        ),
    )
    monkeypatch.setattr(
        main.db, "update_candidate_state", lambda *args: state_persisted
    )
    monkeypatch.setattr(
        main.production_evidence_recorder, "record", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(main.entry_decision_store, "latest_for_symbol", lambda _symbol: None)
    monkeypatch.setattr(
        main.entry_decision_store,
        "append_if_changed",
        lambda *_args, **_kwargs: 9001,
    )
    direct_unsubscribed: list[tuple[str, str]] = []
    shared_unsubscribed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main.validator.ws_manager,
        "unsubscribe",
        lambda *args: direct_unsubscribed.append(args),
    )
    monkeypatch.setattr(
        main.validator.ws_manager,
        "unsubscribe_shared_evidence",
        lambda *args: shared_unsubscribed.append(args),
    )
    return direct_unsubscribed, shared_unsubscribed


def _evaluate(symbol: str) -> None:
    asyncio.run(
        main.evaluate_candidate(
            symbol,
            {"status": "ARMED", "lifecycle_id": 1, "scan_eligible": True},
        )
    )


def test_failed_veto_state_persistence_keeps_websocket_subscription(monkeypatch) -> None:
    symbol = "VETO/USDT:USDT"
    direct, shared = _prepare_veto_evaluation(
        monkeypatch, symbol=symbol, state_persisted=False
    )

    _evaluate(symbol)

    assert direct == []
    assert shared == []


def test_successful_veto_state_persistence_retires_direct_and_shared_websocket(monkeypatch) -> None:
    symbol = "VETOCLEAN/USDT:USDT"
    direct, shared = _prepare_veto_evaluation(
        monkeypatch, symbol=symbol, state_persisted=True
    )

    _evaluate(symbol)

    assert direct == [("binance", symbol)]
    assert shared == [("binance", symbol)]
