from datetime import datetime, timezone
from threading import RLock
from fastapi import APIRouter, HTTPException, Query
from app.api.incidents import find_incident, lock_for
from app.contracts import AnomalyEvent

router = APIRouter(prefix="/internal/anomalies", tags=["anomalies"])
anomalies = {}
_anomaly_lock = RLock()

@router.post("", response_model=AnomalyEvent)
def ingest_anomaly(event: AnomalyEvent, incident_id: str = Query(min_length=1)):
    with lock_for(incident_id):
        incident = find_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        if event.service not in incident["affected_services"]:
            raise HTTPException(status_code=409, detail="Anomaly service is not affected by this incident")
        if event.timestamp > datetime.now(timezone.utc):
            raise HTTPException(status_code=422, detail="Anomaly cannot be in the future")
        payload = event.model_dump(mode="json")
        with _anomaly_lock:
            previous = anomalies.get(event.anomaly_id)
            if previous is not None and previous != payload:
                raise HTTPException(status_code=409, detail="Anomaly ID already contains different evidence")
            anomalies[event.anomaly_id] = payload
        if event.anomaly_id not in incident["anomaly_ids"]:
            incident["anomaly_ids"].append(event.anomaly_id)
        return event

@router.get("", response_model=AnomalyEvent)
def get_anomaly(incident_id: str = Query(min_length=1)):
    incident = find_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    # M3 calls this route while M4 waits for RCA under the incident lock.
    # Stored events are immutable; copy linked IDs and read under a separate short lock.
    identifiers = tuple(incident["anomaly_ids"])
    services = tuple(incident["affected_services"])
    with _anomaly_lock:
        events = [dict(anomalies[identifier]) for identifier in identifiers
                  if identifier in anomalies and anomalies[identifier]["service"] in services]
    if not events:
        raise HTTPException(status_code=404, detail="No actual anomaly evidence is linked to this incident")
    return max(events, key=lambda event: datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")))
