"""Declarative Service Registry for multi-service NEXUS AIOps platform.

Provides service metadata, namespace, workload target, topology dependencies,
and remediation guardrails loaded from `config/services.yaml`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


class WorkloadSpec(BaseModel):
    kind: str = "Deployment"
    name: str


class RemediationConfig(BaseModel):
    actions: list[str] = Field(default_factory=lambda: ["ROLLBACK", "SCALE"])
    min_replicas: int = 1
    max_replicas: int = 10
    max_scale_step: int = 7
    approval: str = "always"


class ServiceSpec(BaseModel):
    name: str
    namespace: str = "nexus-demo"
    workload: WorkloadSpec
    tier: str = "critical"
    depends_on: list[str] = Field(default_factory=list)
    remediation: RemediationConfig = Field(default_factory=RemediationConfig)


class ServiceRegistry(BaseModel):
    version: int = 1
    services: list[ServiceSpec] = Field(default_factory=list)

    def get(self, service_name: str) -> ServiceSpec | None:
        for service in self.services:
            if service.name == service_name:
                return service
        return None

    def target_for(self, service_name: str) -> ServiceSpec:
        spec = self.get(service_name)
        if spec is None:
            raise ValueError(f"Service {service_name} is not allowed for remediation")
        return spec

    def allowed_services(self) -> set[str]:
        return {service.name for service in self.services}

    def allowed_namespaces(self) -> set[str]:
        return {service.namespace for service in self.services}

    def allowed_actions(self, service_name: str | None = None) -> set[str]:
        if service_name is not None:
            spec = self.get(service_name)
            if spec is not None:
                return {action.upper() for action in spec.remediation.actions}
        actions: set[str] = set()
        for service in self.services:
            actions.update(action.upper() for action in service.remediation.actions)
        return actions or {"ROLLBACK", "SCALE"}

    def replica_bounds(self, service_name: str | None = None) -> tuple[int, int]:
        if service_name is not None:
            spec = self.get(service_name)
            if spec is not None:
                return spec.remediation.min_replicas, spec.remediation.max_replicas
        mins = [s.remediation.min_replicas for s in self.services]
        maxs = [s.remediation.max_replicas for s in self.services]
        return min(mins, default=1), max(maxs, default=10)


def default_registry() -> ServiceRegistry:
    return ServiceRegistry(
        version=1,
        services=[
            ServiceSpec(
                name="payment-service",
                namespace="nexus-demo",
                workload=WorkloadSpec(kind="Deployment", name="payment-service"),
                tier="critical",
                depends_on=[],
                remediation=RemediationConfig(
                    actions=["ROLLBACK", "SCALE"],
                    min_replicas=1,
                    max_replicas=10,
                    max_scale_step=7,
                    approval="always",
                ),
            )
        ],
    )


_current_registry: ServiceRegistry | None = None


def load(path: str | Path | None = None) -> ServiceRegistry:
    """Load ServiceRegistry from file, or return default fallback if absent."""
    candidate_paths: list[Path] = []

    if path is not None:
        candidate_paths.append(Path(path))
    elif "NEXUS_SERVICES_CONFIG" in os.environ:
        candidate_paths.append(Path(os.environ["NEXUS_SERVICES_CONFIG"]))

    # Default discovery locations
    candidate_paths.extend([
        Path("config/services.yaml"),
        Path(__file__).resolve().parent.parent / "config" / "services.yaml",
        Path("/app/config/services.yaml"),
    ])

    for candidate in candidate_paths:
        try:
            if candidate.is_file() and yaml is not None:
                with candidate.open("r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict) and "services" in data:
                    return ServiceRegistry.model_validate(data)
        except Exception:
            continue

    return default_registry()


def get_registry() -> ServiceRegistry:
    global _current_registry
    if _current_registry is None:
        _current_registry = load()
    return _current_registry


def set_registry(registry: ServiceRegistry | None) -> None:
    global _current_registry
    _current_registry = registry
