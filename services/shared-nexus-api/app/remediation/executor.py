import json
import hashlib
import os
import subprocess
from decimal import Decimal, DecimalException
import re
from threading import RLock

from app.decision_engine.models import DecisionAction
from app.remediation.guardrails import (
    validate_action_allowed, validate_namespace_allowed, validate_replica_count, validate_service_allowed,
)
from app.remediation.models import RemediationResult


REMEDIATION_BACKEND = os.getenv("REMEDIATION_BACKEND", "kubernetes").lower()
TARGET_SERVICE = "payment-service"
TARGET_NAMESPACE = "nexus-demo"
KUBECTL_CONTEXT = os.getenv("KUBECTL_CONTEXT", "")
KUBECTL_PATH = os.getenv("KUBECTL_PATH", "kubectl")
_service_locks = {}
_service_lock_guard = RLock()


def service_lock(service):
    with _service_lock_guard:
        return _service_locks.setdefault((TARGET_NAMESPACE, service), RLock())


def execute_remediation(action: DecisionAction, approved: bool, replicas: int | None = None,
                        service: str = TARGET_SERVICE, expected_state: dict | None = None,
                        rollback_target: str | None = None):
    if not approved:
        raise ValueError("Remediation cannot execute without approval")
    validate_service_allowed(service)
    validate_namespace_allowed(TARGET_NAMESPACE)
    validate_action_allowed(action)
    if action == DecisionAction.SCALE:
        validate_replica_count(replicas)
    with service_lock(service):
        if REMEDIATION_BACKEND == "mock":
            return RemediationResult(action=action, success=True, message="Mock remediation executed successfully")
        if REMEDIATION_BACKEND == "jenkins":
            raise ValueError("Jenkins remediation is disabled until its job enforces captured deployment preconditions")
        if REMEDIATION_BACKEND != "kubernetes":
            raise ValueError(f"Unsupported remediation backend: {REMEDIATION_BACKEND}")

        deployment = _validate_live_state(service, expected_state)
        resource_version = deployment["metadata"]["resourceVersion"]
        if action == DecisionAction.SCALE:
            current_replicas = deployment["spec"]["replicas"]
            if replicas <= current_replicas:
                raise ValueError("Scaling must increase the current live replica count")
            result = _run(["scale", f"deployment/{service}", f"--replicas={replicas}",
                           f"--current-replicas={current_replicas}", f"--resource-version={resource_version}",
                           "-n", TARGET_NAMESPACE], timeout=120)
            message = result.stdout.strip()
        else:
            revision, template = _rollback_template(service, deployment, rollback_target)
            if (revision != expected_state.get("rollback_revision")
                    or _template_fingerprint(template) != expected_state.get("rollback_template_fingerprint")):
                raise ValueError("Captured rollback revision changed; diagnose again")
            # The resourceVersion test atomically rejects changes after the live-state read.
            patch = [{"op": "test", "path": "/metadata/resourceVersion", "value": resource_version},
                     {"op": "replace", "path": "/spec/template", "value": template}]
            result = _run(["patch", f"deployment/{service}", "--type=json", "--patch", json.dumps(patch),
                           "-n", TARGET_NAMESPACE], timeout=120)
            message = f"Restored captured version {rollback_target} from revision {revision}: {result.stdout.strip()}"
        _wait_for_rollout(service)
        return RemediationResult(action=action, success=True, message=message)


def bind_live_preconditions(service, expected_state, cpu_request_m=None, rollback_target=None):
    """Pin actual deployment identity and template immediately before proposing a real action."""
    with service_lock(service):
        deployment = _validate_live_state(service, expected_state, require_binding=False)
        bound = dict(expected_state, uid=deployment["metadata"]["uid"],
                     template_fingerprint=_template_fingerprint(deployment["spec"]["template"]))
        if cpu_request_m is not None:
            if _cpu_request_m(deployment, service) != cpu_request_m:
                raise ValueError("Application CPU request changed after FinOps; diagnose again")
            bound["cpu_request_m"] = cpu_request_m
        if rollback_target is not None:
            revision, template = _rollback_template(service, deployment, rollback_target)
            bound.update(rollback_revision=revision, rollback_template_fingerprint=_template_fingerprint(template))
        return bound


def _template_fingerprint(template):
    return hashlib.sha256(json.dumps(template, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _cpu_request_m(deployment, service):
    try:
        containers = deployment["spec"]["template"]["spec"]["containers"]
        matches = [container for container in containers if container.get("name") == service]
        if len(matches) == 1:
            container = matches[0]
        elif len(containers) == 1:
            container = containers[0]
        else:
            raise ValueError("Application container is ambiguous")
        quantity = str(container["resources"]["requests"]["cpu"])
        if not re.fullmatch(r"[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?m?", quantity):
            raise ValueError("Invalid CPU request")
        amount = Decimal(quantity[:-1]) if quantity.endswith("m") else Decimal(quantity) * 1000
        if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
            raise ValueError("Invalid CPU request precision")
        return int(amount)
    except (KeyError, TypeError, ValueError, DecimalException) as exc:
        raise ValueError("Live application CPU request is unavailable or invalid") from exc


def _validate_live_state(service, expected_state, require_binding=True):
    if not isinstance(expected_state, dict):
        raise ValueError("Real remediation requires captured deployment preconditions")
    version, replicas = expected_state.get("version"), expected_state.get("replicas")
    if (not isinstance(version, str) or not version or version.lower() == "unknown"
            or isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 1):
        raise ValueError("Captured deployment preconditions are invalid")
    if require_binding and (not expected_state.get("uid") or not expected_state.get("template_fingerprint")):
        raise ValueError("Real remediation requires captured deployment identity and template")
    deployment = _get_json(["get", f"deployment/{service}", "-n", TARGET_NAMESPACE, "-o", "json"])
    metadata, spec, status = deployment.get("metadata", {}), deployment.get("spec", {}), deployment.get("status", {})
    if not metadata.get("uid") or not metadata.get("resourceVersion"):
        raise ValueError("Live deployment identity/resourceVersion is missing")
    if _template_version(spec.get("template", {})) != version or spec.get("replicas") != replicas:
        raise ValueError("Deployment changed since the approved proposal; diagnose again")
    if require_binding and (metadata["uid"] != expected_state["uid"]
                            or _template_fingerprint(spec["template"]) != expected_state["template_fingerprint"]):
        raise ValueError("Deployment identity or template changed since the approved proposal; diagnose again")
    generation = metadata.get("generation", 0)
    if (not isinstance(generation, int) or generation <= 0 or status.get("observedGeneration", 0) < generation
            or status.get("replicas", 0) != replicas or status.get("updatedReplicas", 0) < replicas
            or status.get("readyReplicas", 0) < replicas or status.get("availableReplicas", 0) < replicas):
        raise ValueError("Live deployment rollout is incomplete; diagnose again after it finishes")
    return deployment


def _template_version(template):
    labels = template.get("metadata", {}).get("labels", {}) or {}
    for label in ("version", "app.kubernetes.io/version"):
        if labels.get(label):
            return str(labels[label])
    containers = template.get("spec", {}).get("containers", []) or []
    if not containers:
        return None
    for environment in containers[0].get("env", []) or []:
        if environment.get("name") == "VERSION" and "value" in environment:
            return str(environment["value"])
    image = str(containers[0].get("image", ""))
    tail = image.rsplit("/", 1)[-1]
    return tail.rsplit(":", 1)[1] if ":" in tail and "@" not in tail else None


def _rollback_template(service, deployment, target):
    if not isinstance(target, str) or not target or target.lower() == "unknown":
        raise ValueError("Rollback requires the captured target version")
    if target == _template_version(deployment["spec"]["template"]):
        raise ValueError("Rollback target already matches the live version")
    try:
        current_revision = int(deployment["metadata"]["annotations"]["deployment.kubernetes.io/revision"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Live deployment revision is missing") from exc
    replica_sets = _get_json(["get", "replicasets", "-n", TARGET_NAMESPACE, "-o", "json"])
    candidates = []
    for replica_set in replica_sets.get("items", []):
        metadata = replica_set.get("metadata", {})
        if not any(owner.get("kind") == "Deployment" and owner.get("controller") is True
                   and owner.get("uid") == deployment["metadata"]["uid"]
                   for owner in metadata.get("ownerReferences", [])):
            continue
        template = replica_set.get("spec", {}).get("template", {})
        if _template_version(template) != target:
            continue
        try:
            revision = int(metadata["annotations"]["deployment.kubernetes.io/revision"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < revision < current_revision:
            candidates.append((revision, template))
    if not candidates:
        raise ValueError("No owned rollout revision matches the captured rollback target")
    revision, template = max(candidates, key=lambda candidate: candidate[0])
    template = json.loads(json.dumps(template))
    template.get("metadata", {}).get("labels", {}).pop("pod-template-hash", None)
    return revision, template


def _get_json(arguments):
    completed = _run(arguments, timeout=30)
    try:
        data = json.loads(completed.stdout)
        if not isinstance(data, dict):
            raise ValueError("Expected Kubernetes object")
        return data
    except (ValueError, TypeError) as exc:
        raise ValueError("kubectl returned invalid deployment data") from exc


def _run(arguments, timeout):
    completed = subprocess.run([*_kubectl_command(), *arguments], capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "Kubernetes command failed")
    return completed


def _wait_for_rollout(service: str):
    _run(["rollout", "status", f"deployment/{service}", "-n", TARGET_NAMESPACE, "--timeout=120s"], timeout=130)


def _kubectl_command():
    return [KUBECTL_PATH, "--context", KUBECTL_CONTEXT] if KUBECTL_CONTEXT else [KUBECTL_PATH]
