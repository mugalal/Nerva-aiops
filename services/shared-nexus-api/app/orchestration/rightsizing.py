"""Capacity right-sizing: propose giving back replicas a past scale-out added.

NERVA scaled out under load but never back, so every spike ratcheted capacity
(and cost) upward. This sweep looks for a service that has been quiet and
healthy on more replicas than its measured load needs, and PROPOSES a stepwise
scale-down. Like every action it needs an operator's digest-bound approval,
and M1 then measures that the SLO held on fewer replicas ("scale_down").

Sizing comes from measurements, never from a guess:
* rate:  the healthy baseline says how many requests per second one replica
         served within the SLO; the window's PEAK rate must fit the target
         replicas at no more than M4_SCALE_DOWN_LOAD_FACTOR times that.
* CPU:   the window's PEAK utilization, re-spread over the target replicas,
         must stay under M4_SCALE_DOWN_MAX_UTILIZATION.
The target never goes below the registry minimum and never drops more than
the registry's max_scale_down_step at once.

A right-sizing proposal is an incident of kind "rightsizing". It is never
diagnosed by M3, never absorbs a real detection (coalescing skips it), and is
withdrawn, not executed, if a real incident opens for the service or the
proposal expires.
"""
import logging
import math
import os
from datetime import datetime, timedelta, timezone

from app.decision_engine.models import DecisionAction, DecisionResult
from shared.registry import get_registry

logger = logging.getLogger(__name__)
KIND = "rightsizing"
_last_sweep = {"at": None}


def _number(name, default, low, high):
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number in [{low}, {high}]") from exc
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be a number in [{low}, {high}]")
    return value


def settings():
    return {"check_seconds": _number("M4_SCALE_DOWN_CHECK_SECONDS", 60, 10, 3600),
            "window_seconds": _number("M4_SCALE_DOWN_WINDOW_SECONDS", 600, 60, 86400),
            "quiet_seconds": _number("M4_SCALE_DOWN_QUIET_SECONDS", 600, 0, 86400),
            "load_factor": _number("M4_SCALE_DOWN_LOAD_FACTOR", 1.25, 0.1, 10),
            "max_utilization": _number("M4_SCALE_DOWN_MAX_UTILIZATION", 0.6, 0.05, 1)}


def enabled():
    """On by default wherever M1 telemetry is real; M4_SCALE_DOWN_ENABLED=false turns it off."""
    from app.providers import evidence_provider
    flag = os.getenv("M4_SCALE_DOWN_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    return flag and evidence_provider.EVIDENCE_PROVIDER == "real"


def due(now):
    last = _last_sweep["at"]
    return last is None or (now - last).total_seconds() >= settings()["check_seconds"]


def is_rightsizing(incident):
    return incident.get("_kind") == KIND


# -- the sizing decision (pure, no I/O) -------------------------------------------

def plan(window, baseline, current, min_replicas, max_step, config=None):
    """(target, facts) when fewer replicas would still carry the measured load, else (None, reason)."""
    config = config or settings()
    points = [point["metrics"] for point in window.get("points", [])]
    if len(points) < 3:
        return None, "not enough measurements in the window"
    if any(int(metrics["replica_count"]) != current for metrics in points):
        return None, "replica count changed during the window"
    if current <= min_replicas:
        return None, f"already at the registry minimum of {min_replicas}"
    thresholds = baseline["thresholds"]
    if any(metrics["latency_p95_ms"] > thresholds["latency_p95_ms_max"]
           or metrics["http_5xx_rate"] > thresholds["http_5xx_rate_max"] for metrics in points):
        return None, "the service was not healthy for the whole window"
    baseline_replicas = max(1.0, float(baseline["replica_count"]["maximum"]))
    per_replica_rate = float(baseline["request_rate"]["p95"]) / baseline_replicas
    if per_replica_rate <= 0:
        return None, "the baseline has no measured per-replica capacity"
    peak_rate = max(float(metrics["request_rate"]) for metrics in points)
    peak_cpu = max(float(metrics["cpu"]) for metrics in points)
    for_rate = math.ceil(peak_rate / (per_replica_rate * config["load_factor"]))
    for_cpu = math.ceil(peak_cpu * current / config["max_utilization"])
    needed = max(min_replicas, for_rate, for_cpu, 1)
    target = max(needed, current - max_step)
    facts = {"current_replicas": current, "target_replicas": target, "needed_replicas": needed,
             "window_seconds": config["window_seconds"], "peak_request_rate": round(peak_rate, 3),
             "baseline_rate_per_replica": round(per_replica_rate, 3), "peak_cpu_utilization": round(peak_cpu, 4),
             "projected_cpu_utilization": round(peak_cpu * current / target, 4),
             "replicas_needed_for_rate": for_rate, "replicas_needed_for_cpu": for_cpu}
    if target >= current:
        return None, f"the measured load still needs {needed} replicas"
    return target, facts


# -- the sweep --------------------------------------------------------------------

def _open_or_recent(service, now, quiet_seconds):
    from app.api.incidents import TERMINAL_STATUSES, _parse_time, committed_incidents
    for item in committed_incidents():
        if service not in item.get("affected_services", []):
            continue
        if item.get("status") not in TERMINAL_STATUSES:
            return item["incident_id"]
        closed = _parse_time(item.get("_closed_at") or "")
        if closed is not None and (now - closed).total_seconds() < quiet_seconds:
            return item["incident_id"]
    return None


def sweep(now=None):
    """Check every scalable registered service once; propose at most one scale-down each."""
    from app.providers import telemetry_provider
    from app.remediation import executor
    now = now or datetime.now(timezone.utc)
    _last_sweep["at"] = now
    config = settings()
    proposed = []
    for spec in get_registry().services:
        service, policy = spec.name, spec.remediation
        if "SCALE" not in policy.actions:
            continue
        try:
            if _open_or_recent(service, now, config["quiet_seconds"]):
                continue
            if executor.find_hpa(service) is not None:
                continue  # The autoscaler owns scale-down for this workload.
            baseline = telemetry_provider.get_baseline(service)
            window = telemetry_provider.get_window(service, now - timedelta(seconds=config["window_seconds"]), now)
            points = window.get("points") or []
            if not points:
                continue
            current = int(points[-1]["metrics"]["replica_count"])
            target, facts = plan(window, baseline, current, policy.min_replicas, policy.max_scale_down_step, config)
            if target is None:
                logger.debug("No scale-down for %s: %s", service, facts)
                continue
            proposed.append(propose(service, current, target, facts, now))
        except Exception:
            # Right-sizing is an optimisation: a failed check is logged and retried next sweep.
            logger.exception("Right-sizing check failed for %s", service)
    return proposed


def propose(service, current, target, facts, now=None):
    """Capture evidence and live preconditions, then record an AWAITING_APPROVAL scale-down."""
    from app.api.incidents import _new_incident, find_incident, lock_for, persist_incidents
    from app.contracts import DecisionProposal
    from app.providers.evidence_provider import capture_incident_evidence
    from app.remediation import executor
    from app.remediation.audit import add_audit_record
    from app.remediation.guardrails import validate_scale_down_step
    from app.state_machine.machine import transition
    from app.state_machine.states import IncidentStatus
    now = now or datetime.now(timezone.utc)
    validate_scale_down_step(current, target, service=service)
    incident_id = f"RIGHTSIZE-{now:%Y%m%d-%H%M%S}-{service}"
    started_at = now.isoformat()
    evidence = capture_incident_evidence(incident_id, service, "scale_down", incident_started_at=started_at)
    before = evidence["before"]
    captured = (evidence.get("kubernetes") or {}).get("desired_replicas", before["metrics"]["replica_count"])
    if captured != current:
        raise ValueError(f"{service} replicas changed from {current} to {captured} before the proposal")
    expected_state = {"version": before["version"], "replicas": current, "direction": "down"}
    if executor.REMEDIATION_BACKEND == "kubernetes":
        expected_state = executor.bind_live_preconditions(service, expected_state)
    reason = (f"Capacity right-sizing: for {int(facts['window_seconds'])} s {service} stayed healthy on {current} "
              f"replicas with a peak of {facts['peak_request_rate']} req/s and {facts['peak_cpu_utilization']:.0%} "
              f"CPU. One replica served {facts['baseline_rate_per_replica']} req/s at the healthy baseline, so "
              f"{target} replicas carry this load (projected CPU {facts['projected_cpu_utilization']:.0%}).")
    rca = {"incident_id": incident_id, "root_cause": "over_provisioned", "affected_component": service,
           "confidence": 1.0, "evidence": ["Capacity right-sizing by M4 from M1 measurements, not an M3 diagnosis",
                                           reason]}
    decision = DecisionResult(action=DecisionAction.SCALE, confidence=1.0, reason=reason, approval_required=True,
                              scale_option={"replicas": target, "risk": "LOW", "direction": "down",
                                            "estimated_cost_delta": 0})
    proposal = DecisionProposal(incident_id=incident_id, recommended_action="SCALE", target=service,
                                parameters={"replicas": target, "direction": "down"}, confidence=1.0, risk="LOW",
                                reason=reason, approval_required=True)
    with lock_for(incident_id):
        if find_incident(incident_id) is not None:
            raise ValueError(f"{incident_id} already exists")
        incident = _new_incident({"incident_id": incident_id, "severity": "low", "affected_services": [service],
                                  "started_at": started_at, "anomaly_ids": []})
        for step in (IncidentStatus.CORRELATING, IncidentStatus.DIAGNOSING, IncidentStatus.DIAGNOSED,
                     IncidentStatus.ACTION_PROPOSED, IncidentStatus.AWAITING_APPROVAL):
            incident["status"] = transition(IncidentStatus(incident["status"]), step).value
        incident.update(_kind=KIND, root_cause="over_provisioned", proposed_action="SCALE",
                        scale_option=decision.scale_option, _rca=rca, _evidence=evidence,
                        _expected_state=expected_state, recovery_scenario="scale_down", _rightsizing=facts,
                        _proposal=proposal.model_dump(mode="json"), _decision=decision.model_dump(mode="json"),
                        _proposal_created_at=datetime.now(timezone.utc).isoformat(), _diagnosis_pending=False)
        persist_incidents()
        add_audit_record(incident_id, "SCALE_DOWN_PROPOSED", facts)
        add_audit_record(incident_id, "DECISION_PROPOSED", proposal.model_dump(mode="json"))
    return incident_id


def withdraw(incident, reason):
    """Close a pending scale-down without acting (caller holds the incident lock)."""
    from app.api.incidents import persist_incidents
    from app.remediation.audit import add_audit_record
    from app.state_machine.machine import transition
    from app.state_machine.states import IncidentStatus
    if incident.get("status") != IncidentStatus.AWAITING_APPROVAL.value:
        return False
    incident["status"] = transition(IncidentStatus.AWAITING_APPROVAL, IncidentStatus.ESCALATED).value
    incident["_withdrawn"] = reason
    persist_incidents()
    add_audit_record(incident["incident_id"], "SCALE_DOWN_WITHDRAWN", {"reason": reason})
    return True


def real_incident_open(service, exclude=None):
    """A non-right-sizing incident still open for `service` (it outranks any scale-down)."""
    from app.api.incidents import TERMINAL_STATUSES, committed_incidents
    return next((item["incident_id"] for item in committed_incidents()
                 if item["incident_id"] != exclude and service in item.get("affected_services", [])
                 and not is_rightsizing(item) and item.get("status") not in TERMINAL_STATUSES), None)
