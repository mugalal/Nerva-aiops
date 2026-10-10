from fastapi.testclient import TestClient
import pytest

from app.main import app
from app import persistence

client = TestClient(app)


def test_health_returns_required_fields():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"service", "status", "version", "environment"}
    assert body["service"] == "finops-engine"
    assert body["status"] in {"ok", "degraded", "unavailable"}


@pytest.mark.parametrize("configured,reachable,expected_status", [
    (True, False, 503), (True, True, 200), (False, False, 200),
])
def test_readiness_blocks_configured_unreachable_database(monkeypatch, configured, reachable, expected_status):
    monkeypatch.setattr(persistence, "db_configured", lambda: configured)
    monkeypatch.setattr(persistence, "db_reachable", lambda: reachable)
    response = client.get("/ready")
    assert response.status_code == expected_status
    assert response.json()["status"] == ("degraded" if expected_status == 503 else "ok")
