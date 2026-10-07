from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_required_fields():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"service", "status", "version", "environment"}
    assert body["service"] == "finops-engine"
    assert body["status"] in {"ok", "degraded", "unavailable"}