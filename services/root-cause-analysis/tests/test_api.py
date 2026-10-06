import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1]),
)

from app.main import app


client = TestClient(app)


def test_health():
    response = client.get("/health")

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "ok"
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
    assert data["root_cause"] == "faulty_deployment"
    assert data["affected_component"] == "payment-service:v2"
    assert data["confidence"] == 0.8

    assert (
        "payment-service:v2 deployed 42 seconds before anomaly"
        in data["evidence"]
    )

    assert (
        "P95 latency increased after deployment"
        in data["evidence"]
    )

    assert (
        "HTTP 5xx increased"
        in data["evidence"]
    )