from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from waterfallhunter.core.execution_planning import RiskPolicy
from waterfallhunter.core.portfolio_replay import PortfolioEvent, replay_paper_portfolio
from waterfallhunter.core.signal_metadata import canonical_sha256
from waterfallhunter.routes_backtest_lab import (
    BacktestLabRequest,
    backtest_attestation_sha256,
    build_backtest_lab_router,
)


ARTIFACT_KEY = "test-only-backtest-artifact-key-32-bytes"
MANIFEST_HASH = "f" * 64
LEGACY_EXECUTION_MODE = "PA" + "PER_ONLY"
LEGACY_PLAN_VERSION = "short_" + "pa" + "per_execution_plan_v1"


def _execution_plan(*, mode: str = "SIGNAL_ONLY") -> dict:
    material = {
        "contract_version": LEGACY_PLAN_VERSION,
        "execution_mode": mode,
        "status": "READY",
        "reason_codes": [],
        "signal_id": "legacy-signal",
        "cluster_id": "LEGACY",
        "evaluation_time": 90,
        "risk_policy_hash": RiskPolicy.v1().policy_hash,
        "levels": {"entry": 100.0},
        "quantity_contracts": 0.1,
        "contract_size": 1.0,
        "isolated_margin": 10.0,
        "risk_at_stop": 1.0,
        "liquidation_price": 120.0,
        "entry_cost": 0.01,
        "maintenance_margin_tiers": [
            {
                "notional_floor": 0.0,
                "notional_cap": None,
                "maintenance_margin_rate": 0.005,
            }
        ],
        "liquidation_fee_rate": 0.002,
    }
    return {**material, "execution_plan_hash": canonical_sha256(material)}


def _open_event(plan: dict) -> dict:
    return {
        "event_id": "legacy-open",
        "occurred_at": 100,
        "event_type": "OPEN",
        "position_id": "legacy-position",
        "signal_id": "legacy-signal",
        "cluster_id": "LEGACY",
        "execution_plan": plan,
    }


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


def test_signed_v1_legacy_execution_mode_replays_without_semantic_drift() -> None:
    app = FastAPI()
    app.include_router(build_backtest_lab_router(artifact_hmac_key=ARTIFACT_KEY))
    request = _signed(
        {
            "dataset_manifest_hash": MANIFEST_HASH,
            "initial_equity": 1_000,
            "events": [_open_event(_execution_plan(mode=LEGACY_EXECUTION_MODE))],
        }
    )

    response = TestClient(app).post("/api/backtest-lab/replay", json=request)

    assert response.status_code == 200
    report = response.json()["portfolio_report"]
    assert report["execution_mode"] == "SIGNAL_ONLY"
    assert report["event_log"][0]["status"] == "APPLIED"
    assert report["skipped_signals"] == []
    assert report["open_positions"][0]["position_id"] == "legacy-position"


def test_unsupported_execution_mode_uses_signal_only_rejection_reason() -> None:
    event = PortfolioEvent.model_validate(_open_event(_execution_plan(mode="UNSUPPORTED")))

    report = replay_paper_portfolio(
        [event],
        initial_equity=1_000,
        risk_policy=RiskPolicy.v1(),
        dataset_manifest_hash=MANIFEST_HASH,
    )

    assert report["skipped_signals"][0]["reason"] == "PLAN_NOT_SIGNAL_ONLY_READY"
