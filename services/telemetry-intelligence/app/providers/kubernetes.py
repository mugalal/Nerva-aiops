import asyncio
from datetime import datetime, timezone
import json
import subprocess
from typing import Any

from ..errors import ProviderInvalidResponse, ProviderUnavailable
from ..models import DeploymentEvent, KubernetesContext


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

        metadata = deployment.get("metadata", {})
        annotations = metadata.get("annotations", {}) or {}
        desired = int(deployment.get("spec", {}).get("replicas", 0) or 0)
        ready = int(deployment.get("status", {}).get("readyReplicas", 0) or 0)
        version = annotations.get("nexus.io/version") or self._deployment_version(deployment)
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

        if desired > 0 and ready >= desired:
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
        metadata = deployment.get("metadata", {})
        annotations = metadata.get("annotations", {}) or {}
        required = {
            "old_version": annotations.get("nexus.io/previous-version"),
            "new_version": annotations.get("nexus.io/version"),
            "commit_sha": annotations.get("nexus.io/commit-sha"),
            "pipeline_id": annotations.get("nexus.io/pipeline-id"),
        }
        if not all(required.values()):
            return None

        status = deployment.get("status", {})
        desired = int(deployment.get("spec", {}).get("replicas", 0) or 0)
        ready = int(status.get("readyReplicas", 0) or 0)
        conditions = status.get("conditions", []) or []
        timestamps = [
            condition.get("lastUpdateTime")
            for condition in conditions
            if condition.get("lastUpdateTime")
        ]
        timestamp = max(timestamps) if timestamps else datetime.now(timezone.utc).isoformat()
        revision = annotations.get("deployment.kubernetes.io/revision", "unknown")
        return DeploymentEvent(
            event_id=f"DEP-{revision}",
            service=service,
            old_version=str(required["old_version"]),
            new_version=str(required["new_version"]),
            commit_sha=str(required["commit_sha"]),
            pipeline_id=str(required["pipeline_id"]),
            timestamp=timestamp,
            status="SUCCESS" if desired > 0 and ready >= desired else "IN_PROGRESS",
        )

    @staticmethod
    def _deployment_version(deployment: dict[str, Any]) -> str | None:
        containers = (
            deployment.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        if not containers:
            return None
        for environment in containers[0].get("env", []) or []:
            if environment.get("name") == "VERSION" and "value" in environment:
                return str(environment["value"])
        image = str(containers[0].get("image", ""))
        return image.rsplit(":", 1)[1] if ":" in image else None
