from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.api.incidents import find_incident
from app.decision_engine.models import DecisionAction
from app.orchestration.orchestrator import execute_approved_action
from app.remediation.models import RemediationResult
from app.remediation.audit import audit_records
from app.providers.errors import as_http_error

router = APIRouter(prefix="/internal/remediation", tags=["remediation"])

class RemediationRequest(BaseModel):
    incident_id: str
    action: DecisionAction
    approved: bool
    replicas: int | None = Field(default=None, strict=True)

import os
from fastapi import Header

@router.post("/execute", response_model=RemediationResult)
def execute(
    request: RemediationRequest,
    authorization: str | None = Header(default=None),
    x_nexus_approver_token: str | None = Header(default=None),
):
    expected_token = os.getenv("NEXUS_APPROVAL_TOKEN", os.getenv("M5_SHARED_API_TOKEN"))
    if expected_token:
        received = x_nexus_approver_token
        if not received and authorization and authorization.startswith("Bearer "):
            received = authorization[7:].strip()
        if received != expected_token:
            raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing approval token")
    if find_incident(request.incident_id) is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    try:
        return execute_approved_action(request.incident_id, request.action, request.approved, request.replicas)
    except ValueError as exc:
        raise as_http_error(exc) from exc

@router.get("/audit")
def get_audit_records():
    return audit_records
