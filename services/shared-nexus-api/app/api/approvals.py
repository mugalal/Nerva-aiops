from fastapi import APIRouter
from app.api.incidents import find_incident, update_incident_status
from app.state_machine.states import IncidentStatus
from app.state_machine.machine import transition
from fastapi import APIRouter, HTTPException

router = APIRouter(
    prefix="/api/incidents",
    tags=["approvals"]
)

@router.post("/{incident_id}/approve")
def approve_incident(incident_id: str):
    incident = find_incident(incident_id)

    if incident is None:
        return {
            "message": "Incident not found"
        }

    try:
        updated_incident = update_incident_status(
            incident_id,
            IncidentStatus.EXECUTING,
            approved=True
        )
    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    return updated_incident
    
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