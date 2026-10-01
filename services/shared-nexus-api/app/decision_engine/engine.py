from app.decision_engine.models import DecisionAction, DecisionResult
from app.decision_engine.models import DecisionResult


    
def decide_action(root_cause: str) -> DecisionAction:
    if root_cause == "faulty_deployment":
        return DecisionAction.ROLLBACK

    if root_cause == "traffic_spike":
        return DecisionAction.SCALE

    return DecisionAction.ESCALATE

def decide_from_rca(rca: dict, finops: dict | None = None) -> DecisionResult:
    action = decide_action(rca["root_cause"])
    scale_option = None
    if action == DecisionAction.SCALE:
        scale_option = choose_scale_option(finops or {})

        if scale_option is None:
            action = DecisionAction.ESCALATE
    if action == DecisionAction.ROLLBACK:
        reason = "Faulty deployment detected"

    elif action == DecisionAction.SCALE:
        reason = "Traffic spike detected and a safe scale option is available"

    else:
        reason = "No safe automated action available"

    return DecisionResult(
        action=action,
        reason=reason,
        approval_required=action in {
            DecisionAction.ROLLBACK,
            DecisionAction.SCALE
        },
        confidence=rca.get("confidence", 0.0),
        scale_option=scale_option
    )
def choose_scale_option(finops: dict):
    options = finops.get("temporary_scale_options", [])

    if not options:
        return None

    return min(
        options,
        key=lambda option: option["estimated_cost"]
    )
    
