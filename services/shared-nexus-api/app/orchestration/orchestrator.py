import logging
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from app.api.incidents import find_incident, lock_for, persist_incidents, update_incident_status
from app.contracts import ActionResult, DecisionProposal
from app.config import RECOVERY_TIMEOUT_SECONDS
from app.decision_engine.engine import decide_from_rca
from app.decision_engine.models import DecisionAction, DecisionResult
from app.providers.rca_provider import get_rca_result
from app.providers.finops_provider import get_finops_context
from app.providers.evidence_provider import capture_incident_evidence
from app.providers import evidence_provider as evidence_backend, rca_provider, finops_provider, recovery_provider
from app.providers.recovery_provider import validate_recovery
from app.providers.errors import IntegrationError, RecoveryPending
from app.remediation import executor as remediation_backend
from app.remediation.executor import execute_remediation
from app.remediation.models import RemediationResult
from app.remediation.guardrails import validate_service_allowed, validate_replica_count, validate_action_allowed
from app.remediation.audit import add_audit_record
from app.state_machine.states import IncidentStatus
from app.state_machine.machine import transition
from app.orchestration.archive import enqueue_archive

logger = logging.getLogger(__name__)
_pending_executions = {}


def clear_pending_executions():
    _pending_executions.clear()


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


def provenance():
    return {"rca": rca_provider.RCA_PROVIDER, "evidence": evidence_backend.EVIDENCE_PROVIDER,
            "finops": finops_provider.FINOPS_PROVIDER, "recovery": recovery_provider.RECOVERY_PROVIDER,
            "remediation": "mock" if remediation_backend.REMEDIATION_BACKEND == "mock" else "real"}


def incident_service(incident):
    services = incident.get("affected_services", [])
    if len(services) != 1 or not services[0]:
        raise ValueError("Automated remediation requires one explicit affected service")
    return services[0]


def invalidate_diagnosis(incident):
    for key in ("_rca", "_evidence", "_expected_state"):
        incident.pop(key, None)


def build_decision(incident_id: str):
    with lock_for(incident_id):
        incident = find_incident(incident_id)
        if incident is None:
            raise ValueError("Incident not found")
        if incident.get("_decision") is not None:
            return DecisionResult.model_validate(incident["_decision"])
        incident.setdefault("_provenance", provenance())
        state = IncidentStatus(incident["status"])
        if state == IncidentStatus.DETECTED:
            update_incident_status(incident_id, IncidentStatus.CORRELATING)
            state = IncidentStatus.CORRELATING
        if state == IncidentStatus.CORRELATING:
            update_incident_status(incident_id, IncidentStatus.DIAGNOSING)
            state = IncidentStatus.DIAGNOSING
        if state not in {IncidentStatus.DIAGNOSING, IncidentStatus.DIAGNOSED}:
            raise ValueError("Incident is not ready for diagnosis")
        service = incident_service(incident)
        rca = incident.get("_rca")
        if rca is None:
            rca = get_rca_result(incident_id)
            if rca.get("incident_id") != incident_id:
                raise IntegrationError("RCA result belongs to another incident", status_code=502, retryable=False)
            incident["_rca"] = rca
        if state == IncidentStatus.DIAGNOSING:
            update_incident_status(incident_id, IncidentStatus.DIAGNOSED)
        result = decide_from_rca(rca)
        finops = None
        # FinOps is needed only for a confident scaling diagnosis.
        from app.decision_engine.engine import MIN_RCA_CONFIDENCE
        if rca["root_cause"] == "traffic_spike" and rca["confidence"] >= MIN_RCA_CONFIDENCE:
            try:
                finops = get_finops_context(service)
                incident["_finops"] = finops
                result = decide_from_rca(rca, finops)
            except (IntegrationError, ValueError) as exc:
                add_audit_record(incident_id, "FINOPS_UNAVAILABLE", {"message": str(exc)})
                result = decide_from_rca(rca, None)
        if result.action != DecisionAction.ESCALATE:
            try:
                validate_service_allowed(service)
            except ValueError:
                result = DecisionResult(action=DecisionAction.ESCALATE, confidence=rca["confidence"],
                                        reason="Affected service is outside remediation guardrails", approval_required=False)
        if result.action != DecisionAction.ESCALATE:
            if not (rca["affected_component"] == service or rca["affected_component"].startswith(service + ":")):
                raise IntegrationError("RCA component does not match the affected service", status_code=502, retryable=False)
            evidence = incident.get("_evidence")
            if evidence is None:
                evidence = capture_incident_evidence(incident_id, service, rca["root_cause"],
                                                     incident_started_at=incident.get("started_at"))
                incident["_evidence"] = evidence
            before = evidence["before"]
            if (evidence_backend.EVIDENCE_PROVIDER == "real"
                    and rca["affected_component"] != service + ":" + before["version"]):
                invalidate_diagnosis(incident)
                raise IntegrationError("Deployment version changed after RCA; diagnose again", status_code=409)
            context = evidence.get("kubernetes") or {}
            captured_replicas = context.get("desired_replicas", before.get("metrics", {}).get("replica_count"))
            if (isinstance(captured_replicas, bool) or not isinstance(captured_replicas, int) or captured_replicas < 1
                    or not before.get("version") or before["version"].lower() == "unknown"
                    or context.get("version", before["version"]) != before["version"]
                    or (finops is not None and finops["current_replicas"] != captured_replicas)):
                invalidate_diagnosis(incident)
                raise IntegrationError("Captured deployment state changed during diagnosis; diagnose again", status_code=409)
            incident["_expected_state"] = {"version": before["version"], "replicas": captured_replicas}
            incident["recovery_scenario"] = evidence["scenario"]
        parameters = {}
        if result.action == DecisionAction.ROLLBACK:
            deployment = incident["_evidence"].get("deployment_event")
            if not deployment or deployment.get("service") != service:
                raise IntegrationError("Rollback requires captured deployment evidence", status_code=502, retryable=False)
            if rca["affected_component"] != service + ":" + deployment["new_version"]:
                invalidate_diagnosis(incident)
                raise IntegrationError("Deployment version changed after RCA; diagnose again", status_code=409)
            parameters = {"from_version": deployment["new_version"], "to_version": deployment["old_version"]}
        elif result.action == DecisionAction.SCALE:
            parameters = {"replicas": result.scale_option["replicas"]}
        if result.action != DecisionAction.ESCALATE and remediation_backend.REMEDIATION_BACKEND == "kubernetes":
            try:
                incident["_expected_state"] = remediation_backend.bind_live_preconditions(
                    service, incident["_expected_state"],
                    cpu_request_m=finops["current_cpu_request_m"] if finops is not None else None,
                    rollback_target=parameters.get("to_version"))
            except Exception as exc:
                invalidate_diagnosis(incident)
                raise IntegrationError(f"Live remediation preconditions could not be captured: {exc}", status_code=409) from exc
        proposal = DecisionProposal(incident_id=incident_id, recommended_action=result.action.value,
                                    target=service, parameters=parameters, confidence=result.confidence,
                                    risk="MEDIUM" if result.action == DecisionAction.ROLLBACK else "LOW" if result.action == DecisionAction.SCALE else "HIGH",
                                    reason=result.reason, approval_required=result.approval_required)
        incident.update(root_cause=rca["root_cause"], proposed_action=result.action.value,
                        scale_option=result.scale_option, _proposal=proposal.model_dump(mode="json"),
                        _decision=result.model_dump(mode="json"))
        incident["_diagnosis_pending"] = False
        incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.ACTION_PROPOSED).value
        incident["status"] = transition(IncidentStatus(incident["status"]),
            IncidentStatus.ESCALATED if result.action == DecisionAction.ESCALATE else IncidentStatus.AWAITING_APPROVAL).value
        persist_incidents()
        add_audit_record(incident_id, "DECISION_PROPOSED", proposal.model_dump(mode="json"))
        if result.action == DecisionAction.ESCALATE:
            enqueue_archive(incident)
        return result


def execute_approved_action(incident_id: str, action, approved: bool, replicas: int | None = None, approver: str = "operator"):
    with lock_for(incident_id):
        incident = find_incident(incident_id)
        if incident is None:
            raise ValueError("Incident not found")
        action = DecisionAction(action)
        if not approved:
            raise ValueError("Remediation cannot execute without approval")
        service = incident_service(incident)
        validate_service_allowed(service)
        validate_action_allowed(action)
        if action == DecisionAction.SCALE:
            validate_replica_count(replicas)
        proposed = incident.get("proposed_action")
        if proposed is not None and proposed != action.value:
            raise ValueError("Requested action does not match the approved proposal")
        if action == DecisionAction.SCALE and incident.get("scale_option") is not None:
            if incident["scale_option"].get("replicas") != replicas:
                raise ValueError("Replica count does not match the approved proposal")
        if remediation_backend.REMEDIATION_BACKEND != "mock" and not incident.get("_proposal"):
            raise ValueError("Real remediation requires a stored decision proposal")
        if incident_id in _pending_executions:
            pending = _finish_pending_execution(incident_id)
            if pending.get("failure"):
                raise ValueError(pending["failure"])
            return RemediationResult.model_validate(incident["_execution_result"])
        if incident.get("_execution_result") is not None:
            return RemediationResult.model_validate(incident["_execution_result"])
        if incident.get("_execution_intent") or incident.get("_execution_ambiguous"):
            raise IntegrationError("Execution outcome requires operator reconciliation; action will not be repeated",
                                   status_code=409, retryable=False)
        if incident["status"] != IncidentStatus.AWAITING_APPROVAL.value:
            raise IntegrationError("Incident is not awaiting approval", status_code=409, retryable=False)
        started_at = datetime.now(timezone.utc)
        action_id = "ACT-" + uuid4().hex
        add_audit_record(incident_id, "MANUAL_APPROVAL_GRANTED", {"approver": approver, "action": action.value,
                                                               "action_id": action_id})
        add_audit_record(incident_id, "ACTION_APPROVED", {"action": action.value, "replicas": replicas, "target": service})
        incident["_execution_intent"] = {"action_id": action_id, "incident_id": incident_id,
            "action": action.value, "replicas": replicas, "target": service,
            "started_at": started_at.isoformat(), "approver": approver, "status": "INTENT"}
        incident.setdefault("_provenance", provenance())
        # The durable intent and captured preconditions precede the external side effect.
        update_incident_status(incident_id, IncidentStatus.EXECUTING, approved=True)
        try:
            result = execute_remediation(action, approved=True, replicas=replicas, service=service,
                                         expected_state=incident.get("_expected_state"),
                                         rollback_target=incident.get("_proposal", {}).get("parameters", {}).get("to_version"))
            if not result.success:
                raise ValueError("Remediation reported failure")
        except Exception as exc:
            completed_at = datetime.now(timezone.utc)
            incident["_action_result"] = ActionResult(action_id=action_id, incident_id=incident_id,
                action=action.value, status="FAILED", started_at=started_at, completed_at=completed_at).model_dump(mode="json")
            incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.FAILED_REMEDIATION).value
            incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.ESCALATED).value
            event_type = ("ACTION_APPLIED_ROLLOUT_UNCONFIRMED"
                          if isinstance(exc, remediation_backend.RolloutUnconfirmed)
                          else "ACTION_EXECUTION_FAILED")
            _pending_executions[incident_id] = {"snapshot": deepcopy(incident), "committed": False,
                "event_type": event_type, "details": {"action": action.value, "message": str(exc)},
                "failure": f"Remediation failed: {exc}"}
            _finish_pending_execution(incident_id)
            raise ValueError("Remediation failed; see the audit trail for this incident") from exc
        completed_at = datetime.now(timezone.utc)
        incident["action_completed_at"] = completed_at.isoformat()
        incident["_execution_result"] = result.model_dump(mode="json")
        incident["_action_result"] = ActionResult(action_id=action_id, incident_id=incident_id,
            action=action.value, status="SUCCESS", started_at=started_at, completed_at=completed_at).model_dump(mode="json")
        incident["action_completed_at"] = incident["_action_result"]["completed_at"]
        incident["recovery_deadline_at"] = (completed_at + timedelta(seconds=RECOVERY_TIMEOUT_SECONDS)).isoformat()
        incident["status"] = transition(IncidentStatus(incident["status"]), IncidentStatus.VALIDATING).value
        _pending_executions[incident_id] = {"snapshot": deepcopy(incident), "committed": False,
            "event_type": "ACTION_EXECUTED", "details": deepcopy(incident["_action_result"])}
        _finish_pending_execution(incident_id)
        return result


def recovery_now():
    return datetime.now(timezone.utc)


def finish_recovery_timeout(incident, deadline):
    recovery = dict(incident.get("_last_recovery_measurement", {
        "incident_id": incident["incident_id"], "success": False, "source": "timeout",
    }), success=False, timed_out=True, deadline_at=deadline.isoformat())
    incident["_recovery"] = recovery
    add_audit_record(incident["incident_id"], "RECOVERY_TIMEOUT", recovery)
    return apply_recovery_result(incident["incident_id"], False)


def validate_and_apply_recovery(incident_id: str):
    with lock_for(incident_id):
        incident = find_incident(incident_id)
        if incident is None:
            raise ValueError("Incident not found")
        if incident["status"] in {IncidentStatus.RESOLVED.value, IncidentStatus.ESCALATED.value} and "_recovery" in incident:
            return incident
        if incident["status"] != IncidentStatus.VALIDATING.value:
            raise ValueError("Incident must be in VALIDATING state")
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
            completed_at + timedelta(seconds=RECOVERY_TIMEOUT_SECONDS)).isoformat()).replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            raise ValueError("Recovery deadline must include its timezone")
        incident["recovery_deadline_at"] = deadline.isoformat()
        if recovery_now() >= deadline:
            return finish_recovery_timeout(incident, deadline)
        try:
            recovery = validate_recovery(incident_id, scenario=scenario, action_completed_at=completion,
                                         service=incident_service(incident))
        except IntegrationError:
            if recovery_now() >= deadline:
                return finish_recovery_timeout(incident, deadline)
            raise
        if recovery.get("incident_id") != incident_id:
            raise IntegrationError("Recovery result belongs to another incident", status_code=502, retryable=False)
        if recovery["success"] is not True:
            incident.setdefault("_first_negative_recovery", recovery)
            incident["_last_recovery_measurement"] = recovery
            add_audit_record(incident_id, "RECOVERY_STABILIZING", recovery)
            persist_incidents()
        if recovery_now() >= deadline:
            return finish_recovery_timeout(incident, deadline)
        if recovery["success"] is not True:
            raise RecoveryPending(f"Measured recovery has not restored the SLO; keep polling until {deadline.isoformat()}")
        incident["_recovery"] = recovery
        incident["recovery_validated_at"] = recovery_now().isoformat()
        return apply_recovery_result(incident_id, True)

def apply_recovery_result(incident_id: str, success: bool):
    with lock_for(incident_id):
        return _apply_recovery_result(incident_id, success)


def _apply_recovery_result(incident_id: str, success: bool):
    incident = find_incident(incident_id)
    if incident is None:
        raise ValueError("Incident not found")
    if "_recovery" not in incident and recovery_provider.RECOVERY_PROVIDER == "mock":
        incident["_recovery"] = {"incident_id": incident_id, "success": success, "source": "mock"}
    add_audit_record(incident_id, "RECOVERY_VALIDATION", {"success": success})
    if success:
        res = update_incident_status(incident_id, IncidentStatus.RESOLVED, recovery_validated=True)
        enqueue_archive(incident)
        return res
    update_incident_status(incident_id, IncidentStatus.FAILED_REMEDIATION)
    res = update_incident_status(incident_id, IncidentStatus.ESCALATED)
    enqueue_archive(incident)
    return res

