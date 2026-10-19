"""Incident workflow: diagnosis, approved execution and measured recovery.

Every step that talks to another service (M3 RCA, M6 FinOps, M1 evidence and
recovery, Kubernetes) runs WITHOUT holding the incident lock, in three phases:

1. begin   (locked)   validate state, record a durable attempt/intent token
2. work    (unlocked) the network call
3. commit  (locked)   apply the result only if the token is still current

A result whose token was superseded (state changed, newer attempt, restart) is
rejected as stale rather than applied. Execution additionally never repeats an
action whose outcome is unknown.
"""
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os
from threading import Condition, Lock
from uuid import uuid4

from app.api.incidents import find_incident, lock_for, persist_incidents, update_incident_status
from app.contracts import ActionResult, DecisionProposal
from app import config
from app.decision_engine.engine import decide_from_rca, MIN_RCA_CONFIDENCE
from app.decision_engine.models import DecisionAction, DecisionResult
from app.providers.rca_provider import get_rca_result
from app.providers.finops_provider import get_finops_context
from app.providers.evidence_provider import capture_incident_evidence
from app.providers import evidence_provider as evidence_backend, recovery_provider
from app.providers.recovery_provider import validate_recovery
from app.providers.errors import (ConflictError, IntegrationError, PreconditionRequired, ProposalExpired,
                                  RecoveryPending)
from app.remediation import executor as remediation_backend
from app.remediation.executor import execute_remediation
from app.remediation.models import RemediationResult
from app.remediation.guardrails import (validate_action_allowed, validate_replica_count, validate_scale_step,
                                        validate_service_allowed)
from app.remediation.audit import add_audit_record
from app.state_machine.states import IncidentStatus
from app.state_machine.machine import transition
from app.orchestration.archive import enqueue_archive
from app.orchestration.proposals import proposal_digest, proposal_expired
from app.orchestration.provenance import configured_modes, record_provider_use

logger = logging.getLogger(__name__)
_pending_executions = {}
# In-process "currently running" markers. The durable tokens live on the
# incident; these only stop two local callers running the same step at once.
_inflight_lock = Lock()
_inflight_changed = Condition(_inflight_lock)
_inflight = {"diagnosis": set(), "execution": set(), "recovery": set()}


class _AlreadyRunning(Exception):
    pass

DIAGNOSABLE = {IncidentStatus.DETECTED, IncidentStatus.CORRELATING, IncidentStatus.DIAGNOSING, IncidentStatus.DIAGNOSED}


class NotFoundError(IntegrationError):
    category = "not_found"

    def __init__(self, message="Incident not found"):
        super().__init__(message, status_code=404, retryable=False)


def clear_pending_executions():
    _pending_executions.clear()
    with _inflight_lock:
        for values in _inflight.values():
            values.clear()


def _claim(kind, incident_id):
    with _inflight_lock:
        if incident_id in _inflight[kind]:
            return False
        _inflight[kind].add(incident_id)
        return True


def _release(kind, incident_id):
    with _inflight_lock:
        _inflight[kind].discard(incident_id)
        _inflight_changed.notify_all()


def _wait_until_idle(kind, incident_id, timeout):
    with _inflight_lock:
        return _inflight_changed.wait_for(lambda: incident_id not in _inflight[kind], timeout=timeout)


def in_flight(kind, incident_id):
    with _inflight_lock:
        return incident_id in _inflight[kind]


def provenance():
    """Compatibility: the CONFIGURED modes. Workflow provenance is recorded per use."""
    return configured_modes()


def _require(incident_id):
    incident = find_incident(incident_id)
    if incident is None:
        raise NotFoundError()
    return incident


def _finish_pending_execution(incident_id):
    """Retry only recording a completed actuator call, never the call itself."""
    pending = _pending_executions[incident_id]
    incident = find_incident(incident_id)
    if not pending["committed"]:
        incident.clear()
        incident.update(deepcopy(pending["snapshot"]))
        persist_incidents()
        pending["committed"] = True
    add_audit_record(incident_id, pending["event_type"], pending["details"])
    _pending_executions.pop(incident_id)
    if incident["status"] == IncidentStatus.ESCALATED.value:
        enqueue_archive(incident)
    return pending


def retry_pending_executions():
    for incident_id in list(_pending_executions):
        with lock_for(incident_id):
            if incident_id in _pending_executions:
                _finish_pending_execution(incident_id)


def incident_service(incident):
    services = incident.get("affected_services", [])
    if len(services) != 1 or not services[0]:
        raise ValueError("Automated remediation requires one explicit affected service")
    return services[0]


def invalidate_diagnosis(incident):
    for key in ("_rca", "_evidence", "_expected_state", "_proposal", "_decision", "_proposal_created_at"):
        incident.pop(key, None)


# -- diagnosis -----------------------------------------------------------------

@dataclass
class _Attempt:
    incident_id: str
    attempt_id: str
    service: str
    started_at: str | None
    rca: dict | None
    evidence: dict | None
    uses: list = field(default_factory=list)


class _Invalidate(Exception):
    """Diagnosis evidence no longer matches live state; discard it, then report `error`."""

    def __init__(self, error):
        super().__init__(str(error))
        self.error = error


def _begin_diagnosis(incident_id):
    with lock_for(incident_id):
        incident = _require(incident_id)
        state = IncidentStatus(incident["status"])
        if incident.get("_decision") is not None:
            if state != IncidentStatus.AWAITING_APPROVAL or not proposal_expired(incident):
                # A decided workflow is history: return it, never rewrite it.
                return DecisionResult.model_validate(incident["_decision"])
            add_audit_record(incident_id, "PROPOSAL_EXPIRED", {"created_at": incident.get("_proposal_created_at"),
                                                               "ttl_seconds": config.PROPOSAL_TTL_SECONDS})
            invalidate_diagnosis(incident)
            incident.pop("proposed_action", None)
            incident.pop("scale_option", None)
            incident["status"] = transition(state, IncidentStatus.DIAGNOSED).value
            state = IncidentStatus.DIAGNOSED
        if state not in DIAGNOSABLE:
            raise ConflictError(f"Incident in {state.value} is not ready for diagnosis")
        service = incident_service(incident)
        if not _claim("diagnosis", incident_id):
            raise _AlreadyRunning()
        try:
            if state == IncidentStatus.DETECTED:
                incident["status"] = transition(state, IncidentStatus.CORRELATING).value
                state = IncidentStatus.CORRELATING
            if state == IncidentStatus.CORRELATING:
                incident["status"] = transition(state, IncidentStatus.DIAGNOSING).value
            attempt_id = "DIAG-" + uuid4().hex
            incident["_diagnosis_attempt"] = {"attempt_id": attempt_id,
                                              "started_at": datetime.now(timezone.utc).isoformat()}
            persist_incidents()
        except BaseException:
            _release("diagnosis", incident_id)
            raise
        return _Attempt(incident_id, attempt_id, service, incident.get("started_at"),
                        deepcopy(incident.get("_rca")), deepcopy(incident.get("_evidence")))


def _checkpoint_rca(attempt, rca, mode):
    """Make the measured RCA durable before slower evidence work, so a later
    failure retries evidence without asking M3 again."""
    with lock_for(attempt.incident_id):
        incident = _require(attempt.incident_id)
        if not _current_attempt(incident, attempt):
            raise _stale(attempt)
        record_provider_use(incident, "rca", mode)
        attempt.uses.remove(("rca", mode))
        incident["_rca"] = rca
        if incident["status"] == IncidentStatus.DIAGNOSING.value:
            incident["status"] = transition(IncidentStatus.DIAGNOSING, IncidentStatus.DIAGNOSED).value
        persist_incidents()


def _use(attempt, role):
    mode = configured_modes()[role]
    attempt.uses.append((role, mode))
    return mode


def _escalation(rca, reason):
    return DecisionResult(action=DecisionAction.ESCALATE, confidence=rca["confidence"], reason=reason,
                          approval_required=False)


def _gather_diagnosis(attempt):
    """All network I/O for one diagnosis. Holds no workflow lock."""
    incident_id, service = attempt.incident_id, attempt.service
    rca = attempt.rca
    if rca is None:
        mode = _use(attempt, "rca")
        rca = get_rca_result(incident_id)
        if rca.get("incident_id") != incident_id:
            raise IntegrationError("RCA result belongs to another incident", status_code=502, retryable=False)
        _checkpoint_rca(attempt, rca, mode)
    result = decide_from_rca(rca)
    finops, finops_error = None, None
    # FinOps is needed only for a confident scaling diagnosis.
    if rca["root_cause"] == "traffic_spike" and rca["confidence"] >= MIN_RCA_CONFIDENCE:
        try:
            _use(attempt, "finops")
            finops = get_finops_context(service)
            result = decide_from_rca(rca, finops)
        except (IntegrationError, ValueError) as exc:
            finops_error = str(exc)
            result = decide_from_rca(rca, None)
    if result.action != DecisionAction.ESCALATE:
        try:
            validate_service_allowed(service)
            validate_action_allowed(result.action, service=service)
            if result.action == DecisionAction.SCALE:
                replicas = result.scale_option["replicas"]
                validate_replica_count(replicas, service=service)
                validate_scale_step(finops["current_replicas"], replicas, service=service)
        except ValueError as exc:
            result = _escalation(rca, f"Proposed action is outside the service registry guardrails: {exc}")
    evidence, expected_state, scenario, parameters = attempt.evidence, None, None, {}
    if result.action != DecisionAction.ESCALATE:
        if not (rca["affected_component"] == service or rca["affected_component"].startswith(service + ":")):
            raise IntegrationError("RCA component does not match the affected service", status_code=502, retryable=False)
        if evidence is None:
            _use(attempt, "evidence")
            evidence = capture_incident_evidence(incident_id, service, rca["root_cause"],
                                                 incident_started_at=attempt.started_at)
        before = evidence["before"]
        if (evidence_backend.EVIDENCE_PROVIDER == "real"
                and rca["affected_component"] != service + ":" + before["version"]):
            raise _Invalidate(ConflictError("Deployment version changed after RCA; diagnose again"))
        context = evidence.get("kubernetes") or {}
        captured_replicas = context.get("desired_replicas", before.get("metrics", {}).get("replica_count"))
        if (isinstance(captured_replicas, bool) or not isinstance(captured_replicas, int) or captured_replicas < 1
                or not before.get("version") or before["version"].lower() == "unknown"
                or context.get("version", before["version"]) != before["version"]
                or (finops is not None and finops["current_replicas"] != captured_replicas)):
            raise _Invalidate(ConflictError("Captured deployment state changed during diagnosis; diagnose again"))
        expected_state = {"version": before["version"], "replicas": captured_replicas}
        scenario = evidence["scenario"]
        if result.action == DecisionAction.ROLLBACK:
            deployment = evidence.get("deployment_event")
            if not deployment or deployment.get("service") != service:
                raise IntegrationError("Rollback requires captured deployment evidence", status_code=502, retryable=False)
            if rca["affected_component"] != service + ":" + deployment["new_version"]:
                raise _Invalidate(ConflictError("Deployment version changed after RCA; diagnose again"))
            parameters = {"from_version": deployment["new_version"], "to_version": deployment["old_version"]}
        elif result.action == DecisionAction.SCALE:
            parameters = {"replicas": result.scale_option["replicas"]}
        if remediation_backend.REMEDIATION_BACKEND == "kubernetes":
            try:
                expected_state = remediation_backend.bind_live_preconditions(
                    service, expected_state,
                    cpu_request_m=finops["current_cpu_request_m"] if finops is not None else None,
                    rollback_target=parameters.get("to_version"))
            except Exception as exc:
                raise _Invalidate(ConflictError(f"Live remediation preconditions could not be captured: {exc}")) from exc
    return {"rca": rca, "result": result, "finops": finops, "finops_error": finops_error, "evidence": evidence,
            "expected_state": expected_state, "scenario": scenario, "parameters": parameters}


def _current_attempt(incident, attempt):
    return ((incident.get("_diagnosis_attempt") or {}).get("attempt_id") == attempt.attempt_id
            and IncidentStatus(incident["status"]) in {IncidentStatus.DIAGNOSING, IncidentStatus.DIAGNOSED})


def _stale(attempt):
    return ConflictError(f"Diagnosis attempt {attempt.attempt_id} was superseded; its result was discarded",
                         retryable=True)


def _commit_diagnosis(attempt, gathered):
    incident_id = attempt.incident_id
    with lock_for(incident_id):
        incident = _require(incident_id)
        if not _current_attempt(incident, attempt):
            raise _stale(attempt)
        for role, mode in attempt.uses:
            record_provider_use(incident, role, mode)
        rca, result, finops = gathered["rca"], gathered["result"], gathered["finops"]
        incident["_rca"] = rca
        if finops is not None:
            incident["_finops"] = finops
        if gathered["evidence"] is not None:
            incident["_evidence"] = gathered["evidence"]
        if incident["status"] == IncidentStatus.DIAGNOSING.value:
            incident["status"] = transition(IncidentStatus.DIAGNOSING, IncidentStatus.DIAGNOSED).value
        # Checkpoint the diagnosis before the proposal, so a failed proposal
        # commit still leaves the measured RCA durable for the retry.
        persist_incidents()
        if gathered["finops_error"]:
            add_audit_record(incident_id, "FINOPS_UNAVAILABLE", {"message": gathered["finops_error"]})
        if gathered["expected_state"] is not None:
            incident["_expected_state"] = gathered["expected_state"]
            incident["recovery_scenario"] = gathered["scenario"]
        proposal = DecisionProposal(incident_id=incident_id, recommended_action=result.action.value,
                                    target=attempt.service, parameters=gathered["parameters"], confidence=result.confidence,
                                    # A scale proposal carries the risk M6 assessed for the chosen option.
                                    risk=("MEDIUM" if result.action == DecisionAction.ROLLBACK
                                          else result.scale_option.get("risk", "HIGH") if result.action == DecisionAction.SCALE
                                          else "HIGH"),
                                    reason=result.reason, approval_required=result.approval_required)
        incident.update(root_cause=rca["root_cause"], proposed_action=result.action.value,
                        scale_option=result.scale_option, _proposal=proposal.model_dump(mode="json"),
                        _decision=result.model_dump(mode="json"),
                        _proposal_created_at=datetime.now(timezone.utc).isoformat())
        for key in ("_diagnosis_attempt", "_diagnosis_last_error", "_diagnosis_retry_at", "_diagnosis_failures"):
            incident.pop(key, None)
        incident["_diagnosis_pending"] = False
        incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.ACTION_PROPOSED).value
        incident["status"] = transition(IncidentStatus(incident["status"]),
            IncidentStatus.ESCALATED if result.action == DecisionAction.ESCALATE else IncidentStatus.AWAITING_APPROVAL).value
        persist_incidents()
        add_audit_record(incident_id, "DECISION_PROPOSED", proposal.model_dump(mode="json"))
        if result.action == DecisionAction.ESCALATE:
            enqueue_archive(incident)
        return result


def build_decision(incident_id: str):
    try:
        attempt = _begin_diagnosis(incident_id)
    except _AlreadyRunning:
        # Join the attempt already in flight instead of starting a duplicate:
        # its committed decision is returned once it finishes.
        join = float(os.getenv("M4_DIAGNOSIS_JOIN_SECONDS", "30"))
        if not _wait_until_idle("diagnosis", incident_id, join):
            raise ConflictError("Diagnosis is still running for this incident; retry shortly", retryable=True)
        try:
            attempt = _begin_diagnosis(incident_id)
        except _AlreadyRunning:
            raise ConflictError("Diagnosis is already running for this incident", retryable=True) from None
    if isinstance(attempt, DecisionResult):
        return attempt
    try:
        try:
            gathered = _gather_diagnosis(attempt)
        except _Invalidate as invalid:
            with lock_for(incident_id):
                incident = _require(incident_id)
                if _current_attempt(incident, attempt):
                    invalidate_diagnosis(incident)
                    incident.pop("_diagnosis_attempt", None)
                    persist_incidents()
            raise invalid.error from invalid
        return _commit_diagnosis(attempt, gathered)
    finally:
        _release("diagnosis", incident_id)


# -- approved execution --------------------------------------------------------

def _check_approval(incident, action, replicas, digest, require_digest):
    proposed = incident.get("proposed_action")
    if proposed is not None and proposed != action.value:
        raise ValueError("Requested action does not match the approved proposal")
    if action == DecisionAction.SCALE and incident.get("scale_option") is not None:
        if incident["scale_option"].get("replicas") != replicas:
            raise ValueError("Replica count does not match the approved proposal")
    if remediation_backend.REMEDIATION_BACKEND != "mock" and not incident.get("_proposal"):
        raise ValueError("Real remediation requires a stored decision proposal")
    if incident.get("_proposal"):
        if digest is not None and digest != proposal_digest(incident):
            raise ConflictError("The proposal changed since it was reviewed; reload and review again")
        if digest is None and require_digest:
            raise PreconditionRequired("Real approval must name the reviewed proposal with X-Proposal-Digest")


def execute_approved_action(incident_id: str, action, approved: bool, replicas: int | None = None,
                            approver: str = "operator", digest: str | None = None, require_digest: bool | None = None):
    if require_digest is None:
        # Any real provider or actuator makes this a real approval: name the reviewed proposal.
        require_digest = remediation_backend.REMEDIATION_BACKEND != "mock" or "real" in configured_modes().values()
    with lock_for(incident_id):
        incident = _require(incident_id)
        action = DecisionAction(action)
        if not approved:
            raise ValueError("Remediation cannot execute without approval")
        service = incident_service(incident)
        validate_service_allowed(service)
        validate_action_allowed(action, service=service)
        if action == DecisionAction.SCALE:
            validate_replica_count(replicas, service=service)
        _check_approval(incident, action, replicas, digest, require_digest)
        if incident_id in _pending_executions:
            pending = _finish_pending_execution(incident_id)
            if pending.get("failure"):
                raise ValueError(pending["failure"])
            return RemediationResult.model_validate(incident["_execution_result"])
        if incident.get("_execution_result") is not None:
            # Idempotent replay: the recorded result, never a second action.
            return RemediationResult.model_validate(incident["_execution_result"])
        if in_flight("execution", incident_id):
            raise ConflictError("This action is executing now; it will not be started again", retryable=True)
        if incident.get("_execution_intent") or incident.get("_execution_ambiguous"):
            raise ConflictError("Execution outcome requires operator reconciliation; action will not be repeated")
        if incident["status"] != IncidentStatus.AWAITING_APPROVAL.value:
            raise ConflictError("Incident is not awaiting approval")
        if proposal_expired(incident):
            raise ProposalExpired("Proposal has expired or has no valid timestamp; diagnose again to capture fresh state")
        if action == DecisionAction.SCALE:
            current = (incident.get("_expected_state") or {}).get("replicas")
            if current is not None:
                validate_scale_step(current, replicas, service=service)
        started_at = datetime.now(timezone.utc)
        action_id = "ACT-" + uuid4().hex
        add_audit_record(incident_id, "MANUAL_APPROVAL_GRANTED", {"approver": approver, "action": action.value,
                                                               "action_id": action_id,
                                                               "proposal_digest": proposal_digest(incident)})
        add_audit_record(incident_id, "ACTION_APPROVED", {"action": action.value, "replicas": replicas, "target": service})
        incident["_execution_intent"] = {"action_id": action_id, "incident_id": incident_id,
            "action": action.value, "replicas": replicas, "target": service,
            "started_at": started_at.isoformat(), "approver": approver, "status": "INTENT"}
        record_provider_use(incident, "remediation", configured_modes()["remediation"])
        # The durable intent and captured preconditions precede the external side effect.
        incident["status"] = transition(IncidentStatus.AWAITING_APPROVAL, IncidentStatus.EXECUTING, approved=True).value
        persist_incidents()
        expected_state = deepcopy(incident.get("_expected_state"))
        rollback_target = incident.get("_proposal", {}).get("parameters", {}).get("to_version")
        _claim("execution", incident_id)

    # The external side effect runs outside the lock.
    execution_exc = None
    result = None
    try:
        try:
            result = execute_remediation(action, approved=True, replicas=replicas, service=service,
                                         expected_state=expected_state, rollback_target=rollback_target)
            if not result.success:
                execution_exc = ValueError("Remediation reported failure")
        except Exception as exc:
            execution_exc = exc
        return _record_execution(incident_id, action, action_id, started_at, result, execution_exc)
    finally:
        _release("execution", incident_id)


def _record_execution(incident_id, action, action_id, started_at, result, execution_exc):
    with lock_for(incident_id):
        incident = _require(incident_id)
        if (incident.get("_execution_intent") or {}).get("action_id") != action_id:
            # Never happens without a competing writer; never overwrite its state.
            raise ConflictError("Execution intent changed while the action ran; operator reconciliation required")
        completed_at = datetime.now(timezone.utc)
        deadline = (completed_at + timedelta(seconds=config.RECOVERY_TIMEOUT_SECONDS)).isoformat()
        if execution_exc is not None:
            if isinstance(execution_exc, remediation_backend.RolloutUnconfirmed):
                incident["action_completed_at"] = completed_at.isoformat()
                incident["_action_result"] = ActionResult(
                    action_id=action_id, incident_id=incident_id,
                    action=action.value, status="SUCCESS", started_at=started_at, completed_at=completed_at
                ).model_dump(mode="json")
                incident["_execution_result"] = {
                    "action": action.value, "success": True, "message": str(execution_exc), "rollout_unconfirmed": True
                }
                incident["recovery_deadline_at"] = deadline
                incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.VALIDATING).value
                _pending_executions[incident_id] = {
                    "snapshot": deepcopy(incident), "committed": False,
                    "event_type": "ACTION_APPLIED_ROLLOUT_UNCONFIRMED",
                    "details": {"action": action.value, "message": str(execution_exc)}
                }
                _finish_pending_execution(incident_id)
                return RemediationResult(action=action, success=True, message=str(execution_exc))

            incident["_action_result"] = ActionResult(action_id=action_id, incident_id=incident_id,
                action=action.value, status="FAILED", started_at=started_at, completed_at=completed_at).model_dump(mode="json")
            incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.FAILED_REMEDIATION).value
            incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.ESCALATED).value
            _pending_executions[incident_id] = {"snapshot": deepcopy(incident), "committed": False,
                "event_type": "ACTION_EXECUTION_FAILED", "details": {"action": action.value, "message": str(execution_exc)},
                "failure": f"Remediation failed: {execution_exc}"}
            _finish_pending_execution(incident_id)
            raise ValueError("Remediation failed; see the audit trail for this incident") from execution_exc

        incident["_execution_result"] = result.model_dump(mode="json")
        incident["_action_result"] = ActionResult(action_id=action_id, incident_id=incident_id,
            action=action.value, status="SUCCESS", started_at=started_at, completed_at=completed_at).model_dump(mode="json")
        incident["action_completed_at"] = incident["_action_result"]["completed_at"]
        incident["recovery_deadline_at"] = deadline
        incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.VALIDATING).value
        _pending_executions[incident_id] = {"snapshot": deepcopy(incident), "committed": False,
            "event_type": "ACTION_EXECUTED", "details": deepcopy(incident["_action_result"])}
        _finish_pending_execution(incident_id)
        return result


# -- reconciliation of an unknown outcome --------------------------------------

def reconcile_execution(incident_id: str, outcome: str, observed_by: str, evidence: str, note: str = ""):
    """Record an operator's OBSERVATION of an action whose outcome M4 could not record.

    Allowed exactly once, only for a durable execution intent with an ambiguous
    outcome. It never creates an actuator ActionResult: the operator's finding
    is stored separately, and recovery is still measured by M1.
    """
    with lock_for(incident_id, timeout=5.0):
        incident = _require(incident_id)
        previous = incident.get("_reconciliation")
        if previous is not None:
            if previous["outcome"] == outcome and previous["evidence"] == evidence:
                return incident  # Idempotent repeat of the same observation.
            raise ConflictError("This execution was already reconciled; the recorded observation is immutable")
        intent = incident.get("_execution_intent")
        if (not incident.get("_execution_ambiguous") or not isinstance(intent, dict) or not intent.get("action_id")
                or incident.get("_execution_result") is not None or incident.get("_action_result") is not None
                or incident["status"] != IncidentStatus.ESCALATED.value):
            raise ConflictError("Only a recorded execution intent with an unknown outcome can be reconciled")
        now = datetime.now(timezone.utc)
        incident["_reconciliation"] = {"outcome": outcome, "observed_by": observed_by, "evidence": evidence,
                                       "note": note, "reconciled_at": now.isoformat(),
                                       "action_id": intent["action_id"], "action": intent.get("action"),
                                       "kind": "operator_observation"}
        incident["_execution_ambiguous"] = False
        add_audit_record(incident_id, "MANUAL_RECONCILIATION_" + ("CONFIRMED" if outcome == "CONFIRMED" else "FAILED"),
                         {"approver": observed_by, "outcome": outcome, "evidence": evidence, "note": note,
                          "action_id": intent["action_id"]})
        if outcome == "CONFIRMED":
            # Recovery is measured from when the operator confirmed the change,
            # never earlier: M1 still has to observe sustained health.
            incident["action_completed_at"] = now.isoformat()
            incident["_action_completed_source"] = "operator_reconciliation"
            incident["recovery_deadline_at"] = (now + timedelta(seconds=config.RECOVERY_TIMEOUT_SECONDS)).isoformat()
            incident["status"] = transition(IncidentStatus.ESCALATED, IncidentStatus.VALIDATING, reconciled=True).value
            persist_incidents()
        else:
            persist_incidents()
            enqueue_archive(incident)
        return incident


# -- measured recovery ---------------------------------------------------------

def recovery_now():
    return datetime.now(timezone.utc)


def finish_recovery_timeout(incident, deadline):
    recovery = dict(incident.get("_last_recovery_measurement", {
        "incident_id": incident["incident_id"], "success": False, "source": "timeout",
    }), success=False, timed_out=True, deadline_at=deadline.isoformat())
    incident["_recovery"] = recovery
    add_audit_record(incident["incident_id"], "RECOVERY_TIMEOUT", recovery)
    return apply_recovery_result(incident["incident_id"], False)


def _recovery_token(incident):
    return (incident.get("action_completed_at"), (incident.get("_action_result") or {}).get("action_id"),
            (incident.get("_reconciliation") or {}).get("reconciled_at"))


def _recovery_window(incident):
    scenario = incident.get("recovery_scenario")
    # Legacy scenario inference is allowed only for explicitly mock execution.
    if scenario is None and remediation_backend.REMEDIATION_BACKEND == "mock":
        scenario = {"faulty_deployment": "bad_deployment", "traffic_spike": "traffic_spike"}.get(incident.get("root_cause"))
    if scenario not in {"bad_deployment", "traffic_spike"}:
        raise ValueError("Missing captured remediation scenario")
    completion = incident.get("action_completed_at")
    if not completion:
        raise ValueError("Missing remediation completion timestamp")
    completed_at = datetime.fromisoformat(completion.replace("Z", "+00:00"))
    if completed_at.tzinfo is None:
        raise ValueError("Remediation completion timestamp must include its timezone")
    deadline = datetime.fromisoformat(incident.get("recovery_deadline_at", (
        completed_at + timedelta(seconds=config.RECOVERY_TIMEOUT_SECONDS)).isoformat()).replace("Z", "+00:00"))
    if deadline.tzinfo is None:
        raise ValueError("Recovery deadline must include its timezone")
    return scenario, completion, deadline


def validate_and_apply_recovery(incident_id: str):
    try:
        return _validate_recovery_once(incident_id)
    except _AlreadyRunning:
        # Join the measurement already running (e.g. the worker's poll) and use its outcome.
        join = float(os.getenv("M4_RECOVERY_JOIN_SECONDS", "40"))
        if not _wait_until_idle("recovery", incident_id, join):
            raise ConflictError("A recovery measurement is still running; retry shortly", retryable=True) from None
        try:
            return _validate_recovery_once(incident_id)
        except _AlreadyRunning:
            raise ConflictError("A recovery measurement is already running for this incident", retryable=True) from None


def _validate_recovery_once(incident_id: str):
    with lock_for(incident_id):
        incident = _require(incident_id)
        if incident["status"] in {IncidentStatus.RESOLVED.value, IncidentStatus.ESCALATED.value} and "_recovery" in incident:
            return incident
        if incident["status"] != IncidentStatus.VALIDATING.value:
            raise ConflictError("Incident must be in VALIDATING state")
        scenario, completion, deadline = _recovery_window(incident)
        if incident.get("recovery_deadline_at") != deadline.isoformat():
            incident["recovery_deadline_at"] = deadline.isoformat()
            persist_incidents()
        if recovery_now() >= deadline:
            return finish_recovery_timeout(incident, deadline)
        if not _claim("recovery", incident_id):
            raise _AlreadyRunning()
        token = _recovery_token(incident)
        service = incident_service(incident)
    try:
        mode = configured_modes()["recovery"]
        try:
            recovery = validate_recovery(incident_id, scenario=scenario, action_completed_at=completion, service=service)
            failure = None
        except IntegrationError as exc:
            recovery, failure = None, exc
        with lock_for(incident_id):
            incident = _require(incident_id)
            if incident["status"] != IncidentStatus.VALIDATING.value or _recovery_token(incident) != token:
                raise ConflictError("The incident changed while recovery was measured; the measurement was discarded",
                                    retryable=True)
            if failure is not None:
                if recovery_now() >= deadline:
                    return finish_recovery_timeout(incident, deadline)
                raise failure
            if recovery.get("incident_id") != incident_id:
                raise IntegrationError("Recovery result belongs to another incident", status_code=502, retryable=False)
            record_provider_use(incident, "recovery", mode)
            if recovery["success"] is not True:
                incident.setdefault("_first_negative_recovery", recovery)
                incident["_last_recovery_measurement"] = recovery
                persist_incidents()
                add_audit_record(incident_id, "RECOVERY_STABILIZING", recovery)
            if recovery_now() >= deadline:
                return finish_recovery_timeout(incident, deadline)
            if recovery["success"] is not True:
                raise RecoveryPending(f"Measured recovery has not restored the SLO; keep polling until {deadline.isoformat()}")
            incident["_recovery"] = recovery
            incident["recovery_validated_at"] = recovery_now().isoformat()
            return apply_recovery_result(incident_id, True)
    finally:
        _release("recovery", incident_id)


def apply_recovery_result(incident_id: str, success: bool):
    with lock_for(incident_id):
        return _apply_recovery_result(incident_id, success)


def _apply_recovery_result(incident_id: str, success: bool):
    incident = _require(incident_id)
    if "_recovery" not in incident and recovery_provider.RECOVERY_PROVIDER == "mock":
        record_provider_use(incident, "recovery", "mock")
        incident["_recovery"] = {"incident_id": incident_id, "success": success, "source": "mock"}
    # A pending-measurement message is stale once the outcome is decided.
    for key in ("_recovery_last_error", "_recovery_poll_at"):
        incident.pop(key, None)
    if success:
        incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.RESOLVED,
                                        recovery_validated=True).value
    else:
        incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.FAILED_REMEDIATION).value
        incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.ESCALATED).value
    persist_incidents()
    add_audit_record(incident_id, "RECOVERY_VALIDATION", {"success": success})
    enqueue_archive(incident)
    return incident
