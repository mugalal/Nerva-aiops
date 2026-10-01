from fastapi.testclient import TestClient

from app.main import app
from app.api.incidents import update_incident_status
from app.state_machine.states import IncidentStatus



client = TestClient(app)


def test_create_incident():
    response = client.post(
        "/api/incidents",
        json={
            "incident_id": "INC-TEST-001",
            "severity": "high"
        }
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
            "severity": "medium"
        }
    )

    assert create_response.status_code == 200

    response = client.get("/api/incidents/INC-TEST-002")

    assert response.status_code == 200

    data = response.json()

    assert data["incident_id"] == "INC-TEST-002"
    assert data["severity"] == "medium"
    assert data["status"] == "DETECTED"
    
def test_approve_incident():
    client.post(
        "/api/incidents",
        json={
            "incident_id": "INC-APPROVE-001",
            "severity": "high"
        }
    )

    update_incident_status(
        "INC-APPROVE-001",
        IncidentStatus.CORRELATING
    )

    update_incident_status(
        "INC-APPROVE-001",
        IncidentStatus.DIAGNOSING
    )

    update_incident_status(
        "INC-APPROVE-001",
        IncidentStatus.DIAGNOSED
    )

    update_incident_status(
        "INC-APPROVE-001",
        IncidentStatus.ACTION_PROPOSED
    )

    update_incident_status(
        "INC-APPROVE-001",
        IncidentStatus.AWAITING_APPROVAL
    )

    response = client.post(
        "/api/incidents/INC-APPROVE-001/approve"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["status"] == "EXECUTING"
    
def test_approve_incident_too_early():
    client.post(
        "/api/incidents",
        json={
            "incident_id": "INC-EARLY-001",
            "severity": "high"
        }
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
            "severity": "medium"
        }
    )

    response = client.post(
        "/api/incidents/INC-REJECT-001/reject"
    )

    assert response.status_code == 200

    data = response.json()

    assert data["incident_id"] == "INC-REJECT-001"
    assert data["approved"] is False