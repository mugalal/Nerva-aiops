from fastapi import APIRouter
from app.api.incidents import find_incident, update_incident_status
from app.state_machine.states import IncidentStatus
from app.state_machine.machine import transition
from fastapi import APIRouter, HTTPException
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
        result = execute_approved_action(
            incident_id,
            DecisionAction(proposed_action),
            approved=True
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