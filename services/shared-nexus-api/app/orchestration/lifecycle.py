import logging
import os
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from app.db import workflow_store

logger = logging.getLogger(__name__)


def restore_state():
    from app.api.incidents import incidents, persist_incidents, mark_persisted
    from app.api.anomalies import anomalies, anomaly_owners
    from app.remediation.audit import audit_records, add_audit_record
    from app.orchestration.orchestrator import clear_pending_executions
    clear_pending_executions()
    incidents[:] = workflow_store.load("incidents")
    mark_persisted()
    anomalies.clear()
    anomalies.update({item["anomaly_id"]: item for item in workflow_store.load("anomalies")})
    anomaly_owners.clear()
    anomaly_owners.update(workflow_store.load_anomaly_owners())
    audit_records[:] = workflow_store.load("audit")
    for incident in incidents:
        if incident["status"] == "EXECUTING" and not incident.get("_execution_result"):
            # The process may have died after Kubernetes accepted the request.
            # A restart must never blindly execute this intent a second time.
            incident["status"] = "ESCALATED"
            incident["_execution_ambiguous"] = True
            incident["_execution_reconciliation_reason"] = "Process restarted before a durable action result"
            persist_incidents()
            add_audit_record(incident["incident_id"], "ACTION_OUTCOME_UNKNOWN", {"intent": incident.get("_execution_intent")})


def work_once():
    from app.api.incidents import incidents, lock_for, persist_incidents
    from app.orchestration.orchestrator import build_decision, validate_and_apply_recovery, retry_pending_executions
    from app.orchestration.archive import enqueue_archive, deliver_archive
    from app.providers.errors import IntegrationError
    now = datetime.now(timezone.utc)
    retry_pending_executions()
    for incident in list(incidents):
        identity = incident["incident_id"]
        with lock_for(identity):
            if incident["status"] in {"RESOLVED", "ESCALATED"}:
                enqueue_archive(incident)
            retry = incident.get("_diagnosis_retry_at")
            not_before = incident.get("_diagnosis_not_before")
            if (os.getenv("M4_AUTO_BUILD_DECISIONS", "true").lower() == "true" and incident.get("_diagnosis_pending")
                    and incident["status"] in {"DETECTED", "CORRELATING", "DIAGNOSING", "DIAGNOSED"}
                    and (not not_before or datetime.fromisoformat(not_before) <= now)
                    and (not retry or datetime.fromisoformat(retry) <= now)):
                try:
                    build_decision(identity)
                except (IntegrationError, ValueError) as exc:
                    incident["_diagnosis_last_error"] = str(exc)
                    incident["_diagnosis_retry_at"] = (now + timedelta(seconds=15)).isoformat()
                    persist_incidents()
            auto_recovery = os.getenv("M4_AUTO_VALIDATE_RECOVERY", "true").strip().lower() in {"1", "true", "yes", "on"}
            if auto_recovery and incident["status"] == "VALIDATING":
                retry = incident.get("_recovery_poll_at")
                if not retry or datetime.fromisoformat(retry) <= now:
                    try:
                        validate_and_apply_recovery(identity)
                    except (IntegrationError, ValueError) as exc:
                        incident["_recovery_last_error"] = str(exc)
                    interval = float(os.getenv("M4_RECOVERY_POLL_INTERVAL_SECONDS", "5"))
                    incident["_recovery_poll_at"] = (now + timedelta(seconds=interval)).isoformat()
                    persist_incidents()
        deliver_archive(identity, now=now)


class Worker:
    def __init__(self, lease=None):
        self.lease = lease
        self.stop = Event()
        self.thread = Thread(target=self.run, name="m4-lifecycle", daemon=True)

    def run(self):
        try:
            while not self.stop.is_set():
                try:
                    work_once()
                except Exception:
                    logger.exception("M4 lifecycle worker failed; durable pending work is retained")
                self.stop.wait(1)
        finally:
            workflow_store.close_lease(self.lease)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=6)
