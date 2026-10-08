from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.incidents import find_incident, update_incident_status
from app.state_machine.states import IncidentStatus
from app.orchestration.orchestrator import (
    apply_recovery_result,
    validate_and_apply_recovery,
)

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
        return apply_recovery_result(
            request.incident_id,
            request.success
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )
        
@router.post("/validate/{incident_id}")
def validate_recovery_from_m1(incident_id: str):
    incident = find_incident(incident_id)

    if incident is None:
        raise HTTPException(
            status_code=404,
            detail="Incident not found"
        )

    if incident["status"] != IncidentStatus.VALIDATING:
        raise HTTPException(
            status_code=400,
            detail="Incident must be in VALIDATING state"
        )

    try:
        return validate_and_apply_recovery(incident_id)

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )