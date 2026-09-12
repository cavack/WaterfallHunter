from fastapi import FastAPI
from fastapi.testclient import TestClient

from schema_test_support import migrate_test_database
from waterfallhunter.core.historical_outcome_store import HistoricalOutcomeStore
from waterfallhunter.routes_historical_outcomes import build_historical_outcome_router


def test_historical_outcome_route_has_locked_operational_contract(tmp_path):
    db_path = migrate_test_database(tmp_path / "outcomes.db")
    store = HistoricalOutcomeStore(str(db_path))
    app = FastAPI()
    app.include_router(build_historical_outcome_router(store))
    client = TestClient(app)

    response = client.get("/api/historical-outcomes")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "operational_historical_outcomes_v1",
        "available": False,
        "operational": True,
        "observational_only": True,
        "hard_gating_allowed": False,
        "threshold_calibration_allowed": False,
        "evidence_source": "historical_backfill",
        "dataset": None,
        "summary": {
            "event_count": 0,
            "settled_count": 0,
            "wins": 0,
            "win_rate": None,
            "net_expectancy_r": None,
        },
        "by_symbol": {},
    }

    rejected = client.get("/api/historical-outcomes?promote=true")
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["allowed_parameters"] == []
