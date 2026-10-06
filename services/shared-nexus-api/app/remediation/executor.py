import os
import subprocess

from app.decision_engine.models import DecisionAction
from app.remediation.guardrails import (
    validate_action_allowed,
    validate_namespace_allowed,
    validate_replica_count,
    validate_service_allowed,
)
from app.remediation.jenkins_executor import trigger_jenkins_remediation
from app.remediation.models import RemediationResult


REMEDIATION_BACKEND = os.getenv(
    "REMEDIATION_BACKEND",
    "kubernetes"
).lower()

TARGET_SERVICE = "payment-service"
TARGET_NAMESPACE = "nexus-demo"


def execute_remediation(
    action: DecisionAction,
    approved: bool,
    replicas: int | None = None
):
    if not approved:
        raise ValueError(
            "Remediation cannot execute without approval"
        )

    validate_service_allowed(TARGET_SERVICE)
    validate_namespace_allowed(TARGET_NAMESPACE)
    validate_action_allowed(action)

    if REMEDIATION_BACKEND not in {
        "mock",
        "jenkins",
        "kubernetes",
    }:
        raise ValueError(
            f"Unsupported remediation backend: {REMEDIATION_BACKEND}"
        )

    if action == DecisionAction.SCALE:
        if replicas is None:
            raise ValueError(
                "Replicas must be specified for scaling action"
            )

        validate_replica_count(replicas)

    if REMEDIATION_BACKEND == "mock":
        return RemediationResult(
            action=action,
            success=True,
            message="Mock remediation executed successfully",
        )

    if REMEDIATION_BACKEND == "jenkins":
        result = trigger_jenkins_remediation(
            action.value,
            replicas=replicas,
        )

        if not result["success"]:
            raise ValueError(
                f"Jenkins remediation failed: "
                f"{result.get('result')}"
            )

        return RemediationResult(
            action=action,
            success=True,
            message=(
                "Jenkins remediation completed successfully: "
                f"{result['build_url']}"
            ),
        )

    if action == DecisionAction.ROLLBACK:
        result = subprocess.run(
            [
                "kubectl",
                "rollout",
                "undo",
                f"deployment/{TARGET_SERVICE}",
                "-n",
                TARGET_NAMESPACE,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise ValueError(result.stderr.strip())

        return RemediationResult(
            action=DecisionAction.ROLLBACK,
            success=True,
            message=result.stdout.strip(),
        )

    if action == DecisionAction.SCALE:
        result = subprocess.run(
            [
                "kubectl",
                "scale",
                f"deployment/{TARGET_SERVICE}",
                f"--replicas={replicas}",
                "-n",
                TARGET_NAMESPACE,
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise ValueError(result.stderr.strip())

        return RemediationResult(
            action=DecisionAction.SCALE,
            success=True,
            message=result.stdout.strip(),
        )

    raise ValueError(
        f"Unsupported remediation action: {action}"
    )