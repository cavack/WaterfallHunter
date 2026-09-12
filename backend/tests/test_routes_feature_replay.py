from fastapi import FastAPI
from fastapi.testclient import TestClient

from schema_test_support import migrate_test_database
from waterfallhunter.core.feature_replay import FeatureReplayStore
from waterfallhunter.routes_feature_replay import build_feature_replay_router


def test_feature_replay_route_is_parameter_locked(tmp_path):
    app = FastAPI()
    db_path = migrate_test_database(tmp_path / "replay.db")
    app.include_router(build_feature_replay_router(FeatureReplayStore(str(db_path))))
    client = TestClient(app)
    response = client.get("/api/feature-replay")
    assert response.status_code == 200
    assert response.json()["observational_only"] is True
    assert response.json()["hard_gating_allowed"] is False
    assert response.json()["promotion_allowed"] is False
    assert client.get("/api/feature-replay?promote=true").status_code == 422
