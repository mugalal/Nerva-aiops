import asyncio
from datetime import datetime, timezone
import json
import subprocess
from typing import Any

from ..errors import ProviderInvalidResponse, ProviderUnavailable
from ..models import DeploymentEvent, KubernetesContext


def _pod_version(pod: dict[str, Any]) -> str | None:
    """Version on a pod/template survives rollback, unlike deployment annotations."""
    labels = pod.get("metadata", {}).get("labels", {}) or {}
    for name in ("version", "app.kubernetes.io/version"):
        if labels.get(name):
            return str(labels[name])
    containers = pod.get("spec", {}).get("containers", []) or []
    if not containers:
        return None
    for environment in containers[0].get("env", []) or []:
        if environment.get("name") == "VERSION" and "value" in environment:
            return str(environment["value"])
    image = str(containers[0].get("image", ""))
    tail = image.rsplit("/", 1)[-1]
    return tail.rsplit(":", 1)[1] if ":" in tail and "@" not in tail else None


def _deployment_version(deployment: dict[str, Any]) -> str | None:
    return _pod_version(deployment.get("spec", {}).get("template", {}))


def _rollout_ready(deployment: dict[str, Any]) -> bool:
    status = deployment.get("status", {})
    generation = int(deployment.get("metadata", {}).get("generation", 0) or 0)
    desired = int(deployment.get("spec", {}).get("replicas", 0) or 0)
    return (
        generation > 0
        and int(status.get("observedGeneration", 0) or 0) >= generation
        and desired > 0
        and int(status.get("replicas", 0) or 0) == desired
        and int(status.get("updatedReplicas", 0) or 0) >= desired
        and int(status.get("readyReplicas", 0) or 0) >= desired
        and int(status.get("availableReplicas", 0) or 0) >= desired
    )


def _context_version(deployment: dict[str, Any], pods: dict[str, Any]) -> str:
    versions = set()
    for pod in pods.get("items", []) or []:
        status = pod.get("status", {})
        if pod.get("metadata", {}).get("deletionTimestamp"):
            continue
        if status.get("phase", "Running") != "Running":
            continue
        conditions = status.get("conditions", []) or []
        if conditions and not any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in conditions
        ):
            continue
        version = _pod_version(pod)
        if version:
            versions.add(version)
    if len(versions) == 1:
        return next(iter(versions))
    return "unknown" if versions else _deployment_version(deployment) or "unknown"


def _build_deployment_event(
    deployment: dict[str, Any], replica_sets: dict[str, Any], service: str
) -> DeploymentEvent | None:
    metadata = deployment.get("metadata", {})
    annotations = metadata.get("annotations", {}) or {}
    template_annotations = (
        deployment.get("spec", {}).get("template", {}).get("metadata", {})
        .get("annotations", {}) or {}
    )
    provenance_keys = (
        "nexus.io/previous-version", "nexus.io/version",
        "nexus.io/commit-sha", "nexus.io/pipeline-id",
    )
    # Do not fill gaps in template provenance using a different release's
    # deployment annotations. A partially annotated template has no full event.
    provenance = (
        template_annotations if any(key in template_annotations for key in provenance_keys)
        else annotations
    )
    required = {
        "old_version": provenance.get("nexus.io/previous-version"),
        "new_version": provenance.get("nexus.io/version"),
        "commit_sha": provenance.get("nexus.io/commit-sha"),
        "pipeline_id": provenance.get("nexus.io/pipeline-id"),
    }
    version = _deployment_version(deployment)
    revision = annotations.get("deployment.kubernetes.io/revision")
    uid = metadata.get("uid")
    if not all(required.values()) or not uid or not revision or version != required["new_version"]:
        # Deployment-level release annotations can remain after undo restores
        # a previous template. Never assign their provenance to that rollback.
        return None
    timestamps = []
    for replica_set in replica_sets.get("items", []) or []:
        rs_metadata = replica_set.get("metadata", {})
        if not any(
            owner.get("uid") == uid and owner.get("kind") == "Deployment"
            and owner.get("controller") is True
            for owner in rs_metadata.get("ownerReferences", []) or []
        ):
            continue
        if str((rs_metadata.get("annotations", {}) or {}).get(
            "deployment.kubernetes.io/revision", ""
        )) != str(revision) or _deployment_version(replica_set) != version:
            continue
        timestamp = rs_metadata.get("creationTimestamp")
        if not timestamp:
            continue
        try:
            parsed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is not None and parsed <= datetime.now(timezone.utc):
            timestamps.append(parsed)
    if not timestamps:
        return None
    # Conditions can be updated by scaling/availability changes. ReplicaSet
    # creation is the timestamp tied to this rollout, and never invented now.
    return DeploymentEvent(
        event_id=f"DEP-{revision}", service=service,
        old_version=str(required["old_version"]), new_version=str(required["new_version"]),
        commit_sha=str(required["commit_sha"]), pipeline_id=str(required["pipeline_id"]),
        timestamp=max(timestamps),
        status="SUCCESS" if _rollout_ready(deployment) else "IN_PROGRESS",
    )


class KubectlKubernetesClient:
    name = "kubernetes"

    def __init__(self, kubectl_path: str = "kubectl", timeout: float = 5.0):
        self.kubectl_path = kubectl_path
        self.timeout = timeout

    def _run_json(self, *args: str) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                [self.kubectl_path, *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return json.loads(completed.stdout)
        except FileNotFoundError as exc:
            raise ProviderUnavailable(self.name, "kubectl is not installed") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderUnavailable(self.name, "kubectl request timed out") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "kubectl failed").strip()
            raise ProviderUnavailable(self.name, detail) from exc
        except json.JSONDecodeError as exc:
            raise ProviderInvalidResponse(self.name, "kubectl returned invalid JSON") from exc

    async def ready(self) -> tuple[bool, str]:
        try:
            await asyncio.to_thread(self._run_json, "get", "namespace", "-o", "json")
            return True, "Kubernetes API reachable through kubectl"
        except (ProviderUnavailable, ProviderInvalidResponse) as exc:
            return False, exc.message

    async def context(self, service: str, namespace: str) -> KubernetesContext:
        deployment, pods, events = await asyncio.gather(
            asyncio.to_thread(
                self._run_json,
                "get",
                "deployment",
                service,
                "-n",
                namespace,
                "-o",
                "json",
            ),
            asyncio.to_thread(
                self._run_json,
                "get",
                "pods",
                "-l",
                f"app={service}",
                "-n",
                namespace,
                "-o",
                "json",
            ),
            asyncio.to_thread(
                self._run_json,
                "get",
                "events",
                "-n",
                namespace,
                "-o",
                "json",
            ),
        )

        desired = int(deployment.get("spec", {}).get("replicas", 0) or 0)
        ready = int(deployment.get("status", {}).get("readyReplicas", 0) or 0)
        version = _context_version(deployment, pods)
        pod_restarts = 0
        for pod in pods.get("items", []):
            for container_status in pod.get("status", {}).get("containerStatuses", []) or []:
                pod_restarts += int(container_status.get("restartCount", 0) or 0)

        event_lines = []
        for event in events.get("items", []):
            involved = str(event.get("involvedObject", {}).get("name", ""))
            message = str(event.get("message", ""))
            if service in involved or service in message:
                reason = str(event.get("reason", "Event"))
                event_lines.append(f"{reason}: {message}")

        if _rollout_ready(deployment) and version == _deployment_version(deployment):
            health = "ok"
        elif ready > 0:
            health = "degraded"
        else:
            health = "unavailable"

        return KubernetesContext(
            service=service,
            namespace=namespace,
            desired_replicas=desired,
            ready_replicas=ready,
            pod_restarts=pod_restarts,
            version=version or "unknown",
            service_health=health,
            events=event_lines[-20:],
        )

    async def deployment_event(
        self, service: str, namespace: str
    ) -> DeploymentEvent | None:
        deployment = await asyncio.to_thread(
            self._run_json,
            "get",
            "deployment",
            service,
            "-n",
            namespace,
            "-o",
            "json",
        )
        replica_sets = await asyncio.to_thread(
            self._run_json, "get", "replicasets", "-l", f"app={service}",
            "-n", namespace, "-o", "json",
        )
        return _build_deployment_event(deployment, replica_sets, service)

    @staticmethod
    def _deployment_version(deployment: dict[str, Any]) -> str | None:
        return _deployment_version(deployment)
