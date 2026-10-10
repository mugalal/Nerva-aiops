import os
import math
from app.decision_engine.models import DecisionAction, DecisionResult

MIN_RCA_CONFIDENCE = float(os.getenv("MIN_RCA_CONFIDENCE", "0.60"))

def decide_action(root_cause: str) -> DecisionAction:
    return {"faulty_deployment": DecisionAction.ROLLBACK, "traffic_spike": DecisionAction.SCALE}.get(root_cause, DecisionAction.ESCALATE)


def choose_scale_option(finops: dict):
    current = finops.get("current_replicas")
    if isinstance(current, bool) or not isinstance(current, int) or current < 1:
        return None
    candidates = []
    for option in finops.get("temporary_scale_options", []):
        if not isinstance(option, dict):
            continue
        replicas, cost = option.get("replicas"), option.get("estimated_cost_delta")
        if (isinstance(replicas, int) and not isinstance(replicas, bool) and current < replicas <= 10
                and isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost)
                and cost >= 0 and option.get("risk") in {"LOW", "MEDIUM"}):
            candidates.append(option)
    if not candidates:
        return None
    low_risk = [c for c in candidates if c.get("risk") == "LOW"]
    return min(low_risk or candidates, key=lambda option: option["estimated_cost_delta"])


def decide_from_rca(rca: dict, finops: dict | None = None) -> DecisionResult:
    confidence = rca.get("confidence", 0.0)
    action = decide_action(rca.get("root_cause", "unknown"))
    if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
            or not math.isfinite(confidence) or not MIN_RCA_CONFIDENCE <= confidence <= 1):
        action = DecisionAction.ESCALATE
        confidence = confidence if isinstance(confidence, (int, float)) and math.isfinite(confidence) and 0 <= confidence <= 1 else 0.0
    scale_option = choose_scale_option(finops or {}) if action == DecisionAction.SCALE else None
    if action == DecisionAction.SCALE and scale_option is None:
        action = DecisionAction.ESCALATE
    reason = {DecisionAction.ROLLBACK: "Faulty deployment detected",
              DecisionAction.SCALE: "Traffic spike detected and a safe scale option is available",
              DecisionAction.ESCALATE: "No safe automated action available"}[action]
    return DecisionResult(action=action, reason=reason,
                          approval_required=action in {DecisionAction.ROLLBACK, DecisionAction.SCALE},
                          confidence=confidence, scale_option=scale_option)
