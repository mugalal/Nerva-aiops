import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ..errors import ProviderInvalidResponse, ProviderUnavailable
from ..models import DeploymentEvent, KubernetesContext
from .kubernetes import _build_deployment_event, _context_version, _deployment_version, _rollout_ready
from .resources import application_resources


class InClusterKubernetesClient:
    """Read-only Kubernetes evidence through the pod service account."""

    name = "kubernetes"

    def __init__(
        self,
        base_url: str,
        token_path: Path,
        ca_path: Path,
        timeout: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.token_path = token_path
        self.ca_path = ca_path
        self.timeout = timeout

    async def _get_json(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not self.token_path.exists():
            raise ProviderUnavailable(
                self.name, f"service-account token is missing: {self.token_path}"
            )
        if not self.ca_path.exists():
            raise ProviderUnavailable(
                self.name, f"cluster CA certificate is missing: {self.ca_path}"
            )

        token = self.token_path.read_text(encoding="utf-8").strip()
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                verify=str(self.ca_path),
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Kubernetes response is not a JSON object")
                return payload
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable(self.name, f"request timed out: {url}") from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text.strip()[:300]
            raise ProviderUnavailable(
                self.name,
                f"HTTP {exc.response.status_code} from {url}: {detail}",
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailable(self.name, str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise ProviderInvalidResponse(self.name, str(exc)) from exc

    async def ready(self) -> tuple[bool, str]:
        try:
            await self._get_json("/version")
            return True, "Kubernetes API reachable with the pod service account"
        except (ProviderUnavailable, ProviderInvalidResponse) as exc:
            return False, exc.message

    async def context(self, service: str, namespace: str) -> KubernetesContext:
        safe_namespace = quote(namespace, safe="")
        safe_service = quote(service, safe="")
        deployment_path = (
            f"/apis/apps/v1/namespaces/{safe_namespace}/deployments/{safe_service}"
        )
        pods_path = f"/api/v1/namespaces/{safe_namespace}/pods"
        events_path = f"/api/v1/namespaces/{safe_namespace}/events"
        deployment, pods, events = await asyncio.gather(
            self._get_json(deployment_path),
            self._get_json(pods_path, {"labelSelector": f"app={service}"}),
            self._get_json(events_path),
        )

        desired = int(deployment.get("spec", {}).get("replicas", 0) or 0)
        ready = int(deployment.get("status", {}).get("readyReplicas", 0) or 0)
        version = _context_version(deployment, pods)
        pod_restarts = sum(
            int(container_status.get("restartCount", 0) or 0)
            for pod in pods.get("items", [])
            for container_status in (
                pod.get("status", {}).get("containerStatuses", []) or []
            )
        )

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
        safe_namespace = quote(namespace, safe="")
        safe_service = quote(service, safe="")
        deployment = await self._get_json(
            f"/apis/apps/v1/namespaces/{safe_namespace}/deployments/{safe_service}"
        )
        replica_sets = await self._get_json(
            f"/apis/apps/v1/namespaces/{safe_namespace}/replicasets",
            {"labelSelector": f"app={service}"},
        )
        return _build_deployment_event(deployment, replica_sets, service)

    async def resource_config(self, service: str, namespace: str):
        deployment = await self._get_json(
            f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/deployments/{quote(service, safe='')}"
        )
        if not _rollout_ready(deployment):
            raise ProviderUnavailable(self.name, "Application rollout is not complete")
        return application_resources(deployment, service)

    @staticmethod
    def _deployment_version(deployment: dict[str, Any]) -> str | None:
        return _deployment_version(deployment)
