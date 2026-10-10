from fastapi.testclient import TestClient
from app.main import app

from app.api.incidents import update_incident_status
from app.state_machine.states import IncidentStatus


client = TestClient(app)


def test_successful_recovery_resolves_incident():
    client.post(
        "/api/incidents/",
        json={
            "incident_id": "INC-RECOVERY-001",
            "severity": "high",

        "affected_services": ["payment-service"],}
    )

    update_incident_status(
        "INC-RECOVERY-001",
        IncidentStatus.CORRELATING
    )
    update_incident_status(
        "INC-RECOVERY-001",
        IncidentStatus.DIAGNOSING
    )
    update_incident_status(
        "INC-RECOVERY-001",
        IncidentStatus.DIAGNOSED
    )
    update_incident_status(
        "INC-RECOVERY-001",
        IncidentStatus.ACTION_PROPOSED
    )
    update_incident_status(
        "INC-RECOVERY-001",
        IncidentStatus.AWAITING_APPROVAL
    )
    update_incident_status(
        "INC-RECOVERY-001",
        IncidentStatus.EXECUTING,
        approved=True
    )
    update_incident_status(
        "INC-RECOVERY-001",
        IncidentStatus.VALIDATING
    )

    response = client.post(
        "/internal/recovery/validate",
        json={
            "incident_id": "INC-RECOVERY-001",
            "success": True

        }
    )


    assert response.status_code == 200
    assert response.json()["status"] == "RESOLVED"

    def test_failed_recovery_marks_failed_remediation():
        client.post(
            "/api/incidents/",
            json={
                "incident_id": "INC-RECOVERY-002",
                "severity": "high",

        "affected_services": ["payment-service"],}
        )

        update_incident_status(
            "INC-RECOVERY-002",
            IncidentStatus.CORRELATING
        )
        update_incident_status(
            "INC-RECOVERY-002",
            IncidentStatus.DIAGNOSING
        )
        update_incident_status(
            "INC-RECOVERY-002",
            IncidentStatus.DIAGNOSED
        )
        update_incident_status(
            "INC-RECOVERY-002",
            IncidentStatus.ACTION_PROPOSED
        )
        update_incident_status(
            "INC-RECOVERY-002",
            IncidentStatus.AWAITING_APPROVAL
        )
        update_incident_status(
            "INC-RECOVERY-002",
            IncidentStatus.EXECUTING,
            approved=True
        )
        update_incident_status(
            "INC-RECOVERY-002",
            IncidentStatus.VALIDATING
        )

        response = client.post(
            "/internal/recovery/validate",
            json={
                "incident_id": "INC-RECOVERY-002",
                "success": False
            }
        )

        assert response.status_code == 200
        # assert response.json()["status"] == "FAILED_REMEDIATION"
        assert response.json()["status"] == "FAILED_REMEDIATION"
