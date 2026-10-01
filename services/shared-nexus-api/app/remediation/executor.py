from app.decision_engine.models import DecisionAction


def execute_remediation(action: DecisionAction, approved: bool):
    if not approved:
        raise ValueError("Remediation cannot execute without approval")

    if action == DecisionAction.ROLLBACK:
        return {
            "action": "ROLLBACK",
            "success": True,
            "message": "Mock rollback executed successfully"
        }
    if action == DecisionAction.SCALE:
         return {
            "action": "SCALE",
            "success": True,
            "message": "Mock scale executed successfully"
    }
    raise ValueError(f"Unsupported remediation action: {action}")
