import csv
import io
import json
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field
from app.api.incidents import find_incident
from app.decision_engine.models import DecisionAction
from app.orchestration.orchestrator import execute_approved_action
from app.remediation.models import RemediationResult
from app.remediation.audit import audit_records, verify_chain
from app.providers.errors import api_error, as_http_error
from app.api.auth import verify_approver_auth

router = APIRouter(prefix="/internal/remediation", tags=["remediation"])

class RemediationRequest(BaseModel):
    incident_id: str
    action: DecisionAction
    approved: bool
    replicas: int | None = Field(default=None, strict=True)

from fastapi import Header

@router.post("/execute", response_model=RemediationResult)
def execute(
    request: RemediationRequest,
    authorization: str | None = Header(default=None),
    x_nexus_approver_token: str | None = Header(default=None),
    x_nexus_approver: str | None = Header(default="operator"),
    x_proposal_digest: str | None = Header(default=None),
):
    approver = verify_approver_auth(authorization, x_nexus_approver_token, x_nexus_approver)
    if find_incident(request.incident_id) is None:
        raise api_error(404, "not_found", "Incident not found")
    try:
        return execute_approved_action(request.incident_id, request.action, request.approved, request.replicas,
                                       approver=approver, digest=x_proposal_digest)
    except ValueError as exc:
        raise as_http_error(exc) from exc

@router.get("/audit")
def get_audit_records():
    return list(audit_records)


@router.get("/audit/verify")
def verify_audit():
    """Recompute the hash chain over the whole trail."""
    return verify_chain()


_CSV_COLUMNS = ("timestamp", "event_id", "incident_id", "event_type", "actor", "action", "details", "prev_hash", "hash")


@router.get("/audit/export")
def export_audit(format: str = Query("json", pattern="^(json|csv)$"), incident_id: str | None = Query(None)):
    """Download the audit trail (optionally one incident's), with the chain verification attached."""
    records = [record for record in list(audit_records) if incident_id is None or record["incident_id"] == incident_id]
    verification = verify_chain()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"nerva-audit-{incident_id or 'all'}-{stamp}".replace("/", "_")
    if format == "json":
        body = json.dumps({"exported_at": datetime.now(timezone.utc).isoformat(), "incident_id": incident_id,
                           "verification": verification, "records": records}, indent=2, default=str)
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_CSV_COLUMNS)
    for record in records:
        details = record.get("details") or {}
        writer.writerow([record.get("timestamp"), record.get("event_id"), record.get("incident_id"),
                         record.get("event_type"),
                         details.get("approver") or details.get("observed_by") or "",
                         details.get("action") or "",
                         json.dumps(details, sort_keys=True, default=str),
                         record.get("prev_hash") or "", record.get("hash") or ""])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"',
                             "X-Audit-Chain-Valid": str(verification["valid"]).lower()})
