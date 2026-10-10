import os
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.api.incidents import create_incident, find_incident, incidents, _committed_incidents, IncidentCreate
from app.contracts import AnomalyEvent
from app.orchestration.orchestrator import build_decision, execute_approved_action
from app.orchestration.archive import enqueue_archive, deliver_archive, _archive_breaker
from app.providers.errors import IntegrationError, as_http_error, sanitize_message
from app.providers.resilient import CircuitOpen
from app.state_machine.states import IncidentStatus


@pytest.fixture(autouse=True)
def clean_state():
    incidents.clear()
    _committed_incidents.clear()
    yield
    incidents.clear()
    _committed_incidents.clear()


def test_proposal_ttl_expiry_rejects_approval(monkeypatch):
    client = TestClient(app)
    incident_id = "INC-TTL-001"
    
    # Create incident
    create_incident(IncidentCreate(incident_id=incident_id, severity="high", affected_services=["payment-service"]))
    decision = build_decision(incident_id)
    assert decision is not None

    incident = find_incident(incident_id)
    assert "_proposal_created_at" in incident

    # Age the proposal past TTL (default 300s)
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=350)).isoformat()
    incident["_proposal_created_at"] = old_time

    # Attempt approve via API -> 409
    resp = client.post(f"/api/incidents/{incident_id}/approve")
    assert resp.status_code == 409
    assert "Proposal has expired" in resp.json()["detail"]


def test_proposal_ttl_expiry_in_execute_action():
    incident_id = "INC-TTL-002"
    create_incident(IncidentCreate(incident_id=incident_id, severity="high", affected_services=["payment-service"]))
    build_decision(incident_id)

    incident = find_incident(incident_id)
    incident["_proposal_created_at"] = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()

    with pytest.raises(IntegrationError) as exc_info:
        execute_approved_action(incident_id, "ROLLBACK", approved=True)
    assert exc_info.value.status_code == 409
    assert "Proposal has expired" in str(exc_info.value)


def test_proposal_ttl_expiry_redetects_fresh_decision():
    incident_id = "INC-TTL-003"
    create_incident(IncidentCreate(incident_id=incident_id, severity="high", affected_services=["payment-service"]))
    build_decision(incident_id)

    incident = find_incident(incident_id)
    incident["_proposal_created_at"] = (datetime.now(timezone.utc) - timedelta(seconds=350)).isoformat()
    original_created_at = incident["_proposal_created_at"]

    # Triggering build_decision again invalidates and re-creates with fresh timestamp
    new_decision = build_decision(incident_id)
    assert new_decision is not None
    assert incident["_proposal_created_at"] != original_created_at
    fresh_time = datetime.fromisoformat(incident["_proposal_created_at"])
    assert (datetime.now(timezone.utc) - fresh_time).total_seconds() < 10


def test_ingest_auth_enforced_when_token_configured(monkeypatch):
    client = TestClient(app)
    monkeypatch.setenv("NEXUS_INGEST_TOKEN", "secret-ingest-token-123")

    payload = {"incident_id": "INC-AUTH-001", "severity": "high", "affected_services": ["payment-service"], "anomaly_ids": []}

    # 1. No token -> 401
    resp = client.post("/api/incidents/", json=payload)
    assert resp.status_code == 401
    assert "Invalid or missing ingest authentication token" in resp.json()["detail"]

    # 2. Wrong token -> 401
    resp = client.post("/api/incidents/", json=payload, headers={"x-nexus-ingest-token": "wrong-token"})
    assert resp.status_code == 401

    # 3. Valid x-nexus-ingest-token header -> 200
    resp = client.post("/api/incidents/", json=payload, headers={"x-nexus-ingest-token": "secret-ingest-token-123"})
    assert resp.status_code == 200
    assert resp.json()["incident_id"] == "INC-AUTH-001"

    # 4. Valid Authorization: Bearer token -> 200 on another incident
    payload2 = {"incident_id": "INC-AUTH-002", "severity": "medium", "affected_services": ["payment-service"], "anomaly_ids": []}
    resp = client.post("/api/incidents/", json=payload2, headers={"Authorization": "Bearer secret-ingest-token-123"})
    assert resp.status_code == 200

    # 5. Ingest anomaly route auth check
    anomaly_data = {
        "anomaly_id": "ANOM-AUTH-001",
        "service": "payment-service",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "score": 0.95,
        "severity": "high",
        "model": "isolation_forest",
        "features": {"error_rate": 0.15},
    }
    # Missing token -> 401
    resp = client.post("/internal/anomalies?incident_id=INC-AUTH-001", json=anomaly_data)
    assert resp.status_code == 401

    # Valid token -> 200
    resp = client.post("/internal/anomalies?incident_id=INC-AUTH-001", json=anomaly_data,
                       headers={"Authorization": "Bearer secret-ingest-token-123"})
    assert resp.status_code == 200


def test_archive_outbox_resilient_breaker_handling(monkeypatch):
    incident_id = "INC-ARCHIVE-BREAKER"
    create_incident(IncidentCreate(incident_id=incident_id, severity="high", affected_services=["payment-service"]))
    build_decision(incident_id)

    incident = find_incident(incident_id)
    incident["status"] = "ESCALATED"
    enqueue_archive(incident)

    # Force breaker to allow but mock resilient_call raising CircuitOpen
    with patch("app.orchestration.archive.resilient_call", side_effect=CircuitOpen("Circuit open for M5")):
        # deliver_archive should catch CircuitOpen and record it
        success = deliver_archive(incident_id)
        assert success is False
        job = incident["_archive_outbox"]
        assert job["last_error"] == "CircuitOpen"
        assert job["status"] == "PENDING"


def test_error_taxonomy_and_sanitization():
    # Test path sanitization
    msg = "Failed opening C:\\secret\\project\\config.yaml on server\nTraceback at line 42"
    clean = sanitize_message(msg)
    assert "C:\\secret\\project" not in clean
    assert "Traceback" not in clean
    assert "<internal-path>" in clean

    # Test IntegrationError conversion via as_http_error
    err = IntegrationError("Upstream timeout on /var/run/k8s.sock\nCall failed", status_code=503, retryable=True)
    http_exc = as_http_error(err)
    assert http_exc.status_code == 503
    assert http_exc.detail["category"] == "dependency_error"
    assert "/var/run" not in http_exc.detail["message"]
    assert http_exc.detail["retryable"] is True
    assert http_exc.headers == {"Retry-After": "15"}
