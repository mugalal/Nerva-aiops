from datetime import datetime, timezone
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock
from fastapi import APIRouter, HTTPException, Header
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from app.contracts import IncidentView
from app.state_machine.states import IncidentStatus
from app.state_machine.machine import transition
from app.db import workflow_store
from app.api.auth import verify_ingest_auth

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
incidents = []
_workflow_lock = RLock()
_committed_incidents = []


def mark_persisted():
    """Publish one immutable checkpoint after the storage transaction succeeds."""
    global _committed_incidents
    _committed_incidents = deepcopy(incidents)


def restore_persisted():
    # Preserve incident references held by callers while discarding unsaved fields.
    existing = {item["incident_id"]: item for item in incidents}
    restored = []
    for saved in _committed_incidents:
        item = existing.get(saved["incident_id"], {})
        item.clear()
        item.update(deepcopy(saved))
        restored.append(item)
    incidents[:] = restored

def persist_incidents():
    with _workflow_lock:
        try:
            workflow_store.save_incidents(incidents)
        except workflow_store.StorageError:
            restore_persisted()
            raise
        mark_persisted()


def persist_anomaly(payload, incident_id):
    with _workflow_lock:
        workflow_store.save_anomaly(payload, incident_id, incidents)
        mark_persisted()

class IncidentCreate(BaseModel):
    incident_id: str = Field(min_length=1)
    severity: str
    affected_services: list[str] = Field(min_length=1)
    started_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    anomaly_ids: list[str] = Field(default_factory=list)

    @field_validator("severity")
    @classmethod
    def severity_allowed(cls, value):
        value = value.lower()
        if value not in {"low", "medium", "high", "critical"}:
            raise ValueError("Unsupported incident severity")
        return value

    @field_validator("affected_services", "anomaly_ids")
    @classmethod
    def unique_nonempty_values(cls, value):
        if any(not item.strip() for item in value) or len(value) != len(set(value)):
            raise ValueError("Identifiers must be nonempty and unique")
        return value

    @field_validator("started_at")
    @classmethod
    def start_not_future(cls, value):
        if value > datetime.now(timezone.utc):
            raise ValueError("Incident cannot start in the future")
        return value


@contextmanager
def lock_for(incident_id: str, timeout: float | None = None):
    # A save projects the complete in-process snapshot. Serialize mutation units
    # so another incident cannot publish half of an unfinished workflow change.
    acquired = _workflow_lock.acquire(timeout=timeout if timeout is not None else -1)
    if not acquired:
        from app.providers.errors import IntegrationError
        raise IntegrationError("Another operation is running for this incident; retry shortly",
                               status_code=409, retryable=True)
    try:
        yield
    except workflow_store.StorageError:
        restore_persisted()
        raise
    finally:
        _workflow_lock.release()



@contextmanager
def incident_operation(incident_id: str, wait: float = 5.0):
    with lock_for(incident_id, timeout=wait):
        yield


def find_incident(incident_id: str):
    return next((incident for incident in incidents if incident["incident_id"] == incident_id), None)


def public_incident(incident):
    return IncidentView.model_validate({key: incident[key] for key in IncidentView.model_fields}).model_dump(mode="json")


def update_incident_status(
    incident_id: str,
    next_status: IncidentStatus,
    approved: bool = False,
    recovery_validated: bool = False,
    reconciled: bool = False,
):
    with lock_for(incident_id):
        incident = find_incident(incident_id)
        if incident is None:
            raise ValueError("Incident not found")
        new_status = transition(
            IncidentStatus(incident["status"]),
            next_status,
            approved=approved,
            recovery_validated=recovery_validated,
            reconciled=reconciled,
        )
        incident["status"] = new_status.value
        persist_incidents()
        return incident


@router.post("/", response_model=IncidentView)
def create_incident(
    incident: IncidentCreate,
    authorization: str | None = Header(default=None),
    x_nexus_ingest_token: str | None = Header(default=None),
    x_nexus_sender: str | None = Header(default=None),
):
    verify_ingest_auth(authorization, x_nexus_ingest_token, x_nexus_sender)
    with lock_for(incident.incident_id):
        if find_incident(incident.incident_id) is not None:
            raise HTTPException(status_code=409, detail="Incident already exists")
        incident_data = incident.model_dump(mode="json")
        incident_data["status"] = IncidentStatus.DETECTED.value
        incident_data["detected_at"] = datetime.now(timezone.utc).isoformat()
        incidents.append(incident_data)
        persist_incidents()
        return public_incident(incident_data)


@router.get("/", response_model=list[IncidentView])
def list_incidents():
    # M3 calls these reads while M4 holds the mutation lock awaiting RCA.
    # Read the last committed checkpoint without acquiring that lock.
    return [public_incident(incident) for incident in _committed_incidents]


@router.get("/{incident_id}", response_model=IncidentView)
def get_incident(incident_id: str):
    incident = next((item for item in _committed_incidents if item["incident_id"] == incident_id), None)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return public_incident(incident)


@router.get("/{incident_id}/context")
def get_incident_context(incident_id: str):
    from app.api.anomalies import anomalies
    from app.orchestration.orchestrator import provenance
    from app.remediation.audit import audit_records
    incident = next((item for item in _committed_incidents if item["incident_id"] == incident_id), None)
    if incident is None:
        incident = find_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    linked = [anomalies[key] for key in incident.get("anomaly_ids", []) if key in anomalies]
    recovery = incident.get("_recovery") or incident.get("_last_recovery_measurement") or {}
    modes = incident.get("_provenance") or provenance()
    sources = set(modes.values())
    source = "real" if sources == {"real"} else "mock" if sources == {"mock"} else "mixed"
    return {"incident": public_incident(incident), "anomaly": linked[-1] if linked else None,
        "anomalies": linked, "rca": incident.get("_rca"), "decision": incident.get("_proposal"),
        "action_result": incident.get("_action_result"), "recovery_result": recovery.get("details"),
        "recovery_validation": recovery or None, "finops_context": incident.get("_finops"),
        "deployment_event": (incident.get("_evidence") or {}).get("deployment_event"),
        "evidence": incident.get("_evidence"), "source": source, "provider_modes": modes,
        "workflow": {"action_completed_at": incident.get("action_completed_at"),
            "recovery_deadline_at": incident.get("recovery_deadline_at"),
            "execution_ambiguous": bool(incident.get("_execution_ambiguous")),
            "archive_status": (incident.get("_archive_outbox") or {}).get("status"),
            "archive_error": (incident.get("_archive_outbox") or {}).get("last_error") or incident.get("_archive_blocked_reason")},
        "audit": [record for record in audit_records if record["incident_id"] == incident_id]}

