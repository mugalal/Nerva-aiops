import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1]),
)

import app.main as main_module
from app.main import app
from app.service import RCAService


client = TestClient(app)


@pytest.fixture(autouse=True)
def explicit_mock_service(monkeypatch):
    monkeypatch.setattr(main_module, "rca_service", RCAService(mode="mock"))


def test_health():
    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "degraded"
    assert data["provider_mode"] == "mock"
    assert data["environment"]
    assert data["service"] == "root-cause-analysis"


def test_rca_analyze():
    response = client.post(
        "/internal/rca/analyze",
        json={
            "incident_id": "INC-001",
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["incident_id"] == "INC-001"
    # The canonical telemetry fixture is healthy v1, while the event says v2.
    # Explicit mocks must not hide that contradiction by attributing a release.
    assert data["root_cause"] == "unknown"
    assert data["affected_component"] == "payment-service"
    assert data["confidence"] == 0


def test_mock_rejects_unknown_incident_id():
    response = client.post("/internal/rca/analyze", json={"incident_id": "INC-NOT-EXIST"})
    assert response.status_code == 404
    assert response.json()["detail"]["category"] == "not_found"
