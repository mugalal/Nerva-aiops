"""
A stand-in for the team's shared API, enforcing the same rules as its real
incident and anomaly routes (services/shared-nexus-api/app/api/incidents.py and
anomalies.py on the integration branch):

  POST /api/incidents/                      create (409 if it exists)
       severity must be low / medium / high / critical (case-insensitive)
       affected_services non-empty and unique; anomaly_ids unique
       started_at must carry a timezone and not be in the future
  POST /internal/anomalies?incident_id=...  link an anomaly
       404 unknown incident; 409 service not affected; 422 timestamp in the
       future; 409 if the same id arrives with different evidence
  GET  /internal/anomalies?incident_id=...  the LATEST linked anomaly

Extras for testing: every request is logged in `state.log`; `state.fail_next`
makes the next N POSTs answer 503; `state.clock_offset_s` shifts this server's
idea of "now" (to test clock differences).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import Body, FastAPI, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, field_validator


class IncidentCreate(BaseModel):
    incident_id: str = Field(min_length=1)
    severity: str
    affected_services: list[str] = Field(min_length=1)
    started_at: AwareDatetime
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


class AnomalyEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    anomaly_id: str = Field(min_length=1)
    timestamp: AwareDatetime
    service: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    severity: str
    model: str = Field(min_length=1)
    features: dict[str, float]


def make_fake_shared_api():
    state = SimpleNamespace(incidents=[], anomalies={}, log=[], fail_next=0, clock_offset_s=0.0)
    app = FastAPI()

    def now():
        return datetime.now(timezone.utc) + timedelta(seconds=state.clock_offset_s)

    def find(incident_id):
        return next((i for i in state.incidents if i["incident_id"] == incident_id), None)

    def entered(method, path, query=None, body=None):
        state.log.append({"method": method, "path": path, "query": query, "body": body})
        if method == "POST" and state.fail_next > 0:
            state.fail_next -= 1
            raise HTTPException(status_code=503, detail="service unavailable")

    @app.post("/api/incidents/")
    def create_incident(payload: dict = Body(...)):
        entered("POST", "/api/incidents/", body=payload)
        try:
            incident = IncidentCreate.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=[{"msg": e["msg"]} for e in exc.errors()])
        if incident.started_at > now():
            raise HTTPException(status_code=422, detail="Incident cannot start in the future")
        if find(incident.incident_id) is not None:
            raise HTTPException(status_code=409, detail="Incident already exists")
        data = incident.model_dump(mode="json")
        data["status"] = "DETECTED"
        state.incidents.append(data)
        return data

    @app.post("/internal/anomalies")
    def ingest(payload: dict = Body(...), incident_id: str = Query(min_length=1)):
        entered("POST", "/internal/anomalies", query=incident_id, body=payload)
        try:
            event = AnomalyEvent.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=[{"msg": e["msg"]} for e in exc.errors()])
        incident = find(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        if event.service not in incident["affected_services"]:
            raise HTTPException(status_code=409, detail="Anomaly service is not affected by this incident")
        if event.timestamp > now():
            raise HTTPException(status_code=422, detail="Anomaly cannot be in the future")
        stored = event.model_dump(mode="json")
        previous = state.anomalies.get(event.anomaly_id)
        if previous is not None and previous != stored:
            raise HTTPException(status_code=409, detail="Anomaly ID already contains different evidence")
        state.anomalies[event.anomaly_id] = stored
        if event.anomaly_id not in incident["anomaly_ids"]:
            incident["anomaly_ids"].append(event.anomaly_id)
        return stored

    @app.get("/internal/anomalies")
    def latest(incident_id: str = Query(min_length=1)):
        incident = find(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        events = [state.anomalies[i] for i in incident["anomaly_ids"] if i in state.anomalies]
        if not events:
            raise HTTPException(status_code=404, detail="No actual anomaly evidence is linked to this incident")
        return max(events, key=lambda e: datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")))

    return app, state
