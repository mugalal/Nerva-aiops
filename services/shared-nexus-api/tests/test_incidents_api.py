
from fastapi.testclient import TestClient

from app.main import app
from app.api.incidents import (
    find_incident,
    update_incident_status,
)
from app.state_machine.states import IncidentStatus

client = TestClient(app)


def test_create_incident():
    response = client.post(
        "/api/incidents",
        json={
            "incident_id": "INC-TEST-001",
            "severity": "high",
        "affected_services": ["payment-service"],
        },
    )

    assert response.status_code == 200

    data = response.json()

    assert data["incident_id"] == "INC-TEST-001"
    assert data["severity"] == "high"
    assert data["status"] == "DETECTED"


def test_list_incidents():
    response = client.get("/api/incidents")

    assert response.status_code == 200

    data = response.json()

    assert isinstance(data, list)


def test_get_incident_by_id():
    create_response = client.post(
        "/api/incidents",
        json={
            "incident_id": "INC-TEST-002",
            "severity": "medium",
        "affected_services": ["payment-service"],
        },
    )

    assert create_response.status_code == 200

    response = client.get(
        "/api/incidents/INC-TEST-002"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["incident_id"] == "INC-TEST-002"
    assert data["severity"] == "medium"
    assert data["status"] == "DETECTED"


def test_approve_incident(monkeypatch):
    client.post(
        "/api/incidents",
        json={
            "incident_id": "INC-APPROVE-001",
            "severity": "high",
        "affected_services": ["payment-service"],
        },
    )

    update_incident_status(
        "INC-APPROVE-001",
        IncidentStatus.CORRELATING,
    )
    update_incident_status(
        "INC-APPROVE-001",
        IncidentStatus.DIAGNOSING,
    )
    update_incident_status(
        "INC-APPROVE-001",
        IncidentStatus.DIAGNOSED,
    )
    update_incident_status(
        "INC-APPROVE-001",
        IncidentStatus.ACTION_PROPOSED,
    )
    update_incident_status(
        "INC-APPROVE-001",
        IncidentStatus.AWAITING_APPROVAL,
    )

    incident = find_incident("INC-APPROVE-001")
    incident["proposed_action"] = "ROLLBACK"

    # Mock Jenkins remediation to avoid modifying Kubernetes
    from app.remediation.models import RemediationResult

    def mock_execute_remediation(
        action, approved, replicas=None, service=None, expected_state=None, rollback_target=None
    ):
        return RemediationResult(
            action=action,
            success=True,
            message="Mock remediation successful",
        )

    monkeypatch.setattr(
        "app.orchestration.orchestrator.execute_remediation",
        mock_execute_remediation,
    )

    response = client.post(
        "/api/incidents/INC-APPROVE-001/approve"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["action"] == "ROLLBACK"
    assert data["status"] == "SUCCESS"

    incident_response = client.get(
        "/api/incidents/INC-APPROVE-001"
    )

    assert incident_response.status_code == 200
    assert incident_response.json()["status"] == "VALIDATING"


def test_approve_incident_too_early():
    client.post(
        "/api/incidents",
        json={
            "incident_id": "INC-EARLY-001",
            "severity": "high",
        "affected_services": ["payment-service"],
        },
    )

    response = client.post(
        "/api/incidents/INC-EARLY-001/approve"
    )

    assert response.status_code == 400


def test_reject_incident():
    client.post(
        "/api/incidents",
        json={
            "incident_id": "INC-REJECT-001",
            "severity": "medium",
        "affected_services": ["payment-service"],
        },
    )

    update_incident_status(
        "INC-REJECT-001",
        IncidentStatus.CORRELATING,
    )
    update_incident_status(
        "INC-REJECT-001",
        IncidentStatus.DIAGNOSING,
    )
    update_incident_status(
        "INC-REJECT-001",
        IncidentStatus.DIAGNOSED,
    )
    update_incident_status(
        "INC-REJECT-001",
        IncidentStatus.ACTION_PROPOSED,
    )
    update_incident_status(
        "INC-REJECT-001",
        IncidentStatus.AWAITING_APPROVAL,
    )

    response = client.post(
        "/api/incidents/INC-REJECT-001/reject"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["incident_id"] == "INC-REJECT-001"
    assert data["approved"] is False
    assert data["status"] == "ESCALATED"
