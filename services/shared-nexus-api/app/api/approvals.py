
from fastapi import APIRouter, HTTPException

from app.api.incidents import find_incident, update_incident_status
from app.state_machine.states import IncidentStatus
from app.orchestration.orchestrator import execute_approved_action
from app.decision_engine.models import DecisionAction


router = APIRouter(
    prefix="/api/incidents",
    tags=["approvals"]
)


@router.post("/{incident_id}/approve")
def approve_incident(incident_id: str):
    incident = find_incident(incident_id)

    if incident is None:
        raise HTTPException(
            status_code=404,
            detail="Incident not found"
        )

    proposed_action = incident.get("proposed_action")

    if proposed_action is None:
        raise HTTPException(
            status_code=400,
            detail="No proposed action found for this incident"
        )

    try:
        action = DecisionAction(proposed_action)
        replicas = None

        if action == DecisionAction.SCALE:
            scale_option = incident.get("scale_option")

            if not isinstance(scale_option, dict):
                raise ValueError(
                    "No valid scale option found for this incident"
                )

            replicas = scale_option.get("replicas")

            if not isinstance(replicas, int) or isinstance(replicas, bool):
                raise ValueError(
                    "Invalid replica count in scale option"
                )

            if not 1 <= replicas <= 10:
                raise ValueError(
                    "Replica count must be between 1 and 10"
                )

        result = execute_approved_action(
            incident_id,
            action,
            approved=True,
            replicas=replicas
        )

        return result

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )


@router.post("/{incident_id}/reject")
def reject_incident(incident_id: str):
    incident = find_incident(incident_id)

    if incident is None:
        return {
            "message": "Incident not found"
        }

    try:
        updated_incident = update_incident_status(
            incident_id,
            IncidentStatus.ESCALATED
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    return {
        "incident_id": incident_id,
        "status": updated_incident["status"],
        "approved": False
    }
