from app.remediation.executor import execute_remediation
from app.decision_engine.models import DecisionAction


def test_execute_rollback_with_approval():
    result = execute_remediation(
        DecisionAction.ROLLBACK,
        approved=True
    )

    assert result.action == DecisionAction.ROLLBACK
    assert result.success is True
    
import pytest


def test_execute_rollback_without_approval_fails():
    with pytest.raises(ValueError):
        execute_remediation(
            DecisionAction.ROLLBACK,
            approved=False
        )
        
def test_execute_scale_with_approval():
    result = execute_remediation(
        DecisionAction.SCALE,
        approved=True,
        replicas=3
    )

    assert result.action == DecisionAction.SCALE
    assert result.success is True
    
def test_execute_escalate_is_blocked():
    with pytest.raises(ValueError):
        execute_remediation(
            DecisionAction.ESCALATE,
            approved=True
        )