import re

from .models import SearchRequest
from .retrieval import search

INSUFFICIENT = "NEXUS does not have enough recorded evidence."
QUESTIONS = ["What happened?", "What changed before the incident?", "What is the root cause?",
             "Have we seen this before?", "What action was taken?", "Did it work?"]


def intent(question):
    q = re.sub(r"[^a-z0-9 ]", "", question.casefold())
    if any(phrase in q for phrase in ("solve the incident", "resolve the incident", "fix the incident",
                                     "solve this incident", "resolve this incident", "fix this incident",
                                     "what can i do", "how can i fix", "how do i fix")):
        return "resolution"
    if any(word in q for word in ("seen this", "similar", "before like", "previous incident")):
        return "similar"
    if any(word in q for word in ("what changed", "change before", "deployment before")):
        return "changed"
    if any(word in q for word in ("root cause", "why did", "why was")):
        return "cause"
    if any(word in q for word in ("did it work", "recovered", "recovery", "successful")):
        return "outcome"
    if any(word in q for word in ("action", "treatment", "remediation")):
        return "action"
    if any(word in q for word in ("what happened", "describe", "summarize")):
        return "happened"
    return None


def answer(repository, request):
    response = {"status": "insufficient_evidence", "source": request.source,
                "mode": "deterministic_grounded", "answer": INSUFFICIENT,
                "citations": [], "similar_incidents": [], "supported_questions": QUESTIONS}
    kind = intent(request.question)
    if kind is None:
        response.update(status="unsupported_question", answer="This Copilot supports the six operational questions listed below.")
        return response
    record = repository.get(request.incident_id, request.source) if request.incident_id else None
    if request.incident_id and not record:
        return response
    if not record and request.search:
        query = request.search.model_copy(update={"source": request.source})
        matches = search(repository, query)["results"]
        record = matches[0]["record"] if matches else None
    if not record:
        return response
    memory, context = record["memory"], record["context"]
    citations = response["citations"]

    def cite(path, value, rec=record):
        citations.append({"incident_id": rec["memory"]["incident_id"], "source": rec["source"], "field": path, "value": value})
        return value

    text = []
    if kind == "happened":
        text.append(f"Incident {cite('memory.incident_id', memory['incident_id'])} affected {cite('memory.service', memory['service'])}; recorded type: {cite('memory.incident_type', memory['incident_type'])}.")
        if context.get("anomaly"):
            text.append(f"Recorded anomaly features: {cite('context.anomaly.features', context['anomaly'].get('features', {}))}.")
    elif kind == "cause":
        text.append(f"Recorded root cause: {cite('memory.root_cause', memory['root_cause'])}.")
        if (context.get("rca") or {}).get("evidence"):
            text.append(f"Recorded evidence: {cite('context.rca.evidence', context['rca']['evidence'])}.")
    elif kind == "changed":
        event = context.get("deployment_event")
        incident = context.get("incident")
        # Only claim 'before' if both timestamps establish that ordering.
        if event and incident and event.get("timestamp") and incident.get("started_at"):
            from datetime import datetime
            try:
                before = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")) < datetime.fromisoformat(incident["started_at"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                before = False
            if before:
                text.append(f"Recorded deployment changed {cite('context.deployment_event.old_version', event.get('old_version'))} to {cite('context.deployment_event.new_version', event.get('new_version'))} at {cite('context.deployment_event.timestamp', event['timestamp'])}, before the incident started at {cite('context.incident.started_at', incident['started_at'])}.")
    elif kind == "action":
        text.append(f"Recorded action: {cite('memory.action', memory['action'])}.")
        if context.get("action_result"):
            text.append(f"Execution status: {cite('context.action_result.status', context['action_result'].get('status'))}.")
    elif kind == "resolution":
        text.append(f"Recorded resolution for {cite('memory.incident_id', memory['incident_id'])} on {cite('memory.service', memory['service'])}: cause {cite('memory.root_cause', memory['root_cause'])}; action {cite('memory.action', memory['action'])}.")
        text.append(f"Recorded action success: {str(cite('memory.action_success', memory['action_success'])).lower()}; recorded recovery: {str(cite('memory.recovered', memory['recovered'])).lower()}.")
        text.append("This is historical evidence, not a check of current service health. Compare the current symptoms with this record before choosing a response. Any new service action requires review and M4 approval; no action was executed by this chat.")
    elif kind == "outcome":
        succeeded = cite("memory.action_success", memory["action_success"])
        recovered = cite("memory.recovered", memory["recovered"])
        text.append(f"Recorded action success: {str(succeeded).lower()}. Recorded recovery: {str(recovered).lower()}.")
        text.append("Action execution and service recovery are separate outcomes.")
        if context.get("recovery_result"):
            text.append(f"Recovery measurements: {cite('context.recovery_result', context['recovery_result'])}.")
    elif kind == "similar":
        query = SearchRequest(incident_type=memory["incident_type"], service=memory["service"],
                              tags=memory["tags"], exclude_incident_id=memory["incident_id"], source=request.source)
        matches = search(repository, query)["results"]
        response["similar_incidents"] = matches
        if not matches:
            response.update(status="no_similar_incident", answer="No similar incident is recorded for this type and service.")
            return response
        for match in matches:
            other = match["record"]
            cite("memory", other["memory"], other)
            text.append(f"Similar recorded incident: {other['memory']['incident_id']}; cause: {other['memory']['root_cause']}; action: {other['memory']['action']}; recovered: {str(other['memory']['recovered']).lower()}.")
    if text:
        response.update(status="ok", answer=" ".join(text))
    return response
