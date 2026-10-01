from fastapi.testclient import TestClient
from app.main import app
from app.api.incidents import update_incident_status
from app.state_machine.states import IncidentStatus


client = TestClient(app)


def test_execute_rollback_with_approval():
    client.post(
    "/api/incidents/",
    json={
        "incident_id": "INC-REMEDIATION-001",
        "severity": "high"
    }
)

    update_incident_status(
        "INC-REMEDIATION-001",
        IncidentStatus.CORRELATING
    )
    update_incident_status(
        "INC-REMEDIATION-001",
        IncidentStatus.DIAGNOSING
    )
    update_incident_status(
        "INC-REMEDIATION-001",
        IncidentStatus.DIAGNOSED
    )
    update_incident_status(
        "INC-REMEDIATION-001",
        IncidentStatus.ACTION_PROPOSED
    )
    update_incident_status(
        "INC-REMEDIATION-001",
        IncidentStatus.AWAITING_APPROVAL
    )
    response = client.post(
        "/internal/remediation/execute",
       json={
        "incident_id": "INC-REMEDIATION-001",
        "action": "ROLLBACK",
        "approved": True
}
    )

    assert response.status_code == 200

    data = response.json()

    assert data["action"] == "ROLLBACK"
    assert data["success"] is True
    incident_response = client.get(
    "/api/incidents/INC-REMEDIATION-001"
    )

    incident_data = incident_response.json()

    assert incident_data["status"] == "VALIDATING"
    
def test_execute_scale_with_approval():
    client.post(
    "/api/incidents/",
    json={
        "incident_id": "INC-REMEDIATION-002",
        "severity": "high"
    }
)

    update_incident_status(
        "INC-REMEDIATION-002",
        IncidentStatus.CORRELATING
    )
    update_incident_status(
        "INC-REMEDIATION-002",
        IncidentStatus.DIAGNOSING
    )
    update_incident_status(
        "INC-REMEDIATION-002",
        IncidentStatus.DIAGNOSED
    )
    update_incident_status(
        "INC-REMEDIATION-002",
        IncidentStatus.ACTION_PROPOSED
    )
    update_incident_status(
        "INC-REMEDIATION-002",
        IncidentStatus.AWAITING_APPROVAL
    )
    response = client.post(
        "/internal/remediation/execute",
        json={
            "incident_id": "INC-REMEDIATION-002",
            "action": "SCALE",
            "approved": True
        }
    )

    assert response.status_code == 200

    data = response.json()

    assert data["action"] == "SCALE"
    assert data["success"] is True
    
def test_execute_without_approval_fails():
    client.post(
    "/api/incidents/",
    json={
        "incident_id": "INC-REMEDIATION-003",
        "severity": "high"
    }
)

    update_incident_status(
        "INC-REMEDIATION-003",
        IncidentStatus.CORRELATING
    )
    update_incident_status(
        "INC-REMEDIATION-003",
        IncidentStatus.DIAGNOSING
    )
    update_incident_status(
        "INC-REMEDIATION-003",
        IncidentStatus.DIAGNOSED
    )
    update_incident_status(
        "INC-REMEDIATION-003",
        IncidentStatus.ACTION_PROPOSED
    )
    update_incident_status(
        "INC-REMEDIATION-003",
        IncidentStatus.AWAITING_APPROVAL
    )
    response = client.post(
        "/internal/remediation/execute",
        json={
            "incident_id": "INC-REMEDIATION-003",
            "action": "ROLLBACK",
            "approved": False
        }
    )

    assert response.status_code == 400