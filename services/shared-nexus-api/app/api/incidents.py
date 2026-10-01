from fastapi import APIRouter
from pydantic import BaseModel

from app.state_machine.states import IncidentStatus
from app.state_machine.machine import transition


router = APIRouter(
    prefix="/api/incidents",
    tags=["incidents"]
)


incidents = []


class IncidentCreate(BaseModel):
    incident_id: str
    severity: str


def find_incident(incident_id: str):
    for incident in incidents:
        if incident["incident_id"] == incident_id:
            return incident

    return None

def update_incident_status(
    incident_id: str,
    next_status: IncidentStatus,
    approved: bool = False,
    recovery_validated: bool = False
):
    incident = find_incident(incident_id)

    if incident is None:
        return None

    new_status = transition(
        IncidentStatus(incident["status"]),
        next_status,
        approved=approved,
        recovery_validated=recovery_validated
    )

    incident["status"] = new_status.value

    return incident

@router.post("/")
def create_incident(incident: IncidentCreate):
    incident_data = incident.model_dump()

    incident_data["status"] = IncidentStatus.DETECTED.value

    incidents.append(incident_data)

    return incident_data


@router.get("/")
def list_incidents():
    return incidents


@router.get("/{incident_id}")
def get_incident(incident_id: str):
    incident = find_incident(incident_id)

    if incident is None:
        return {
            "message": "Incident not found"
        }

    return incident