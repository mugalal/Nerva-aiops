from app.orchestration.orchestrator import build_decision
from app.decision_engine.models import DecisionAction
from app.api.incidents import update_incident_status
from app.state_machine.states import IncidentStatus
from fastapi.testclient import TestClient
from app.main import app
from app.remediation.executor import execute_remediation
from app.orchestration.orchestrator import (
    build_decision,
    execute_approved_action,
)
from app.orchestration.orchestrator import apply_recovery_result
client = TestClient(app)


def test_build_decision_returns_result():
    client.post(
        "/api/incidents/",
        json={
            "incident_id": "INC-ORCH-001",
            "severity": "high",

        "affected_services": ["payment-service"],}
    )



    result = build_decision("INC-ORCH-001")

    assert result.action in {
        DecisionAction.ROLLBACK,
        DecisionAction.SCALE,
        DecisionAction.ESCALATE
    }
    incident_response = client.get(
    "/api/incidents/INC-ORCH-001"
)

    incident_data = incident_response.json()

    assert incident_data["status"] == "AWAITING_APPROVAL"


def test_execute_approved_action_moves_to_validating():
    client.post(
        "/api/incidents/",
        json={
            "incident_id": "INC-ORCH-002",
            "severity": "high",

        "affected_services": ["payment-service"],}
    )



    decision = build_decision("INC-ORCH-002")

    replicas = (
        decision.scale_option["replicas"]
        if decision.action == DecisionAction.SCALE
        else None
)

    result = execute_approved_action(
        "INC-ORCH-002",
        decision.action,
        approved=True,
        replicas=replicas
    )

    assert result.success is True

    incident_response = client.get(
        "/api/incidents/INC-ORCH-002"
    )

    assert incident_response.json()["status"] == "VALIDATING"

def test_apply_recovery_result_resolves_incident():
    client.post(
        "/api/incidents/",
        json={
            "incident_id": "INC-ORCH-003",
            "severity": "high",

        "affected_services": ["payment-service"],}
    )



    decision = build_decision("INC-ORCH-003")


    replicas = (
        decision.scale_option["replicas"]
        if decision.action == DecisionAction.SCALE
        else None
)

    execute_approved_action(
        "INC-ORCH-003",
        decision.action,
        approved=True,
        replicas=replicas
    )

    incident = apply_recovery_result(
        "INC-ORCH-003",
        success=True
    )

    assert incident["status"] == "RESOLVED"

def test_apply_recovery_result_escalates_on_failure():
    client.post(
        "/api/incidents/",
        json={
            "incident_id": "INC-ORCH-004",
            "severity": "high",

        "affected_services": ["payment-service"],}
    )



    decision = build_decision("INC-ORCH-004")
    replicas = (
        decision.scale_option["replicas"]
        if decision.action == DecisionAction.SCALE
        else None
)

    execute_approved_action(
        "INC-ORCH-004",
        decision.action,
        approved=True,
        replicas=replicas
    )

    incident = apply_recovery_result(
        "INC-ORCH-004",
        success=False
    )

    assert incident["status"] == "ESCALATED"
