from datetime import datetime, timezone
from threading import RLock
from fastapi import APIRouter, HTTPException
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from app.contracts import IncidentView
from app.state_machine.states import IncidentStatus
from app.state_machine.machine import transition

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
incidents = []
_locks = {}
_lock_guard = RLock()

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


def lock_for(incident_id):
    with _lock_guard:
        return _locks.setdefault(incident_id, RLock())


def find_incident(incident_id: str):
    return next((incident for incident in incidents if incident["incident_id"] == incident_id), None)


def public_incident(incident):
    return IncidentView.model_validate({key: incident[key] for key in IncidentView.model_fields}).model_dump(mode="json")


def update_incident_status(incident_id: str, next_status: IncidentStatus, approved: bool = False, recovery_validated: bool = False):
    with lock_for(incident_id):
        incident = find_incident(incident_id)
        if incident is None:
            raise ValueError("Incident not found")
        new_status = transition(IncidentStatus(incident["status"]), next_status,
                                approved=approved, recovery_validated=recovery_validated)
        incident["status"] = new_status.value
        return incident


@router.post("/", response_model=IncidentView)
def create_incident(incident: IncidentCreate):
    with lock_for(incident.incident_id):
        if find_incident(incident.incident_id) is not None:
            raise HTTPException(status_code=409, detail="Incident already exists")
        incident_data = incident.model_dump(mode="json")
        incident_data["status"] = IncidentStatus.DETECTED.value
        incidents.append(incident_data)
        return public_incident(incident_data)


@router.get("/", response_model=list[IncidentView])
def list_incidents():
    return [public_incident(incident) for incident in incidents]


@router.get("/{incident_id}", response_model=IncidentView)
def get_incident(incident_id: str):
    incident = find_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return public_incident(incident)
