import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from app.db import workflow_store

logger = logging.getLogger(__name__)


def restore_state():
    from app.api.incidents import incidents, persist_incidents, mark_persisted
    from app.api.anomalies import anomalies, anomaly_owners
    from app.remediation.audit import audit_records, add_audit_record
    from app.orchestration.orchestrator import clear_pending_executions
    from app.state_machine.machine import transition
    from app.state_machine.states import IncidentStatus
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
            incident["status"] = transition(IncidentStatus.EXECUTING, IncidentStatus.ESCALATED, outcome_unknown=True).value
            incident["_execution_ambiguous"] = True
            incident["_execution_reconciliation_reason"] = "Process restarted before a durable action result"
            persist_incidents()
            add_audit_record(incident["incident_id"], "ACTION_OUTCOME_UNKNOWN", {"intent": incident.get("_execution_intent")})
        attempt = incident.get("_diagnosis_attempt")
        if attempt is not None:
            # Diagnosis reads only; an interrupted attempt is safely superseded.
            incident.pop("_diagnosis_attempt")
            if incident["status"] in {"DETECTED", "CORRELATING", "DIAGNOSING", "DIAGNOSED"}:
                incident["_diagnosis_pending"] = True
            persist_incidents()
            add_audit_record(incident["incident_id"], "DIAGNOSIS_INTERRUPTED", {"attempt": attempt})


def _setting(name, default, low, high):
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number in [{low}, {high}]") from exc
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a number in [{low}, {high}]")
    return value


def validate_settings():
    """Fail startup on malformed worker settings instead of failing every request."""
    from app.api.anomalies import diagnosis_delay_seconds
    diagnosis_delay_seconds()
    _setting("M4_RECOVERY_POLL_INTERVAL_SECONDS", 5, 1, 300)
    _setting("M4_DIAGNOSIS_RETRY_SECONDS", 15, 1, 600)
    _setting("M4_DIAGNOSIS_MAX_ATTEMPTS", 20, 1, 1000)
    _setting("M4_WORKER_CONCURRENCY", 4, 1, 32)
    _setting("M4_LEASE_CHECK_SECONDS", 5, 1, 60)


def _enabled(name, default="true"):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _due(value, now):
    if not value:
        return True
    try:
        return datetime.fromisoformat(value) <= now
    except (TypeError, ValueError):
        return True


def _diagnose(identity):
    from app.api.incidents import find_incident, lock_for, persist_incidents
    from app.orchestration.orchestrator import build_decision
    from app.providers.errors import IntegrationError

    try:
        build_decision(identity)
    except (IntegrationError, ValueError) as exc:
        retryable = getattr(exc, "retryable", False)
        retry = _setting("M4_DIAGNOSIS_RETRY_SECONDS", 15, 1, 600)

        with lock_for(identity):
            incident = find_incident(identity)

            if incident is not None and incident.get("_diagnosis_pending"):
                incident["_diagnosis_last_error"] = str(exc)
                incident.pop("_diagnosis_attempt", None)
                failures = int(incident.get("_diagnosis_failures") or 0) + 1
                incident["_diagnosis_failures"] = failures

                if retryable and failures < int(_setting("M4_DIAGNOSIS_MAX_ATTEMPTS", 20, 1, 1000)):
                    incident["_diagnosis_retry_at"] = (
                        datetime.now(timezone.utc)
                        + timedelta(seconds=retry)
                    ).isoformat()
                    persist_incidents()
                else:
                    _escalate_failed_diagnosis(incident, str(exc), failures, retryable)


def _escalate_failed_diagnosis(incident, error, failures, retryable):
    """A diagnosis that cannot succeed hands the incident to a human.

    Without this an incident stayed in DIAGNOSING with nothing scheduled to
    move it: not resolved, not escalated, invisible to the escalation queue.
    """
    from app.api.incidents import persist_incidents
    from app.orchestration.archive import enqueue_archive
    from app.remediation.audit import add_audit_record
    from app.state_machine.machine import transition
    from app.state_machine.states import IncidentStatus
    incident["_diagnosis_pending"] = False
    incident.pop("_diagnosis_retry_at", None)
    incident["_diagnosis_failed"] = True
    if incident["status"] in {"DETECTED", "CORRELATING", "DIAGNOSING", "DIAGNOSED"}:
        incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.ESCALATED,
                                        diagnosis_failed=True).value
    persist_incidents()
    add_audit_record(incident["incident_id"], "DIAGNOSIS_FAILED",
                     {"message": error, "attempts": failures,
                      "reason": "retry limit reached" if retryable else "non-retryable error"})
    if incident["status"] == "ESCALATED":
        enqueue_archive(incident)

def _measure(identity):
    from app.api.incidents import find_incident, lock_for, persist_incidents
    from app.orchestration.orchestrator import validate_and_apply_recovery
    from app.providers.errors import IntegrationError
    error = None
    try:
        validate_and_apply_recovery(identity)
    except (IntegrationError, ValueError) as exc:
        error = str(exc)
    interval = _setting("M4_RECOVERY_POLL_INTERVAL_SECONDS", 5, 1, 300)
    with lock_for(identity):
        incident = find_incident(identity)
        if incident is not None and incident["status"] == "VALIDATING":
            incident["_recovery_last_error"] = error
            incident["_recovery_poll_at"] = (datetime.now(timezone.utc) + timedelta(seconds=interval)).isoformat()
            persist_incidents()


def work_once(pool=None, running=None):
    """One pass. With a pool, network work runs concurrently and outside any lock.

    `running` is the set of (task, incident) already submitted; a task is never
    queued twice. Without a pool (tests) everything runs inline.
    """
    from app.api.incidents import incidents, lock_for
    from app.orchestration.orchestrator import retry_pending_executions
    from app.orchestration.archive import enqueue_archive, deliver_archive
    now = datetime.now(timezone.utc)
    retry_pending_executions()
    auto_build = _enabled("M4_AUTO_BUILD_DECISIONS")
    auto_recovery = _enabled("M4_AUTO_VALIDATE_RECOVERY")
    for incident in list(incidents):
        identity = incident["incident_id"]
        tasks = []
        with lock_for(identity):
            if incident["status"] in {"RESOLVED", "ESCALATED"}:
                enqueue_archive(incident)
            if (auto_build and incident.get("_diagnosis_pending")
                    and incident["status"] in {"DETECTED", "CORRELATING", "DIAGNOSING", "DIAGNOSED"}
                    and _due(incident.get("_diagnosis_not_before"), now) and _due(incident.get("_diagnosis_retry_at"), now)):
                tasks.append(("diagnose", _diagnose))
            if auto_recovery and incident["status"] == "VALIDATING" and _due(incident.get("_recovery_poll_at"), now):
                tasks.append(("measure", _measure))
            if (incident.get("_archive_outbox") or {}).get("status") == "PENDING":
                tasks.append(("archive", lambda i: deliver_archive(i, now=now)))
        for name, task in tasks:
            key = (name, identity)
            if pool is None:
                task(identity)
            elif key not in running:
                running.add(key)
                future = pool.submit(task, identity)
                future.add_done_callback(lambda f, k=key: _finished(f, k, running))


def _finished(future, key, running):
    running.discard(key)
    if future.exception() is not None:
        logger.error("M4 %s task failed for %s; durable pending work is retained", key[0], key[1],
                     exc_info=future.exception())


class Worker:
    def __init__(self, lease=None):
        self.lease = lease
        self.stop = Event()
        self.thread = Thread(target=self.run, name="m4-lifecycle", daemon=True)
        self.pool = ThreadPoolExecutor(max_workers=int(_setting("M4_WORKER_CONCURRENCY", 4, 1, 32)),
                                       thread_name_prefix="m4-task")
        self.running = set()
        self.monitor = LeaseMonitor()

    def run(self):
        try:
            while not self.stop.is_set():
                if workflow_store.lease_lost():
                    logger.error("M4 writer lease lost; the worker stops mutating state")
                    break
                try:
                    work_once(self.pool, self.running)
                except Exception:
                    logger.exception("M4 lifecycle worker failed; durable pending work is retained")
                self.stop.wait(1)
        finally:
            # Let in-flight network work finish so its result is recorded under this lease.
            self.pool.shutdown(wait=True, cancel_futures=True)
            workflow_store.close_lease(self.lease)

    def start(self):
        self.monitor.start()
        self.thread.start()

    def close(self, timeout=40):
        self.stop.set()
        self.monitor.close()
        self.thread.join(timeout=timeout)


class LeaseMonitor:
    """Detects lease loss while idle, so readiness and mutations fail promptly."""

    def __init__(self):
        self.stop = Event()
        self.thread = Thread(target=self.run, name="m4-lease-monitor", daemon=True)

    def run(self):
        interval = _setting("M4_LEASE_CHECK_SECONDS", 5, 1, 60)
        while not self.stop.wait(interval):
            if not workflow_store.check_lease():
                return

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=5)
