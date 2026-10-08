from app.api.incidents import incidents, update_incident_status
from app.state_machine.states import IncidentStatus
from app.orchestration.orchestrator import (
    build_decision,
    execute_approved_action,
    validate_and_apply_recovery,
)


def test_full_m4_e2e_flow():
    incident_id = "INC-E2E-M4-001"

    incidents.append({
        "incident_id": incident_id,
        "severity": "HIGH",
        "status": IncidentStatus.DETECTED,
    })


    decision = build_decision(incident_id)

    assert decision.action.value in [
        "ROLLBACK",
        "SCALE",
        "ESCALATE",
    ]

    replicas = None

    if decision.action.value == "SCALE":
        replicas = decision.scale_option["replicas"]

    result = execute_approved_action(
        incident_id,
        decision.action,
        approved=True,
        replicas=replicas,
    )

    assert result.success is True

    final_incident = validate_and_apply_recovery(
        incident_id
    )

    assert final_incident["status"] == IncidentStatus.RESOLVED