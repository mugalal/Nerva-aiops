import math
import os
from datetime import datetime, timezone
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock, local
from fastapi import APIRouter, Header
from pydantic import AwareDatetime, BaseModel, Field, field_validator
from app.contracts import IncidentView
from app.state_machine.states import IncidentStatus
from app.state_machine.machine import transition
from app.db import workflow_store
from app.api.auth import verify_ingest_auth
from app.providers.errors import api_error

router = APIRouter(prefix="/api/incidents", tags=["incidents"])

# Working copies, mutated only while holding that incident's lock.
incidents = []
# The last durably committed version of every incident. Replaced (never
# mutated in place) so readers can iterate it without any lock.
_committed_incidents = []
# Short critical sections only: list membership, the committed list and the lock table.
_registry_lock = RLock()
_incident_locks = {}
_held = local()


def _held_ids():
    if not hasattr(_held, "ids"):
        _held.ids = []
    return _held.ids


def _committed_index():
    return {item["incident_id"]: item for item in _committed_incidents}


def committed_incidents():
    """Every incident's last durable state (an immutable snapshot list)."""
    return _committed_incidents


def committed_incident(incident_id):
    """The last durable state; never a half-finished mutation."""
    return next((item for item in _committed_incidents if item["incident_id"] == incident_id), None)


def _publish(saved):
    """Replace the committed copies of `saved` incidents with a new immutable list."""
    global _committed_incidents
    with _registry_lock:
        by_id = {item["incident_id"]: item for item in saved}
        updated = [by_id.pop(item["incident_id"], item) for item in _committed_incidents]
        _committed_incidents = updated + [item for item in saved if item["incident_id"] in by_id]


def mark_persisted():
    """Publish every working copy as committed (startup restore and tests)."""
    global _committed_incidents
    with _registry_lock:
        _committed_incidents = deepcopy(incidents)


def restore_persisted(incident_ids=None):
    """Discard unsaved working changes, keeping references held by callers."""
    with _registry_lock:
        committed = _committed_index()
        targets = set(incident_ids) if incident_ids is not None else {item["incident_id"] for item in incidents}
        kept = []
        for item in incidents:
            identity = item["incident_id"]
            if identity in targets:
                saved = committed.get(identity)
                if saved is None:
                    continue  # Never committed: a failed create disappears.
                item.clear()
                item.update(deepcopy(saved))
            kept.append(item)
        if incident_ids is None:
            known = {item["incident_id"] for item in kept}
            kept.extend(deepcopy(item) for item in _committed_incidents if item["incident_id"] not in known)
        incidents[:] = kept


def _dirty(ids):
    committed = _committed_index()
    changed = []
    for identity in dict.fromkeys(ids):
        item = find_incident(identity)
        if item is not None and item != committed.get(identity):
            changed.append(item)
    return changed


def _prepare(items):
    """Deep copies carrying the next revision; working copies change only after commit."""
    prepared = []
    for item in items:
        snapshot = deepcopy(item)
        snapshot["_revision"] = int(item.get("_revision") or 0) + 1
        prepared.append(snapshot)
    return prepared


TERMINAL_STATUSES = {IncidentStatus.RESOLVED.value, IncidentStatus.ESCALATED.value}


def _stamp_closed(items):
    """Record when an incident reached a terminal state (the cooldown clock)."""
    now = datetime.now(timezone.utc).isoformat()
    for item in items:
        if item.get("status") in TERMINAL_STATUSES:
            item.setdefault("_closed_at", now)
        else:
            item.pop("_closed_at", None)


def _commit(items, write):
    _stamp_closed(items)
    prepared = _prepare(items)
    try:
        write(prepared)
    except workflow_store.StorageError:
        restore_persisted([item["incident_id"] for item in items])
        raise
    for item, snapshot in zip(items, prepared):
        item["_revision"] = snapshot["_revision"]
    _publish(prepared)


def persist_incidents():
    """Commit the incidents this thread is mutating, each against its revision.

    Inside lock_for() only the locked incidents are written. Outside any lock
    (controlled tests and restore), every changed incident is committed under
    its own lock.
    """
    held = list(dict.fromkeys(_held_ids()))
    if held:
        items = _dirty(held)
        if items:
            _commit(items, workflow_store.save_incidents)
        return
    for identity in [item["incident_id"] for item in list(incidents)]:
        with lock_for(identity):
            items = _dirty([identity])
            if items:
                _commit(items, workflow_store.save_incidents)


def persist_anomaly(payload, incident_id):
    items = _dirty([incident_id])
    _commit(items, lambda prepared: workflow_store.save_anomaly(payload, incident_id, prepared))


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
    """Serialize mutations of ONE incident; other incidents proceed independently."""
    with _registry_lock:
        lock = _incident_locks.setdefault(incident_id, RLock())
    acquired = lock.acquire(timeout=timeout if timeout is not None else -1)
    if not acquired:
        from app.providers.errors import ConflictError
        raise ConflictError("Another operation is running for this incident; retry shortly", retryable=True)
    held = _held_ids()
    held.append(incident_id)
    try:
        yield
    except workflow_store.StorageError:
        restore_persisted([incident_id])
        raise
    finally:
        held.pop()
        lock.release()


@contextmanager
def incident_operation(incident_id: str, wait: float = 5.0):
    with lock_for(incident_id, timeout=wait):
        yield


def find_incident(incident_id: str):
    with _registry_lock:
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


# -- coalescing (anti-flapping) -------------------------------------------------
#
# M2 opens a new incident id whenever a service alerts again after a quiet gap.
# Without coalescing, a service that flaps gets one incident (and one proposed
# action) per flap. Instead a new id for a service that already has an open
# incident, or whose last incident closed less than the cooldown ago, becomes
# an ALIAS of that incident: its anomalies are linked there and no second
# diagnosis starts. If alerts under an alias continue after the cooldown has
# elapsed, the alias becomes a real incident of its own (see resolve_for_ingest).

def coalescing_enabled():
    return os.getenv("M4_COALESCE_INCIDENTS", "true").strip().lower() in {"1", "true", "yes", "on"}


def cooldown_seconds():
    raw = os.getenv("M4_INCIDENT_COOLDOWN_SECONDS", "300")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("M4_INCIDENT_COOLDOWN_SECONDS must be a number in [0, 86400]") from exc
    if not math.isfinite(value) or not 0 <= value <= 86400:
        raise ValueError("M4_INCIDENT_COOLDOWN_SECONDS must be a number in [0, 86400]")
    return value


def _parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def in_cooldown(incident, now=None):
    """True while a closed incident still absorbs new alerts for its service."""
    if incident.get("status") not in TERMINAL_STATUSES:
        return False
    closed = _parse_time(incident.get("_closed_at") or "")
    now = now or datetime.now(timezone.utc)
    return closed is not None and (now - closed).total_seconds() < cooldown_seconds()


def alias_target(incident_id):
    """The incident an alias id was coalesced into, or None."""
    for item in _committed_incidents:
        if incident_id in (item.get("_aliases") or {}):
            return item["incident_id"]
    return None


def coalesce_target(services, now=None):
    """The incident a new detection for `services` joins: an open one first, else one in cooldown."""
    wanted = set(services)
    same = [item for item in list(incidents) if set(item.get("affected_services", [])) == wanted
            and item.get("_kind") != "rightsizing"]
    active = [item for item in same if item.get("status") not in TERMINAL_STATUSES]
    if active:
        return max(active, key=lambda item: item.get("detected_at", "")), "active"
    cooling = [item for item in same if in_cooldown(item, now)]
    if cooling:
        return max(cooling, key=lambda item: item.get("_closed_at", "")), "cooldown"
    return None, None


def _new_incident(data):
    data["status"] = IncidentStatus.DETECTED.value
    data["detected_at"] = datetime.now(timezone.utc).isoformat()
    with _registry_lock:
        incidents.append(data)
    return data


def resolve_for_ingest(incident_id, started_at):
    """The incident an anomaly for `incident_id` is linked to (caller holds lock_for(incident_id)).

    An alias whose target is still open or cooling down resolves to the
    target. An alias whose target closed longer ago than the cooldown is a
    new problem: it becomes a real DETECTED incident under its own id.
    """
    from app.remediation.audit import add_audit_record
    if find_incident(incident_id) is not None:
        return incident_id
    target_id = alias_target(incident_id)
    if target_id is None:
        return None
    target = find_incident(target_id)
    if target is None or target.get("status") not in TERMINAL_STATUSES or in_cooldown(target):
        return target_id
    alias = (target.get("_aliases") or {}).get(incident_id) or {}
    _new_incident({"incident_id": incident_id, "severity": alias.get("severity", target["severity"]),
                   "affected_services": list(target["affected_services"]),
                   "started_at": started_at, "anomaly_ids": []})
    persist_incidents()
    add_audit_record(incident_id, "INCIDENT_OPENED_AFTER_COOLDOWN", {"previous_incident_id": target_id})
    return incident_id


@router.post("/", response_model=IncidentView)
def create_incident(
    incident: IncidentCreate,
    authorization: str | None = Header(default=None),
    x_nexus_ingest_token: str | None = Header(default=None),
    x_nexus_sender: str | None = Header(default=None),
):
    from app.remediation.audit import add_audit_record
    verify_ingest_auth(authorization, x_nexus_ingest_token, x_nexus_sender)
    with lock_for(incident.incident_id):
        if find_incident(incident.incident_id) is not None:
            raise api_error(409, "conflict", "Incident already exists")
        existing = alias_target(incident.incident_id)
        if existing is not None:
            return public_incident(committed_incident(existing))  # Idempotent repeat of a coalesced create.
        if coalescing_enabled():
            target, reason = coalesce_target(incident.affected_services)
            if target is not None:
                with lock_for(target["incident_id"]):
                    # Re-check under the target's lock: its state may have moved on.
                    if target.get("status") not in TERMINAL_STATUSES or in_cooldown(target):
                        target.setdefault("_aliases", {})[incident.incident_id] = {
                            "coalesced_at": datetime.now(timezone.utc).isoformat(), "reason": reason,
                            "severity": incident.severity}
                        persist_incidents()
                        add_audit_record(target["incident_id"], "INCIDENT_COALESCED",
                                         {"duplicate_incident_id": incident.incident_id, "reason": reason,
                                          "cooldown_seconds": cooldown_seconds()})
                        return public_incident(target)
        incident_data = _new_incident(incident.model_dump(mode="json"))
        persist_incidents()
        return public_incident(incident_data)


@router.get("/", response_model=list[IncidentView])
def list_incidents():
    # Reads never wait for a mutation: they see the last committed checkpoint.
    return [public_incident(incident) for incident in _committed_incidents]


@router.get("/{incident_id}", response_model=IncidentView)
def get_incident(incident_id: str):
    incident = committed_incident(incident_id) or committed_incident(alias_target(incident_id) or "")
    if incident is None:
        raise api_error(404, "not_found", "Incident not found")
    return public_incident(incident)


def _deadline_view(incident):
    deadline = incident.get("recovery_deadline_at")
    if not deadline:
        return None
    try:
        remaining = (datetime.fromisoformat(deadline) - datetime.now(timezone.utc)).total_seconds()
    except (TypeError, ValueError):
        return {"at": deadline, "valid": False}
    return {"at": deadline, "valid": True, "seconds_remaining": max(0.0, round(remaining, 1)),
            "expired": remaining <= 0}


@router.get("/{incident_id}/context")
def get_incident_context(incident_id: str):
    from app.api.anomalies import anomalies
    from app.orchestration.provenance import workflow_source, provenance_view
    from app.orchestration.proposals import proposal_digest, proposal_state
    from app.remediation.audit import audit_records
    incident = committed_incident(incident_id)
    if incident is None:
        raise api_error(404, "not_found", "Incident not found")
    linked = [anomalies[key] for key in incident.get("anomaly_ids", []) if key in anomalies]
    recovery = incident.get("_recovery") or incident.get("_last_recovery_measurement") or {}
    proposal = incident.get("_proposal")
    outbox = incident.get("_archive_outbox") or {}
    missing = [name for name, value in (
        ("anomaly", linked), ("rca", incident.get("_rca")), ("evidence", incident.get("_evidence")),
        ("decision", proposal), ("action_result", incident.get("_action_result")),
        ("recovery_result", recovery.get("details"))) if not value]
    return {"incident": public_incident(incident), "anomaly": linked[-1] if linked else None,
        "anomalies": linked, "rca": incident.get("_rca"), "decision": proposal,
        "proposal_digest": proposal_digest(incident) if proposal else None,
        "proposal_state": proposal_state(incident),
        "action_result": incident.get("_action_result"), "recovery_result": recovery.get("details"),
        "recovery_validation": recovery or None, "finops_context": incident.get("_finops"),
        "deployment_event": (incident.get("_evidence") or {}).get("deployment_event"),
        "evidence": incident.get("_evidence"), "source": workflow_source(incident),
        "provider_modes": provenance_view(incident)["modes"], "provenance": provenance_view(incident),
        "missing": missing,
        "workflow": {"revision": incident.get("_revision", 0),
            "action_completed_at": incident.get("action_completed_at"),
            "recovery_deadline_at": incident.get("recovery_deadline_at"),
            "recovery_deadline": _deadline_view(incident),
            "execution_ambiguous": bool(incident.get("_execution_ambiguous")),
            "execution_intent": incident.get("_execution_intent"),
            "reconciliation": incident.get("_reconciliation"),
            "diagnosis_error": incident.get("_diagnosis_last_error"),
            "recovery_error": incident.get("_recovery_last_error"),
            "archive_status": outbox.get("status"),
            "archive_attempts": outbox.get("attempts"),
            "archive_error": outbox.get("last_error") or incident.get("_archive_blocked_reason"),
            "coalesced_incident_ids": sorted(incident.get("_aliases") or {}),
            "recurrences_in_cooldown": int(incident.get("_recurrences") or 0),
            "diagnosis_failed": bool(incident.get("_diagnosis_failed")),
            "kind": incident.get("_kind") or "incident",
            "withdrawn": incident.get("_withdrawn"),
            "rejected_by": incident.get("_rejected_by"),
            "rightsizing": incident.get("_rightsizing")},
        "service_policy": _service_policy(incident),
        "audit_chain": _chain_summary(),
        "audit": [record for record in list(audit_records) if record["incident_id"] == incident_id]}


def _service_policy(incident):
    """The registry limits the proposal was checked against (shown to the operator)."""
    from shared.registry import get_registry
    services = incident.get("affected_services") or []
    spec = get_registry().get(services[0]) if len(services) == 1 else None
    return None if spec is None else {"service": spec.name, "depends_on": list(spec.depends_on),
                                      **spec.remediation.model_dump()}


def _chain_summary():
    from app.remediation.audit import verify_chain
    result = verify_chain()
    return {key: result[key] for key in ("valid", "records", "chained", "legacy_unchained", "head_hash")} | {
        "problems": len(result["problems"])}
