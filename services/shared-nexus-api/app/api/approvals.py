from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, Field, field_validator
from app.api.incidents import committed_incident, find_incident, lock_for, persist_incidents, public_incident
from app.api.auth import named_approver_identity, verify_approver_auth, require_explicit_identity
from app.contracts import ActionResult, DecisionProposal, IncidentView
from app.state_machine.states import IncidentStatus
from app.state_machine.machine import transition
from app.orchestration.orchestrator import execute_approved_action, reconcile_execution
from app.orchestration.proposals import digest_of, proposal_digest as incident_proposal_digest
from app.decision_engine.models import DecisionAction
from app.remediation.audit import add_audit_record
from app.providers.errors import ConflictError, api_error, as_http_error

router = APIRouter(prefix="/api/incidents", tags=["approvals"])


def proposal_digest(proposal: dict) -> str:
    """Compatibility helper: digest of a bare proposal (no diagnosis timestamp)."""
    return digest_of(proposal)


def _not_found(what="Incident"):
    return api_error(404, "not_found", f"{what} not found")


@router.get("/{incident_id}/proposal", response_model=DecisionProposal)
def get_proposal(incident_id: str, response: Response):
    incident = committed_incident(incident_id)
    if incident is None or "_proposal" not in incident:
        raise _not_found("Decision proposal")
    # The frozen DecisionProposal body is unchanged; the digest an approval
    # must quote travels as a header (and in /context).
    response.headers["X-Proposal-Digest"] = incident_proposal_digest(incident)
    return incident["_proposal"]


@router.get("/{incident_id}/actions/latest", response_model=ActionResult)
def get_action_result(incident_id: str):
    incident = committed_incident(incident_id)
    if incident is None or "_action_result" not in incident:
        raise _not_found("Action result")
    return incident["_action_result"]


@router.post("/{incident_id}/approve", response_model=ActionResult)
def approve_incident(
    incident_id: str,
    authorization: str | None = Header(default=None),
    x_nexus_approver_token: str | None = Header(default=None),
    x_nexus_approver: str | None = Header(default="operator"),
    x_proposal_digest: str | None = Header(default=None),
):
    approver = verify_approver_auth(authorization, x_nexus_approver_token, x_nexus_approver)
    # Read the proposal under the lock, then execute WITHOUT holding it: the
    # orchestrator re-validates everything atomically before its durable intent.
    with lock_for(incident_id, timeout=5.0):
        incident = find_incident(incident_id)
        if incident is None:
            raise _not_found()
        proposed_action = incident.get("proposed_action")
        scale_option = incident.get("scale_option")
    if proposed_action is None:
        raise api_error(409, "conflict", "No proposed action exists for this incident")
    try:
        action = DecisionAction(proposed_action)
        replicas = None
        if action == DecisionAction.SCALE:
            if not isinstance(scale_option, dict):
                raise ValueError("No valid scale option found for this incident")
            replicas = scale_option.get("replicas")
        execute_approved_action(incident_id, action, approved=True, replicas=replicas, approver=approver,
                                digest=x_proposal_digest)
    except ValueError as exc:
        raise as_http_error(exc) from exc
    return committed_incident(incident_id)["_action_result"]


@router.post("/{incident_id}/reject")
def reject_incident(incident_id: str, authorization: str | None = Header(default=None),
                    x_nexus_approver_token: str | None = Header(default=None),
                    x_nexus_approver: str | None = Header(default="operator"),
                    x_proposal_digest: str | None = Header(default=None)):
    approver = verify_approver_auth(authorization, x_nexus_approver_token, x_nexus_approver)
    with lock_for(incident_id, timeout=5.0):
        incident = find_incident(incident_id)
        if incident is None:
            raise _not_found()
        if incident["status"] != IncidentStatus.AWAITING_APPROVAL.value:
            raise api_error(409, "conflict", "Only an awaiting approval proposal can be rejected")
        if x_proposal_digest is not None and x_proposal_digest != incident_proposal_digest(incident):
            raise api_error(409, "conflict", "The proposal changed since it was reviewed; reload and review again")
        try:
            add_audit_record(incident_id, "MANUAL_APPROVAL_REJECTED", {"approver": approver,
                "action": incident.get("proposed_action")})
            # Rejection performs no infrastructure action: status and audit only.
            incident["status"] = transition(IncidentStatus.AWAITING_APPROVAL, IncidentStatus.ESCALATED).value
            incident["_rejected_by"] = approver
            persist_incidents()
            from app.orchestration.archive import enqueue_archive
            enqueue_archive(incident)
        except ValueError as exc:
            raise as_http_error(exc) from exc
        return {"incident_id": incident_id, "status": incident["status"], "approved": False}


@router.post("/{incident_id}/archive/retry")
def retry_archive(incident_id: str, authorization: str | None = Header(default=None),
                  x_nexus_approver_token: str | None = Header(default=None),
                  x_nexus_approver: str | None = Header(default="operator")):
    approver = verify_approver_auth(authorization, x_nexus_approver_token, x_nexus_approver)
    with lock_for(incident_id, timeout=5.0):
        incident = find_incident(incident_id)
        if incident is None:
            raise _not_found()
        from app.orchestration.archive import enqueue_archive
        enqueue_archive(incident)
        job = incident.get("_archive_outbox")
        if job is None:
            raise api_error(409, "conflict", incident.get("_archive_blocked_reason", "Incident has no terminal archive outcome"))
        if job["status"] != "DELIVERED":
            job.update(status="PENDING", next_attempt_at=datetime.now(timezone.utc).isoformat(), last_error=None)
            persist_incidents()
            add_audit_record(incident_id, "MEMORY_ARCHIVE_RETRY_REQUESTED", {"approver": approver, "job_id": job["job_id"]})
        return {"incident_id": incident_id, "archive_status": job["status"], "attempts": job["attempts"]}


class ReconciliationRequest(BaseModel):
    outcome: Literal["CONFIRMED", "FAILED"]
    evidence: str = Field(min_length=10, max_length=2000,
                          description="What the operator observed (e.g. rollout history, live pod versions)")
    note: str = Field(default="", max_length=2000)

    @field_validator("evidence")
    @classmethod
    def meaningful(cls, value):
        if not value.strip():
            raise ValueError("Reconciliation evidence must describe what was observed")
        return value.strip()


@router.post("/{incident_id}/reconcile", response_model=IncidentView)
def reconcile_incident(
    incident_id: str,
    request: ReconciliationRequest,
    authorization: str | None = Header(default=None),
    x_nexus_approver_token: str | None = Header(default=None),
    x_nexus_approver: str | None = Header(default=None),
):
    verify_approver_auth(authorization, x_nexus_approver_token, x_nexus_approver or "operator")
    # A personal approver token names the operator itself; a shared token must be told who observed.
    operator = (named_approver_identity(authorization, x_nexus_approver_token)
                or require_explicit_identity(x_nexus_approver))
    if find_incident(incident_id) is None:
        raise _not_found()
    try:
        incident = reconcile_execution(incident_id, request.outcome, operator, request.evidence, request.note)
    except ValueError as exc:
        raise as_http_error(exc) from exc
    return public_incident(incident)
