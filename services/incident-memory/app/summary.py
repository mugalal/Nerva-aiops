"""Compact, lossless-by-field views of incident records for the Copilot.

M4's incident context grows with every recovery poll (the audit trail repeats
full before/after details) and carries raw log lines. Sent whole, a scale-out
incident exceeds the tool context limit and the model receives nothing. These
views keep every recorded decision, cost and recovery fact, and replace bulky
repeated or raw sections with explicit counts so nothing is silently dropped.
"""
from collections import Counter

COST_NOTE = ("estimated_cost_delta is M6's estimate in abstract cost units (CU) for the configured "
             "scale window (FINOPS_SCALE_DURATION_MINUTES, 30 minutes by default). It is not money "
             "and not a measured saving.")
RECENT_AUDIT_EVENTS = 6


def _pick(data, *keys):
    if not isinstance(data, dict):
        return None
    picked = {key: data[key] for key in keys if data.get(key) is not None}
    return picked or None


def finops_view(context):
    """M6 scale options with the option the decision chose, from any incident context."""
    finops = (context or {}).get("finops_context")
    if not isinstance(finops, dict):
        return None
    options = [option for option in finops.get("temporary_scale_options") or [] if isinstance(option, dict)]
    decision = (context or {}).get("decision") or {}
    target = (decision.get("parameters") or {}).get("replicas")
    chosen = next((option for option in options if option.get("replicas") == target), None) if target else None
    return {"service": finops.get("service"), "current_replicas": finops.get("current_replicas"),
            "observed_cpu_pct": finops.get("observed_cpu_pct"),
            "current_cpu_request_m": finops.get("current_cpu_request_m"),
            "options": options, "chosen_option": chosen, "cost_note": COST_NOTE}


def _evidence_view(evidence):
    if not isinstance(evidence, dict):
        return None
    baseline = evidence.get("baseline") or {}
    view = {"scenario": evidence.get("scenario"), "captured_at": evidence.get("captured_at"),
            "before": evidence.get("before"),
            "kubernetes": _pick(evidence.get("kubernetes"), "version", "desired_replicas", "ready_replicas",
                                "service_health"),
            "baseline": _pick(baseline, "version", "measured_at", "thresholds") if isinstance(baseline, dict) else None,
            "provider_errors": evidence.get("provider_errors") or None,
            "selected_log_lines": len(evidence.get("selected_logs") or [])}
    return {key: value for key, value in view.items() if value is not None}


def _audit_view(audit):
    if not isinstance(audit, list) or not audit:
        return None
    events = [event for event in audit if isinstance(event, dict)]
    counts = Counter(str(event.get("event_type")) for event in events)
    recent = [{"timestamp": event.get("timestamp"), "event_type": event.get("event_type")}
              for event in events[-RECENT_AUDIT_EVENTS:]]
    return {"total_events": len(events), "event_counts": dict(counts), "recent_events": recent,
            "notice": "Event details are omitted; decision, action and recovery facts are listed in their own sections."}


def incident_summary(bundle):
    """Compact view of an M4 incident context or an archived memory context."""
    if not isinstance(bundle, dict):
        return None
    recovery = bundle.get("recovery_result")
    validation = bundle.get("recovery_validation")
    summary = {
        "memory": bundle.get("memory"),
        "incident": _pick(bundle.get("incident"), "incident_id", "status", "severity", "affected_services",
                          "started_at", "root_cause", "proposed_action"),
        "source": bundle.get("source"),
        "provider_modes": bundle.get("provider_modes"),
        "anomaly": _pick(bundle.get("anomaly"), "anomaly_id", "timestamp", "score", "model", "features"),
        "linked_anomalies": len(bundle["anomalies"]) if isinstance(bundle.get("anomalies"), list) else None,
        "rca": _pick(bundle.get("rca"), "root_cause", "confidence", "affected_component", "evidence"),
        "decision": _pick(bundle.get("decision"), "recommended_action", "parameters", "risk", "reason",
                          "confidence", "approval_required"),
        "proposal_state": bundle.get("proposal_state"),
        "finops": finops_view(bundle),
        "deployment_event": bundle.get("deployment_event"),
        "evidence": _evidence_view(bundle.get("evidence")),
        "action_result": _pick(bundle.get("action_result"), "action", "status", "started_at", "completed_at",
                               "message", "error"),
        "recovery_result": recovery if isinstance(recovery, dict) else None,
        "recovery_validation": _pick(validation, "source", "success", "timed_out", "deadline_at"),
        "workflow": _pick(bundle.get("workflow"), "action_completed_at", "recovery_deadline_at",
                          "execution_ambiguous", "diagnosis_error", "recovery_error", "archive_status"),
        "missing": bundle.get("missing") or None,
        "audit": _audit_view(bundle.get("audit")),
    }
    return {key: value for key, value in summary.items() if value is not None}


def archived_summary(record):
    if not isinstance(record, dict):
        return None
    return {"memory": record.get("memory"), "resolved": record.get("resolved"), "stored_at": record.get("stored_at"),
            "context": incident_summary(record.get("context"))}
