"""Register observed experiments without fabricating fault injection timing."""
from datetime import datetime, timezone
from fastapi import APIRouter, Header, HTTPException
from pydantic import AwareDatetime, BaseModel, Field
from typing import Literal
from app.api.auth import verify_approver_auth
from app.api.incidents import find_incident, lock_for, persist_incidents
from app.remediation.audit import add_audit_record

router = APIRouter(prefix="/api/incidents", tags=["experiments"])


class ExperimentRegistration(BaseModel):
    run_id: str = Field(min_length=1, max_length=120)
    scenario: Literal["bad_deployment", "traffic_spike"]
    injection_time: AwareDatetime | None = None
    injection_evidence: str | None = Field(default=None, min_length=1)


@router.post("/{incident_id}/experiment")
def register_experiment(incident_id: str, request: ExperimentRegistration,
                        authorization: str | None = Header(default=None),
                        x_nexus_approver_token: str | None = Header(default=None),
                        x_nexus_approver: str | None = Header(default="operator")):
    approver = verify_approver_auth(authorization, x_nexus_approver_token, x_nexus_approver)
    with lock_for(incident_id):
        incident = find_incident(incident_id)
        if incident is None:
            raise HTTPException(404, "Incident not found")
        if request.injection_time is not None:
            if not request.injection_evidence:
                raise HTTPException(422, "Injection time requires its observed evidence reference")
            detected = datetime.fromisoformat(incident["detected_at"].replace("Z", "+00:00"))
            if request.injection_time > min(detected, datetime.now(timezone.utc)):
                raise HTTPException(422, "Injection time must not be after detection or in the future")
        value = request.model_dump(mode="json")
        previous = incident.get("_experiment")
        if previous is not None:
            if {key: previous.get(key) for key in value} != value:
                raise HTTPException(409, "Experiment already registered with different observations")
            return previous
        from app.api.incidents import incidents
        if any((item.get("_experiment") or {}).get("run_id") == request.run_id for item in incidents):
            raise HTTPException(409, "Run ID already belongs to another incident")
        expected = "faulty_deployment" if request.scenario == "bad_deployment" else "traffic_spike"
        root_cause = (incident.get("_rca") or {}).get("root_cause")
        value.update(registered_by=approver, expected_root_cause=expected,
                     rca_correct=root_cause == expected if root_cause else None)
        incident["_experiment"] = value
        persist_incidents()
        add_audit_record(incident_id, "EXPERIMENT_REGISTERED", value)
        return value
