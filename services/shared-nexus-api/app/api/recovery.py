from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, StrictBool
from app.api.incidents import find_incident, public_incident
from app.contracts import IncidentView
from app.orchestration.orchestrator import apply_recovery_result, validate_and_apply_recovery
from app.providers import recovery_provider
from app.providers.errors import as_http_error

class RecoveryRequest(BaseModel):
    incident_id: str
    success: StrictBool

router = APIRouter(prefix="/internal/recovery", tags=["recovery"])

@router.post("/validate", response_model=IncidentView)
def validate_recovery(request: RecoveryRequest):
    if recovery_provider.RECOVERY_PROVIDER != "mock":
        raise HTTPException(status_code=409, detail="Caller-supplied recovery booleans are disabled with real M1 validation")
    if find_incident(request.incident_id) is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    try:
        return public_incident(apply_recovery_result(request.incident_id, request.success))
    except ValueError as exc:
        raise as_http_error(exc) from exc

@router.post("/validate/{incident_id}", response_model=IncidentView)
def validate_recovery_from_m1(incident_id: str):
    if find_incident(incident_id) is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    try:
        return public_incident(validate_and_apply_recovery(incident_id))
    except ValueError as exc:
        raise as_http_error(exc) from exc
