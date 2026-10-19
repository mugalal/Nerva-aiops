"""Remediation guardrails, all read from the one service registry (shared/registry.py)."""
from app.decision_engine.models import DecisionAction
from shared.registry import get_registry

# Action types M4 can actuate at all; the registry narrows this per service.
ALLOWED_ACTIONS = {DecisionAction.ROLLBACK, DecisionAction.SCALE}


def validate_action_allowed(action: DecisionAction, service: str | None = None):
    action = DecisionAction(action)
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"Action {action.value} is not allowed for automated remediation")
    if service is not None and action.value not in get_registry().allowed_actions(service):
        raise ValueError(f"Action {action.value} is not allowed for service {service}")


def validate_replica_count(replicas: int, service: str):
    min_rep, max_rep = get_registry().replica_bounds(service)
    if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < min_rep or replicas > max_rep:
        raise ValueError(f"Replica count must be between {min_rep} and {max_rep}")


def validate_scale_step(current: int, target: int, service: str):
    reason = get_registry().scale_allowed(service, current, target)
    if reason is not None:
        raise ValueError(f"Scale {current} -> {target} rejected: {reason}")


def validate_scale_down_step(current: int, target: int, service: str):
    reason = get_registry().scale_down_allowed(service, current, target)
    if reason is not None:
        raise ValueError(f"Scale-down {current} -> {target} rejected: {reason}")


def validate_service_allowed(service: str):
    if service not in get_registry().allowed_services():
        raise ValueError(f"Service {service} is not allowed for remediation")


def validate_namespace_allowed(namespace: str):
    if namespace not in get_registry().allowed_namespaces():
        raise ValueError(f"Namespace {namespace} is not allowed for remediation")
