"""A retained, idempotent M4 -> M5 outbox; success requires a verified acknowledgement.

1. claim   (incident lock)  count the attempt durably
2. deliver (no lock)        POST the immutable payload with an Idempotency-Key
3. record  (incident lock)  DELIVERED only if M5 echoes exactly what was sent

The network transport is an explicit dependency (`set_transport`), so tests
replace it without changing production behaviour.
"""
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import requests
from app.orchestration.provenance import provider_use, workflow_source
from app.providers.resilient import Breaker, make_session, resilient_call, CircuitOpen

_archive_breaker = Breaker(threshold=5, cooldown=30.0)
_archive_session = make_session(retry_post=True, total_retries=2)


def _default_transport(url, **kwargs):
    return resilient_call(_archive_breaker, _archive_session, "POST", url, **kwargs)


_transport = _default_transport


def set_transport(transport=None):
    """Inject the POST transport (callable(url, json=, headers=, timeout=)); None restores the default."""
    global _transport
    _transport = transport or _default_transport


def memory_token():
    """Service credential for M4 -> M5 archival. Never sent anywhere else."""
    return os.getenv("M5_SHARED_API_TOKEN") or os.getenv("NEXUS_APPROVAL_TOKEN")


def _reconciled(incident):
    return incident.get("_reconciliation") or {}


def archive_payload(incident):
    from app.api.incidents import public_incident
    from app.api.anomalies import anomalies
    from app.contracts import IncidentView
    if any(key not in incident for key in IncidentView.model_fields):
        raise ValueError("Incident contract fields are missing; archive cannot invent them")
    if incident.get("_execution_ambiguous"):
        raise ValueError("Ambiguous execution must be reconciled before outcome archival")
    source = workflow_source(incident)
    used = provider_use(incident)
    real = source == "real" and bool(used)
    recovery = incident.get("_recovery") or {}
    details = recovery.get("details")
    action = incident.get("_action_result")
    reconciliation = _reconciled(incident)
    applied = (action is not None and action["status"] == "SUCCESS") or reconciliation.get("outcome") == "CONFIRMED"
    resolved = incident["status"] == "RESOLVED"
    if resolved and real and (not details or recovery.get("source") != "m1" or not recovery.get("success") or not applied):
        raise ValueError("Real resolved memory requires an applied action and measured M1 SLO recovery")
    rca = incident.get("_rca")
    root_cause = (rca or {}).get("root_cause") or incident.get("root_cause")
    proposed = incident.get("_proposal") or {}
    chosen_action = ((action or {}).get("action") or reconciliation.get("action")
                     or proposed.get("recommended_action") or incident.get("proposed_action"))
    if not root_cause or not chosen_action:
        raise ValueError("Missing diagnosis or decision evidence; no placeholder memory will be fabricated")
    linked = [anomalies[key] for key in incident["anomaly_ids"] if key in anomalies]
    context = {"incident": public_incident(incident), "anomaly": linked[-1] if linked else None,
        "rca": rca, "decision": incident.get("_proposal"), "action_result": action,
        "recovery_result": details, "finops_context": incident.get("_finops"),
        "deployment_event": (incident.get("_evidence") or {}).get("deployment_event")}
    # The immutable memory contract has real/mock namespaces. Any mock input or
    # effect keeps the record out of real history, tagged with its provenance.
    tags = []
    if source == "mixed":
        tags.append("source:mixed")
    tags.extend(f"used:{role}:{mode}" for role, modes in sorted(used.items()) for mode in modes)
    if reconciliation:
        tags.append("action_outcome:operator_observed_" + reconciliation["outcome"].lower())
    if incident.get("_rejected_by"):
        tags.append("decision:rejected_by_operator")
    return {"memory": {"incident_id": incident["incident_id"], "incident_type": root_cause,
        "service": incident["affected_services"][0], "root_cause": root_cause, "action": chosen_action,
        "action_success": bool(applied),
        "recovered": details["recovered"] if details else bool(recovery.get("success", False)), "tags": tags},
        "context": {key: value for key, value in context.items() if value is not None},
        "source": "real" if real else "mock", "resolved": resolved}


def enqueue_archive(incident):
    from app.api.incidents import persist_incidents
    if incident["status"] not in {"RESOLVED", "ESCALATED"} or incident.get("_archive_outbox"):
        return
    if incident.get("_kind") == "rightsizing" and not incident.get("_action_result"):
        return  # A withdrawn or rejected scale-down changed nothing: nothing to learn from.
    try:
        payload = archive_payload(incident)
    except ValueError as exc:
        if incident.get("_archive_blocked_reason") != str(exc):
            incident["_archive_blocked_reason"] = str(exc)
            persist_incidents()
        return
    incident.pop("_archive_blocked_reason", None)
    incident["_archive_outbox"] = {"job_id": "memory:" + incident["incident_id"] + ":" + incident["status"],
        "status": "PENDING", "attempts": 0, "next_attempt_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload, "last_error": None, "delivered_at": None}
    persist_incidents()


def acknowledgement_matches(body, payload):
    """M5 must return the exact immutable record M4 sent (it may only ADD context)."""
    record = body.get("record") if isinstance(body, dict) else None
    if not isinstance(record, dict):
        return False
    if (record.get("memory") != payload["memory"] or record.get("source") != payload["source"]
            or record.get("resolved") is not payload["resolved"]):
        return False
    stored_context = record.get("context") or {}
    return all(stored_context.get(key) == value for key, value in payload["context"].items())


def deliver_archive(incident_id, now=None):
    from app.api.incidents import find_incident, lock_for, persist_incidents
    now = now or datetime.now(timezone.utc)

    with lock_for(incident_id):
        incident = find_incident(incident_id)
        job = (incident or {}).get("_archive_outbox")
        if not job or job["status"] != "PENDING" or datetime.fromisoformat(job["next_attempt_at"]) > now:
            return False
        job["attempts"] += 1
        persist_incidents()
        payload = deepcopy(job["payload"])
        job_id = job["job_id"]

    headers = {"Idempotency-Key": job_id}
    token = memory_token()
    if token:
        headers["Authorization"] = "Bearer " + token
    last_error, is_delivered, is_blocked = None, False, False
    try:
        url = os.getenv("M5_MEMORY_BASE_URL", "http://localhost:8005").rstrip("/") + "/internal/memory/store"
        response = _transport(url, json=payload, headers=headers, timeout=5)
        if response.status_code not in {200, 201}:
            last_error = "M5 returned HTTP " + str(response.status_code)
            if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
                is_blocked = True
        elif acknowledgement_matches(response.json(), payload):
            is_delivered = True
        else:
            last_error = "M5 acknowledgement did not match the delivered record"
    except (requests.RequestException, ValueError, CircuitOpen) as exc:
        last_error = type(exc).__name__

    with lock_for(incident_id):
        incident = find_incident(incident_id)
        job = (incident or {}).get("_archive_outbox")
        if not job or job["job_id"] != job_id or job["status"] != "PENDING":
            return False
        if is_delivered:
            job.update(status="DELIVERED", last_error=None, delivered_at=datetime.now(timezone.utc).isoformat())
        else:
            if is_blocked:
                job["status"] = "BLOCKED"
            job["last_error"] = last_error
            if job["status"] == "PENDING":
                job["next_attempt_at"] = (now + timedelta(seconds=min(300, 2 ** min(job["attempts"], 8)))).isoformat()
        persist_incidents()
        return job["status"] == "DELIVERED"
