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


def test_multi_service_guardrails():
    from app.remediation.guardrails import validate_service_allowed, validate_namespace_allowed

    # Allowed services in registry
    validate_service_allowed("payment-service")
    validate_service_allowed("cart-service")
    validate_service_allowed("frontend")

    with pytest.raises(ValueError, match="not allowed for remediation"):
        validate_service_allowed("unknown-service")

    # Allowed namespaces
    validate_namespace_allowed("nexus-demo")
    with pytest.raises(ValueError, match="not allowed for remediation"):
        validate_namespace_allowed("kube-system")

    # Service-specific actions (cart-service only allows SCALE)
    validate_action_allowed(DecisionAction.SCALE, service="cart-service")
    with pytest.raises(ValueError, match="Action .* is not allowed for service cart-service"):
        validate_action_allowed(DecisionAction.ROLLBACK, service="cart-service")

    # Service-specific replica limits (cart-service max is 5)
    validate_replica_count(5, service="cart-service")
    with pytest.raises(ValueError, match="Replica count must be between 1 and 5"):
        validate_replica_count(6, service="cart-service")

