from fastapi import FastAPI
from fastapi.testclient import TestClient

from schema_test_support import migrate_test_database
from waterfallhunter.core.production_evidence import ProductionEvidenceRecorder
from waterfallhunter.routes_production_evidence import build_production_evidence_router


def test_production_evidence_route_is_read_only_and_parameter_locked(tmp_path):
    db_path = migrate_test_database(tmp_path / "evidence.db")
    recorder = ProductionEvidenceRecorder(str(db_path))
    app = FastAPI()
    app.include_router(build_production_evidence_router(recorder))
    client = TestClient(app)

    response = client.get("/api/production-evidence")

    assert response.status_code == 200
    assert response.json()["operational"] is True
    assert response.json()["observational_only"] is True
    assert response.json()["hard_gating_allowed"] is False
    assert response.json()["snapshot_count_24h"] == 0

    rejected = client.get("/api/production-evidence?gate=true")
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["allowed_parameters"] == []
