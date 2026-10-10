from app.decision_engine.models import DecisionResult
from app.decision_engine.engine import DecisionAction


def test_decision_result_model():
    result = DecisionResult(
        action=DecisionAction.ROLLBACK,
        reason="Faulty deployment detected",
        approval_required=True,
        confidence=0.92
    )

    assert result.action == DecisionAction.ROLLBACK
    assert result.approval_required is True
    assert result.confidence == 0.92
