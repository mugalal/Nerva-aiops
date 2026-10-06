import pytest

from app.remediation.guardrails import validate_action_allowed
from app.decision_engine.models import DecisionAction
from app.remediation.guardrails import (
    validate_action_allowed,
    validate_replica_count,
)
from app.remediation.guardrails import (
    validate_action_allowed,
    validate_replica_count,
)


def test_rollback_is_allowed():
    validate_action_allowed(DecisionAction.ROLLBACK)


def test_scale_is_allowed():
    validate_action_allowed(DecisionAction.SCALE)


def test_escalate_is_blocked():
    with pytest.raises(ValueError):
        validate_action_allowed(DecisionAction.ESCALATE)
        
def test_valid_replica_count_is_allowed():
    validate_replica_count(3)


def test_replica_count_below_minimum_is_blocked():
    with pytest.raises(ValueError):
        validate_replica_count(0)


def test_replica_count_above_maximum_is_blocked():
    with pytest.raises(ValueError):
        validate_replica_count(100)
        
