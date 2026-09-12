from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from schema_test_support import migrate_test_database
from waterfallhunter.core.lifecycle_v2_shadow import (
    LifecycleV2Evidence,
    LifecycleV2State,
    compare_v1_v2_shadow,
    evaluate_lifecycle_v2_shadow,
)
from waterfallhunter.core.lifecycle_v2_shadow_store import LifecycleV2ShadowStore
from waterfallhunter.routes_lifecycle_v2_shadow import (
    build_lifecycle_v2_shadow_router,
)


def _client(tmp_path) -> TestClient:
    db_path = migrate_test_database(tmp_path / "lifecycle-api.db")
    store = LifecycleV2ShadowStore(db_path)
    evidence = LifecycleV2Evidence(
        eligible_data=True,
        fuel_rich=True,
        structure_count=2,
        flow_family_pass=True,
        relative_family_pass=False,
        anti_chase_pass=True,
        strict_setup_ready=True,
        lower_tf_trigger_closed=False,
        distance_to_trigger_atr=0.4,
        lbank_constraints_fresh=True,
        orderbook_fresh=True,
        levels_constructible=True,
        estimated_round_trip_cost_r=0.1,
        executable_depth_multiple=12,
        preliminary_portfolio_capacity=True,
        confirmation_count=2,
        confirmation_family_count=2,
        extension_atr=0.2,
        oldest_required_observed_at=990,
        required_observed_at=(1_000, 999, 995, 990),
        decision_at=1_000,
        evidence_refs=("runtime:test",),
    )
    transition = evaluate_lifecycle_v2_shadow(
        episode_id="episode-api",
        current_state=LifecycleV2State.WATCH,
        evidence=evidence,
    )
    comparison = compare_v1_v2_shadow(
        episode_id="episode-api",
        v1_state="WATCH",
        v2_state=LifecycleV2State.WATCH,
        evidence=evidence,
    )
    store.append_comparison(
        symbol="TEST/USDT:USDT",
        v1_state="WATCH",
        transition=transition,
        comparison=comparison,
        created_at=1_000,
    )
    app = FastAPI()
    app.include_router(build_lifecycle_v2_shadow_router(store))
    return TestClient(app)


def test_shadow_report_and_contract_are_read_only_and_explicit(tmp_path) -> None:
    client = _client(tmp_path)

    report = client.get("/api/lifecycle-v2-shadow?limit=1")
    contract = client.get("/api/lifecycle-v2-contract")

    assert report.status_code == 200
    assert report.json()["event_count"] == 1
    assert report.json()["returned_event_count"] == 1
    assert report.json()["promotion_allowed"] is False
    assert contract.status_code == 200
    assert contract.json()["policy"]["threshold_status"] == "SHADOW_HYPOTHESES"
    assert contract.json()["feature_registry"]["outcome_fields_allowed_as_features"] is False


def test_shadow_api_rejects_unbounded_or_unknown_queries(tmp_path) -> None:
    client = _client(tmp_path)

    assert client.get("/api/lifecycle-v2-shadow?limit=5001").status_code == 422
    assert client.get("/api/lifecycle-v2-shadow?unknown=1").status_code == 422
    assert client.get("/api/lifecycle-v2-contract?unknown=1").status_code == 422
