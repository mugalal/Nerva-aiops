import os
from fastapi import APIRouter, HTTPException, Header
from app.api.incidents import find_incident, update_incident_status
from app.contracts import ActionResult, DecisionProposal
from app.state_machine.states import IncidentStatus
from app.orchestration.orchestrator import execute_approved_action
from app.decision_engine.models import DecisionAction
from app.remediation.audit import add_audit_record
from app.providers.errors import as_http_error

router = APIRouter(prefix="/api/incidents", tags=["approvals"])

def verify_approver_auth(
    authorization: str | None = Header(default=None),
    x_nexus_approver_token: str | None = Header(default=None),
    x_nexus_approver: str | None = Header(default="operator"),
):
    expected_token = os.getenv("NEXUS_APPROVAL_TOKEN", os.getenv("M5_SHARED_API_TOKEN"))
    if expected_token:
        received = x_nexus_approver_token
        if not received and authorization and authorization.startswith("Bearer "):
            received = authorization[7:].strip()
        if received != expected_token:
            raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing approval token")
    return x_nexus_approver or "operator"

@router.get("/{incident_id}/proposal", response_model=DecisionProposal)
def get_proposal(incident_id: str):
    incident = find_incident(incident_id)
    if incident is None or "_proposal" not in incident:
        raise HTTPException(status_code=404, detail="Decision proposal not found")
    return incident["_proposal"]

@router.get("/{incident_id}/actions/latest", response_model=ActionResult)
def get_action_result(incident_id: str):
    incident = find_incident(incident_id)
    if incident is None or "_action_result" not in incident:
        raise HTTPException(status_code=404, detail="Action result not found")
    return incident["_action_result"]

@router.post("/{incident_id}/approve", response_model=ActionResult)
def approve_incident(
    incident_id: str,
    authorization: str | None = Header(default=None),
    x_nexus_approver_token: str | None = Header(default=None),
    x_nexus_approver: str | None = Header(default="operator"),
):
    approver = verify_approver_auth(authorization, x_nexus_approver_token, x_nexus_approver)
    incident = find_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    proposed_action = incident.get("proposed_action")
    if proposed_action is None:
        raise HTTPException(status_code=400, detail="No proposed action found for this incident")
    try:
        action = DecisionAction(proposed_action)
        replicas = None
        if action == DecisionAction.SCALE:
            scale_option = incident.get("scale_option")
            if not isinstance(scale_option, dict):
                raise ValueError("No valid scale option found for this incident")
            replicas = scale_option.get("replicas")
            if isinstance(replicas, bool) or not isinstance(replicas, int) or not 1 <= replicas <= 10:
                raise ValueError("Replica count must be an integer between 1 and 10")
        add_audit_record(incident_id, "MANUAL_APPROVAL_GRANTED", {"approver": approver, "action": action.value})
        execute_approved_action(incident_id, action, approved=True, replicas=replicas)
        return incident["_action_result"]
    except ValueError as exc:
        raise as_http_error(exc) from exc

@router.post("/{incident_id}/reject")
def reject_incident(incident_id: str):
    incident = find_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    try:
        updated = update_incident_status(incident_id, IncidentStatus.ESCALATED)
    except ValueError as exc:
        raise as_http_error(exc) from exc
    return {"incident_id": incident_id, "status": updated["status"], "approved": False}
