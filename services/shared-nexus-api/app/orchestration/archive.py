"""A retained, idempotent outbox; network success is explicitly acknowledged."""
import os
from datetime import datetime, timedelta, timezone
import requests


def archive_payload(incident):
    from app.api.incidents import public_incident
    from app.api.anomalies import anomalies
    from app.contracts import IncidentView
    if any(key not in incident for key in IncidentView.model_fields):
        raise ValueError("Incident contract fields are missing; archive cannot invent them")
    modes = incident.get("_provenance") or {}
    real = bool(modes) and all(value == "real" for value in modes.values())
    recovery = incident.get("_recovery") or {}
    details = recovery.get("details")
    action = incident.get("_action_result")
    resolved = incident["status"] == "RESOLVED"
    if resolved and real and (not details or recovery.get("source") != "m1" or
                             not recovery.get("success") or not action or action["status"] != "SUCCESS"):
        raise ValueError("Real resolved memory requires a successful action and measured M1 SLO recovery")
    if incident.get("_execution_ambiguous"):
        raise ValueError("Ambiguous execution must be reconciled before outcome archival")
    rca = incident.get("_rca")
    root_cause = (rca or {}).get("root_cause") or incident.get("root_cause")
    proposed = incident.get("_proposal") or {}
    chosen_action = (action or {}).get("action") or proposed.get("recommended_action") or incident.get("proposed_action")
    if not root_cause or not chosen_action:
        raise ValueError("Missing diagnosis or decision evidence; no placeholder memory will be fabricated")
    linked = [anomalies[key] for key in incident["anomaly_ids"] if key in anomalies]
    context = {"incident": public_incident(incident), "anomaly": linked[-1] if linked else None,
        "rca": rca, "decision": incident.get("_proposal"), "action_result": action,
        "recovery_result": details, "finops_context": incident.get("_finops"),
        "deployment_event": (incident.get("_evidence") or {}).get("deployment_event")}
    # The immutable memory contract has real/mock namespaces. Mixed executions
    # stay in mock, with an explicit mixed provenance tag; never in real history.
    tags = ["source:mixed"] if modes and len(set(modes.values())) > 1 else []
    return {"memory": {"incident_id": incident["incident_id"], "incident_type": root_cause,
        "service": incident["affected_services"][0], "root_cause": root_cause, "action": chosen_action,
        "action_success": bool(action and action["status"] == "SUCCESS"),
        "recovered": details["recovered"] if details else bool(recovery.get("success", False)), "tags": tags},
        "context": {key: value for key, value in context.items() if value is not None},
        "source": "real" if real else "mock", "resolved": resolved}


def enqueue_archive(incident):
    from app.api.incidents import persist_incidents
    if incident["status"] not in {"RESOLVED", "ESCALATED"} or incident.get("_archive_outbox"):
        return
    try:
        payload = archive_payload(incident)
    except ValueError as exc:
        incident["_archive_blocked_reason"] = str(exc)
        persist_incidents()
        return
    incident.pop("_archive_blocked_reason", None)
    incident["_archive_outbox"] = {"job_id": "memory:" + incident["incident_id"] + ":" + incident["status"],
        "status": "PENDING", "attempts": 0, "next_attempt_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload, "last_error": None, "delivered_at": None}
    persist_incidents()


def deliver_archive(incident_id, now=None):
    from app.api.incidents import find_incident, lock_for, persist_incidents
    now = now or datetime.now(timezone.utc)
    with lock_for(incident_id):
        incident = find_incident(incident_id)
        job = (incident or {}).get("_archive_outbox")
        if not job or job["status"] != "PENDING" or datetime.fromisoformat(job["next_attempt_at"]) > now:
            return False
        job["attempts"] += 1
        # Persist the attempt before delivery. An unknown response can be retried
        # with exactly the same payload; M5's incident/source key is idempotent.
        persist_incidents()
        headers = {"Idempotency-Key": job["job_id"]}
        token = os.getenv("M5_SHARED_API_TOKEN") or os.getenv("NEXUS_APPROVAL_TOKEN")
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            response = requests.post(os.getenv("M5_MEMORY_BASE_URL", "http://localhost:8005").rstrip("/") +
                                     "/internal/memory/store", json=job["payload"], headers=headers, timeout=5)
            if response.status_code not in {200, 201}:
                job["last_error"] = "M5 returned HTTP " + str(response.status_code)
                if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
                    job["status"] = "BLOCKED"
            else:
                body = response.json()
                record = body.get("record") if isinstance(body, dict) else None
                if (not isinstance(record, dict) or record.get("memory") != job["payload"]["memory"]
                        or record.get("source") != job["payload"]["source"]
                        or record.get("resolved") is not job["payload"]["resolved"]):
                    raise ValueError("M5 acknowledgement did not match the delivered outcome and provenance")
                job.update(status="DELIVERED", last_error=None, delivered_at=datetime.now(timezone.utc).isoformat())
        except (requests.RequestException, ValueError) as exc:
            job["last_error"] = type(exc).__name__
        if job["status"] == "PENDING":
            job["next_attempt_at"] = (now + timedelta(seconds=min(300, 2 ** min(job["attempts"], 8)))).isoformat()
        persist_incidents()
        return job["status"] == "DELIVERED"
