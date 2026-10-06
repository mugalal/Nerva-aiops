from app.decision_engine.models import DecisionAction


ALLOWED_ACTIONS = {
    DecisionAction.ROLLBACK,
    DecisionAction.SCALE,
}
ALLOWED_SERVICES = {"payment-service"}
ALLOWED_NAMESPACES = {"nexus-demo"}

def validate_action_allowed(action: DecisionAction):
    if action not in ALLOWED_ACTIONS:
        raise ValueError(
            f"Action {action} is not allowed for automated remediation"
        )


MIN_REPLICAS = 1
MAX_REPLICAS = 10


def validate_replica_count(replicas: int):
    if replicas < MIN_REPLICAS or replicas > MAX_REPLICAS:
        raise ValueError(
            f"Replica count must be between {MIN_REPLICAS} and {MAX_REPLICAS}"
        )
        
def validate_service_allowed(service: str):
    if service not in ALLOWED_SERVICES:
        raise ValueError(f"Service {service} is not allowed for remediation")


def validate_namespace_allowed(namespace: str):
    if namespace not in ALLOWED_NAMESPACES:
        raise ValueError(f"Namespace {namespace} is not allowed for remediation")