import math
import os
from datetime import datetime, timedelta, timezone
from threading import RLock
from fastapi import APIRouter, Query, Header
from app.api.incidents import (TERMINAL_STATUSES, committed_incident, find_incident, lock_for, persist_anomaly,
                               resolve_for_ingest)
from app.contracts import AnomalyEvent
from app.api.auth import verify_ingest_auth
from app.providers.errors import api_error

router = APIRouter(prefix="/internal/anomalies", tags=["anomalies"])
anomalies = {}
anomaly_owners = {}
_anomaly_lock = RLock()


def diagnosis_delay_seconds():
    """Settle delay before the first automatic diagnosis; 0 disables it."""
    raw = os.getenv("M4_AUTO_BUILD_DELAY_SECONDS", "0")
    try:
        delay = float(raw)
    except ValueError as exc:
        raise ValueError("M4_AUTO_BUILD_DELAY_SECONDS must be a number in [0, 600]") from exc
    if not math.isfinite(delay) or not 0 <= delay <= 600:
        raise ValueError("M4_AUTO_BUILD_DELAY_SECONDS must be a number in [0, 600]")
    return delay


@router.post("", response_model=AnomalyEvent)
def ingest_anomaly(
    event: AnomalyEvent,
    incident_id: str = Query(min_length=1),
    authorization: str | None = Header(default=None),
    x_nexus_ingest_token: str | None = Header(default=None),
    x_nexus_sender: str | None = Header(default=None),
):
    verify_ingest_auth(authorization, x_nexus_ingest_token, x_nexus_sender)
    with lock_for(incident_id, timeout=5.0):
        target_id = resolve_for_ingest(incident_id, min(event.timestamp, datetime.now(timezone.utc)).isoformat())
        if target_id is None:
            raise api_error(404, "not_found", "Incident not found")
        if target_id == incident_id:
            return _link(incident_id, event)
        # A coalesced alias: the anomaly is evidence for the incident it joined.
        with lock_for(target_id, timeout=5.0):
            return _link(target_id, event)


def _link(incident_id, event):
    """Link one anomaly to one incident; the caller holds that incident's lock."""
    incident = find_incident(incident_id)
    if incident is None:
        raise api_error(404, "not_found", "Incident not found")
    if event.service not in incident["affected_services"]:
        raise api_error(409, "conflict", "Anomaly service is not affected by this incident")
    if event.timestamp > datetime.now(timezone.utc):
        raise api_error(422, "invalid_request", "Anomaly cannot be in the future")
    payload = event.model_dump(mode="json")
    with _anomaly_lock:
        previous = anomalies.get(event.anomaly_id)
        if previous is not None and previous != payload:
            raise api_error(409, "conflict", "Anomaly ID already contains different evidence")
        if anomaly_owners.get(event.anomaly_id, incident_id) != incident_id:
            raise api_error(409, "conflict", "Anomaly already belongs to another incident")
    is_first_anomaly = len(incident["anomaly_ids"]) == 0
    if event.anomaly_id not in incident["anomaly_ids"]:
        incident["anomaly_ids"].append(event.anomaly_id)
        if incident.get("status") in TERMINAL_STATUSES:
            # Alerting again inside the cooldown: recorded, not acted on again.
            incident["_recurrences"] = int(incident.get("_recurrences") or 0) + 1
    if (is_first_anomaly or "_proposal" not in incident) and incident.get("status") in {"DETECTED", "CORRELATING", "DIAGNOSING"}:
        incident["_diagnosis_pending"] = True
        delay = diagnosis_delay_seconds()
        if delay > 0 and "_diagnosis_not_before" not in incident:
            incident["_diagnosis_not_before"] = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
    persist_anomaly(payload, incident_id)
    with _anomaly_lock:
        anomalies[event.anomaly_id] = payload
        anomaly_owners[event.anomaly_id] = incident_id
    return event


@router.get("", response_model=AnomalyEvent)
def get_anomaly(incident_id: str = Query(min_length=1)):
    incident = committed_incident(incident_id)
    if incident is None:
        raise api_error(404, "not_found", "Incident not found")
    # M3 calls this while M4 diagnoses; read the committed incident, never a lock.
    identifiers = tuple(incident["anomaly_ids"])
    services = tuple(incident["affected_services"])
    with _anomaly_lock:
        events = [dict(anomalies[identifier]) for identifier in identifiers
                  if identifier in anomalies and anomalies[identifier]["service"] in services]
    if not events:
        raise api_error(404, "not_found", "No actual anomaly evidence is linked to this incident")
    return max(events, key=lambda event: datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")))
