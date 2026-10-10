from fastapi import APIRouter, HTTPException, Header
from datetime import datetime, timezone, timedelta
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel
from app.api.incidents import find_incident, lock_for, persist_incidents, update_incident_status, incident_operation, _committed_incidents
from app.api.auth import verify_approver_auth
from app.contracts import ActionResult, DecisionProposal, IncidentView
from app.state_machine.states import IncidentStatus
from app.orchestration.orchestrator import execute_approved_action
from app.decision_engine.models import DecisionAction
from app.remediation.audit import add_audit_record
from app.providers.errors import as_http_error

router = APIRouter(prefix="/api/incidents", tags=["approvals"])

@router.get("/{incident_id}/proposal", response_model=DecisionProposal)
def get_proposal(incident_id: str):
    incident = next((item for item in _committed_incidents if item["incident_id"] == incident_id), None)
    if incident is None:
        incident = find_incident(incident_id)
    if incident is None or "_proposal" not in incident:
        raise HTTPException(status_code=404, detail="Decision proposal not found")
    return incident["_proposal"]

@router.get("/{incident_id}/actions/latest", response_model=ActionResult)
def get_action_result(incident_id: str):
    incident = next((item for item in _committed_incidents if item["incident_id"] == incident_id), None)
    if incident is None:
        incident = find_incident(incident_id)
    if incident is None or "_action_result" not in incident:
        raise HTTPException(status_code=404, detail="Action result not found")
    return incident["_action_result"]



import hashlib
import json

def proposal_digest(proposal: dict) -> str:
    return hashlib.sha256(json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

@router.post("/{incident_id}/approve", response_model=ActionResult)
def approve_incident(
    incident_id: str,
    authorization: str | None = Header(default=None),
    x_nexus_approver_token: str | None = Header(default=None),
    x_nexus_approver: str | None = Header(default="operator"),
    x_proposal_digest: str | None = Header(default=None),
):
    approver = verify_approver_auth(authorization, x_nexus_approver_token, x_nexus_approver)
    with incident_operation(incident_id):
        incident = find_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        if x_proposal_digest is not None:
            current_proposal = incident.get("_proposal") or {}
            if x_proposal_digest != proposal_digest(current_proposal):
                raise HTTPException(status_code=409, detail="The proposal changed since review; reload and review again")
        created_at_raw = incident.get("_proposal_created_at")
        if created_at_raw:
            try:
                from app.config import PROPOSAL_TTL_SECONDS
                created_at = datetime.fromisoformat(created_at_raw)
                if (datetime.now(timezone.utc) - created_at).total_seconds() > PROPOSAL_TTL_SECONDS:
                    raise HTTPException(status_code=409, detail="Proposal has expired; please re-diagnose to capture fresh state")
            except (ValueError, TypeError):
                pass
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
            execute_approved_action(incident_id, action, approved=True, replicas=replicas, approver=approver)
            return incident["_action_result"]
        except ValueError as exc:
            raise as_http_error(exc) from exc

@router.post("/{incident_id}/reject")
def reject_incident(incident_id: str, authorization: str | None = Header(default=None),
                    x_nexus_approver_token: str | None = Header(default=None),
                    x_nexus_approver: str | None = Header(default="operator")):
    approver = verify_approver_auth(authorization, x_nexus_approver_token, x_nexus_approver)
    with lock_for(incident_id):
        incident = find_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        if incident["status"] != IncidentStatus.AWAITING_APPROVAL.value:
            raise HTTPException(status_code=409, detail="Only an awaiting approval proposal can be rejected")
        try:
            add_audit_record(incident_id, "MANUAL_APPROVAL_REJECTED", {"approver": approver,
                "action": incident.get("proposed_action")})
            updated = update_incident_status(incident_id, IncidentStatus.ESCALATED)
            from app.orchestration.archive import enqueue_archive
            enqueue_archive(updated)
        except ValueError as exc:
            raise as_http_error(exc) from exc
        return {"incident_id": incident_id, "status": updated["status"], "approved": False}


@router.post("/{incident_id}/archive/retry")
def retry_archive(incident_id: str, authorization: str | None = Header(default=None),
                  x_nexus_approver_token: str | None = Header(default=None),
                  x_nexus_approver: str | None = Header(default="operator")):
    approver = verify_approver_auth(authorization, x_nexus_approver_token, x_nexus_approver)
    with lock_for(incident_id):
        incident = find_incident(incident_id)
        if incident is None:
            raise HTTPException(404, "Incident not found")
        from app.orchestration.archive import enqueue_archive
        enqueue_archive(incident)
        job = incident.get("_archive_outbox")
        if job is None:
            raise HTTPException(409, incident.get("_archive_blocked_reason", "Incident has no terminal archive outcome"))
        if job["status"] != "DELIVERED":
            job.update(status="PENDING", next_attempt_at=datetime.now(timezone.utc).isoformat(), last_error=None)
            persist_incidents()
            add_audit_record(incident_id, "MEMORY_ARCHIVE_RETRY_REQUESTED", {"approver": approver, "job_id": job["job_id"]})
        return {"incident_id": incident_id, "archive_status": job["status"], "attempts": job["attempts"]}


class ReconciliationRequest(BaseModel):
    outcome: Literal["CONFIRMED", "FAILED"]
    note: str = ""


@router.post("/{incident_id}/reconcile", response_model=IncidentView)
def reconcile_incident(
    incident_id: str,
    request: ReconciliationRequest,
    authorization: str | None = Header(default=None),
    x_nexus_approver_token: str | None = Header(default=None),
    x_nexus_approver: str | None = Header(default="operator"),
):
    approver = verify_approver_auth(authorization, x_nexus_approver_token, x_nexus_approver)
    with incident_operation(incident_id):
        incident = find_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        if not incident.get("_execution_ambiguous") and incident["status"] != IncidentStatus.ESCALATED.value:
            raise HTTPException(status_code=409, detail="Only ambiguous or escalated incidents can be reconciled")
        intent = incident.pop("_execution_intent", None) or {}
        incident.pop("_execution_ambiguous", None)
        incident.pop("_execution_reconciliation_reason", None)
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        action_id = intent.get("action_id") or ("ACT-" + uuid4().hex)
        raw_action = intent.get("action") or incident.get("proposed_action") or "ROLLBACK"
        action_val = "SCALE" if raw_action == "SCALE" else "ROLLBACK"
        if request.outcome == "CONFIRMED":
            incident["action_completed_at"] = now_iso
            incident["recovery_deadline_at"] = (now + timedelta(seconds=360)).isoformat()
            incident["_action_result"] = ActionResult(
                action_id=action_id,
                incident_id=incident_id,
                action=action_val,
                status="SUCCESS",
                started_at=intent.get("started_at") or now_iso,
                completed_at=now_iso,
            ).model_dump(mode="json")
            incident["_execution_result"] = {
                "action": action_val,
                "success": True,
                "message": f"Operator reconciled: {request.note or 'Rollout confirmed manually'}",
            }
            add_audit_record(incident_id, "MANUAL_RECONCILIATION_CONFIRMED", {
                "approver": approver, "note": request.note, "outcome": "CONFIRMED",
            })
            update_incident_status(incident_id, IncidentStatus.VALIDATING, reconciled=True)
        else:
            incident["_action_result"] = ActionResult(
                action_id=action_id,
                incident_id=incident_id,
                action=action_val,
                status="FAILED",
                started_at=intent.get("started_at") or now_iso,
                completed_at=now_iso,
            ).model_dump(mode="json")
            incident["_execution_result"] = {
                "action": action_val,
                "success": False,
                "message": f"Operator reconciled: {request.note or 'Rollout aborted manually'}",
            }
            add_audit_record(incident_id, "MANUAL_RECONCILIATION_FAILED", {
                "approver": approver, "note": request.note, "outcome": "FAILED",
            })
            from app.orchestration.archive import enqueue_archive
            enqueue_archive(incident)
            persist_incidents()
        from app.api.incidents import public_incident
        return public_incident(incident)

