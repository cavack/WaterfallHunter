from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from waterfallhunter.core.execution_planning import RiskPolicy
from waterfallhunter.routes_backtest_lab import (
    MAX_REPLAY_EVENTS,
    BacktestLabRequest,
    backtest_attestation_sha256,
    build_backtest_lab_router,
)


MANIFEST_HASH = "e" * 64
ARTIFACT_KEY = "test-only-backtest-artifact-key-32-bytes"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(build_backtest_lab_router(artifact_hmac_key=ARTIFACT_KEY))
    return TestClient(app)


def _signed(payload: dict) -> dict:
    provisional = {
        "artifact_key_id": "wfh-backtest-hmac-v1",
        "artifact_hmac_sha256": "0" * 64,
        **payload,
    }
    request = BacktestLabRequest.model_validate(provisional)
    return {
        **provisional,
        "artifact_hmac_sha256": backtest_attestation_sha256(
            request,
            artifact_hmac_key=ARTIFACT_KEY,
        ),
    }


def test_contract_is_fixed_read_only_and_rejects_unknown_queries() -> None:
    client = _client()

    response = client.get("/api/backtest-lab/contract")

    assert response.status_code == 200
    payload = response.json()
    assert payload["execution_mode"] == "SIGNAL_ONLY"
    assert payload["risk_policy"]["policy_hash"] == RiskPolicy.v1().policy_hash
    assert payload["caller_risk_overrides_allowed"] is False
    assert payload["database_writes"] is False
    assert payload["strategy_equivalent"] is False
    assert payload["promotion_allowed"] is False
    assert client.get("/api/backtest-lab/contract?unsafe=1").status_code == 422


def test_empty_replay_is_deterministic_and_explicitly_non_equivalent() -> None:
    client = _client()
    request = _signed({
        "dataset_manifest_hash": MANIFEST_HASH,
        "initial_equity": 1_000,
        "events": [],
        "signal_rows": [
            {
                "signal_id": "signal-1",
                "signal_triggered_at": 100,
                "outcome": "UNAVAILABLE",
            }
        ],
    })

    first = client.post("/api/backtest-lab/replay", json=request)
    second = client.post("/api/backtest-lab/replay", json=request)

    assert first.status_code == 200
    assert first.json() == second.json()
    payload = first.json()
    assert payload["contract_version"] == "backtest_lab_response_v2"
    assert payload["execution_mode"] == "SIGNAL_ONLY"
    assert payload["strategy_equivalent"] is False
    assert payload["claims_allowed"] is False
    assert payload["promotion_allowed"] is False
    assert payload["portfolio_report"]["final_marked_equity"] == 1_000
    assert payload["portfolio_report"]["cost_attribution"] == {
        "entry_cost": 0.0,
        "exit_cost": 0.0,
        "modeled_trading_cost": 0.0,
        "net_funding": 0.0,
    }
    assert payload["signal_level_report"]["portfolio_realizability_applied"] is False


def test_replay_rejects_invalid_hash_policy_override_and_duplicate_events() -> None:
    client = _client()
    base = {
        "dataset_manifest_hash": MANIFEST_HASH,
        "initial_equity": 1_000,
        "events": [],
    }

    invalid_hash = client.post(
        "/api/backtest-lab/replay",
        json=_signed(base) | {"dataset_manifest_hash": "invalid"},
    )
    override = client.post(
        "/api/backtest-lab/replay",
        json=_signed(base) | {"risk_policy": {"max_open_positions": 100}},
    )
    event = {
        "event_id": "duplicate",
        "occurred_at": 100,
        "event_type": "MARK",
        "position_id": "position-1",
        "price": 100,
        "exit_cost": 0,
    }
    duplicate = client.post(
        "/api/backtest-lab/replay",
        json=_signed({**base, "events": [event, event]}),
    )

    assert invalid_hash.status_code == 422
    assert override.status_code == 422
    assert duplicate.status_code == 422
    assert duplicate.json()["detail"]["reason"] == "portfolio event IDs must be unique"


def test_replay_event_count_is_bounded() -> None:
    client = _client()
    events = [
        {
            "event_id": f"mark-{index}",
            "occurred_at": index,
            "event_type": "MARK",
            "position_id": "missing-position",
            "price": 100,
            "exit_cost": 0,
        }
        for index in range(MAX_REPLAY_EVENTS + 1)
    ]

    response = client.post(
        "/api/backtest-lab/replay",
        json={
            "artifact_key_id": "wfh-backtest-hmac-v1",
            "artifact_hmac_sha256": "0" * 64,
            "dataset_manifest_hash": MANIFEST_HASH,
            "initial_equity": 1_000,
            "events": events,
        },
    )

    assert response.status_code == 422


def test_replay_requires_server_attestation_and_strict_signal_time() -> None:
    client = _client()
    base = {
        "dataset_manifest_hash": MANIFEST_HASH,
        "initial_equity": 1_000,
        "events": [],
        "signal_rows": [],
    }
    signed = _signed(base)

    tampered = client.post(
        "/api/backtest-lab/replay",
        json={**signed, "initial_equity": 2_000},
    )
    coerced_time = client.post(
        "/api/backtest-lab/replay",
        json={
            **signed,
            "signal_rows": [{"signal_id": "signal", "signal_triggered_at": "100"}],
        },
    )
    unavailable_app = FastAPI()
    unavailable_app.include_router(build_backtest_lab_router())
    unavailable = TestClient(unavailable_app).post(
        "/api/backtest-lab/replay",
        json=signed,
    )

    assert tampered.status_code == 422
    assert tampered.json()["detail"] == "backtest artifact attestation invalid"
    assert coerced_time.status_code == 422
    assert unavailable.status_code == 503


def test_production_bundle_is_server_signed_and_replayable(tmp_path) -> None:
    import json
    import sqlite3

    db_path = tmp_path / "production-signals.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE lbank_signal_ledger (
                id INTEGER PRIMARY KEY, symbol TEXT, triggered_at INTEGER,
                score REAL, trigger_metrics_json TEXT
            );
            CREATE TABLE signal_metadata (
                signal_id INTEGER PRIMARY KEY, signal_class TEXT,
                strategy_profile TEXT, score_version TEXT
            );
            CREATE TABLE lbank_signal_outcomes (
                id INTEGER PRIMARY KEY, signal_id INTEGER,
                outcome_status TEXT, resolved_at INTEGER
            );
            """
        )
        conn.execute(
            "INSERT INTO lbank_signal_ledger VALUES (1, ?, ?, ?, ?)",
            (
                "BUNDLE/USDT:USDT",
                1_700_000_000,
                92.5,
                json.dumps({"applied_leverage": 12}),
            ),
        )
        conn.execute(
            "INSERT INTO signal_metadata VALUES (1, 'STRICT', 'strict_v3', 'score_v2')"
        )
        conn.execute(
            "INSERT INTO lbank_signal_outcomes VALUES (1, 1, 'TP1_HIT', 1700003600)"
        )

    app = FastAPI()
    app.include_router(
        build_backtest_lab_router(
            artifact_hmac_key=ARTIFACT_KEY,
            db_path=str(db_path),
        )
    )
    client = TestClient(app)
    response = client.get(
        "/api/backtest-lab/production-bundle?limit=10&initial_equity=100"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["contract_version"] == "backtest_production_bundle_v1"
    assert payload["execution_mode"] == "SIGNAL_ONLY"
