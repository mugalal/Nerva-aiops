from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.decision_engine.models import DecisionAction
from app.remediation.executor import execute_remediation
from app.api.incidents import find_incident, update_incident_status
from app.state_machine.states import IncidentStatus
from app.remediation.audit import audit_records

router = APIRouter(
    prefix="/internal/remediation",
    tags=["remediation"]
)

class RemediationRequest(BaseModel):
    incident_id: str
    action: DecisionAction
    approved: bool
    replicas: int | None = None  # Optional field for scaling action
@router.post("/execute")
def execute(request: RemediationRequest):
    incident = find_incident(request.incident_id)

    if incident is None:
        raise HTTPException(
            status_code=404,
            detail="Incident not found"
        )

    try:
        update_incident_status(
            request.incident_id,
            IncidentStatus.EXECUTING,
            approved=request.approved
        )

        result = execute_remediation(
            request.action,
            request.approved,
            replicas=request.replicas  # Pass replicas for scaling action
        )
        update_incident_status(
        request.incident_id,
        IncidentStatus.VALIDATING
    )

        return result

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )
@router.get("/audit")
def get_audit_records():
    return audit_records