from app.decision_engine.models import DecisionAction

try:
    from shared.registry import get_registry
except ImportError:
    get_registry = None


ALLOWED_ACTIONS = {
    DecisionAction.ROLLBACK,
    DecisionAction.SCALE,
}
ALLOWED_SERVICES = {"payment-service"}
ALLOWED_NAMESPACES = {"nexus-demo"}
MIN_REPLICAS = 1
MAX_REPLICAS = 10


def validate_action_allowed(action: DecisionAction, service: str | None = None):
    if action not in ALLOWED_ACTIONS:
        raise ValueError(
            f"Action {action} is not allowed for automated remediation"
        )
    if get_registry is not None and service is not None:
        spec = get_registry().get(service)
        if spec is not None:
            allowed = {a.upper() for a in spec.remediation.actions}
            action_name = action.value.upper() if hasattr(action, "value") else str(action).upper()
            if action_name not in allowed:
                raise ValueError(
                    f"Action {action} is not allowed for service {service}"
                )


def validate_replica_count(replicas: int, service: str | None = None):
    min_rep, max_rep = MIN_REPLICAS, MAX_REPLICAS
    if get_registry is not None:
        min_rep, max_rep = get_registry().replica_bounds(service)
    if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < min_rep or replicas > max_rep:
        raise ValueError(
            f"Replica count must be between {min_rep} and {max_rep}"
        )


def validate_service_allowed(service: str):
    allowed = get_registry().allowed_services() if get_registry is not None else ALLOWED_SERVICES
    if service not in allowed:
        raise ValueError(f"Service {service} is not allowed for remediation")


def validate_namespace_allowed(namespace: str):
    allowed = get_registry().allowed_namespaces() if get_registry is not None else ALLOWED_NAMESPACES
    if namespace not in allowed:
        raise ValueError(f"Namespace {namespace} is not allowed for remediation")

