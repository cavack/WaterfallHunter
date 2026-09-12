from fastapi import FastAPI
from fastapi.testclient import TestClient

from schema_test_support import migrate_test_database
from waterfallhunter.core.entry_decision_store import EntryDecisionStore
from waterfallhunter.routes_recent_signals import build_recent_signals_router

SYMBOL = "RECENT/USDT:USDT"


def _decision(decision: str, now: int) -> dict:
    return {
        "contract_version": "entry_decision_v1",
        "policy_version": "entry_policy_v1",
        "evaluated_at": now,
        "decision": decision,
        "lifecycle_state": "ARMED",
        "entry_readiness": 84.0,
        "evidence_coverage_pct": 82.0,
        "hard_blocked": False,
        "block_reasons": [],
        "reason_codes": ["ENTRY_GATES_PASS"],
        "components": {},
        "trade_plan": {
            "entry_price": 0.0101,
            "stop_loss": 0.0105,
            "take_profit_1": 0.0097,
            "take_profit_2": 0.0093,
            "take_profit_3": 0.0090,
            "leverage": 11,
            "expires_at": 1_700_000_100,
        },
        "policy": {},
    }


def test_recent_signals_route_returns_canonical_decision_transitions(tmp_path):
    db_path = migrate_test_database(tmp_path / "recent.db")
    store = EntryDecisionStore(db_path)
    ready_id = store.append_if_changed(SYMBOL, _decision("ENTRY_READY", 1_700_000_010))
    assert ready_id is not None
    store.append_advisory(
        ready_id,
        {
            "observational_only": True,
            "decision_mutated": False,
            "ai_advice": "SHORT",
            "ai_confidence": 81,
            "ai_reasoning": "Evidence agrees.",
            "ai_provider": "gemini",
            "ai_model": "gemini-test",
            "ai_status": "AVAILABLE",
        },
        advisory_at=1_700_000_011,
    )
    store.append_if_changed(SYMBOL, _decision("ACTIVE", 1_700_000_020))

    app = FastAPI()
    app.include_router(build_recent_signals_router(str(db_path)))
    response = TestClient(app).get("/api/recent-signals?limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "canonical_decision_history_v1"
    assert body["operational"] is True
    assert body["observational_only"] is True
    assert body["hard_gating_allowed"] is False
    assert body["count"] == 2
    row = body["decisions"][0]
    assert row["event_id"] == 2
    assert row["symbol"] == SYMBOL
    assert row["decision"] == "ACTIVE"
    assert row["previous_decision"] == "ENTRY_READY"
    ready = body["decisions"][1]
    assert ready["trade_plan"]["entry_price"] == 0.0101
    assert ready["trade_plan"]["expires_at"] == 1_700_000_100
    assert ready["ai_advisory"]["ai_advice"] == "SHORT"


def test_recent_signals_route_rejects_unsupported_or_out_of_range_queries(tmp_path):
    db_path = migrate_test_database(tmp_path / "recent-empty.db")
    app = FastAPI()
    app.include_router(build_recent_signals_router(str(db_path)))
    client = TestClient(app)
    assert client.get("/api/recent-signals?promote=true").status_code == 422
    assert client.get("/api/recent-signals?limit=101").status_code == 422


def test_recent_signals_translates_database_failure_to_503(tmp_path):
    app = FastAPI()
    app.include_router(
        build_recent_signals_router(str(tmp_path / "missing" / "recent.db"))
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/recent-signals")

    assert response.status_code == 503
    assert "503" in app.openapi()["paths"]["/api/recent-signals"]["get"]["responses"]
