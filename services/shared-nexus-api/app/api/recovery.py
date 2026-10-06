from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.incidents import find_incident, update_incident_status
from app.state_machine.states import IncidentStatus

class RecoveryRequest(BaseModel):
    incident_id: str
    success: bool
    
router = APIRouter(
    prefix="/internal/recovery",
    tags=["recovery"]
)
@router.post("/validate")
def validate_recovery(request: RecoveryRequest):
    incident = find_incident(request.incident_id)

    if incident is None:
        raise HTTPException(
            status_code=404,
            detail="Incident not found"
        )

    try:
        if request.success:
            return update_incident_status(
                request.incident_id,
                IncidentStatus.RESOLVED,
                recovery_validated=True
            )

        update_incident_status(
            request.incident_id,
            IncidentStatus.FAILED_REMEDIATION
        )
        return update_incident_status(
            request.incident_id,
            IncidentStatus.ESCALATED
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )