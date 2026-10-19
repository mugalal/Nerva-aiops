"""Declarative service registry: the single remediation policy for M4 and M6.

Loading rules
-------------
* `NEXUS_SERVICES_CONFIG` set: that file MUST exist and be valid, otherwise
  startup fails with RegistryError. There is no silent fallback.
* Not set: the first existing file among `config/services.yaml` (relative to
  the working directory, the repository, or `/app/config`) is loaded and must
  be valid. Only when none exists is the built-in payment-service policy used,
  and /health reports that source.

Field semantics
---------------
Enforced:  name, namespace, workload, remediation.actions, min_replicas,
           max_replicas, max_scale_step, approval ("always" is the only policy),
           depends_on: M4 will not SCALE a service while a service it depends on
           has an open incident (it escalates instead: more callers would only
           add load to the struggling dependency).
Informational only (validated, shown to operators, not used for decisions):
           tier.

A registry entry grants remediation guardrails. It does not create telemetry:
only services that M1 actually scrapes and that run as a Kubernetes
Deployment can be diagnosed and actuated.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

try:
    import yaml
except ImportError:  # pragma: no cover - reported as RegistryError when a file must be read
    yaml = None  # type: ignore[assignment]

_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")
SUPPORTED_ACTIONS = {"ROLLBACK", "SCALE"}
ABSOLUTE_MAX_REPLICAS = 50


class RegistryError(RuntimeError):
    """The configured service registry is missing or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkloadSpec(_Strict):
    kind: Literal["Deployment"] = "Deployment"
    name: str

    @field_validator("name")
    @classmethod
    def dns_name(cls, value):
        if not _DNS_LABEL.match(value):
            raise ValueError("workload name must be a DNS-1123 label")
        return value


class RemediationConfig(_Strict):
    actions: list[str] = Field(default_factory=lambda: ["ROLLBACK", "SCALE"], min_length=1)
    min_replicas: int = Field(default=1, ge=1, strict=True)
    max_replicas: int = Field(default=10, ge=1, le=ABSOLUTE_MAX_REPLICAS, strict=True)
    max_scale_step: int = Field(default=7, ge=1, strict=True)
    # Right-sizing removes at most this many replicas per approved step.
    max_scale_down_step: int = Field(default=3, ge=1, strict=True)
    approval: Literal["always"] = "always"

    @field_validator("actions")
    @classmethod
    def known_actions(cls, value):
        normalized = [action.upper() for action in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("remediation actions must be unique")
        unknown = set(normalized) - SUPPORTED_ACTIONS
        if unknown:
            raise ValueError(f"unsupported remediation actions: {sorted(unknown)}")
        return normalized

    @model_validator(mode="after")
    def coherent_bounds(self):
        if self.min_replicas > self.max_replicas:
            raise ValueError("min_replicas must not exceed max_replicas")
        if self.max_scale_step > self.max_replicas - self.min_replicas and self.max_replicas > self.min_replicas:
            raise ValueError("max_scale_step cannot exceed max_replicas - min_replicas")
        return self


class ServiceSpec(_Strict):
    name: str
    namespace: str = "nexus-demo"
    workload: WorkloadSpec
    tier: Literal["critical", "standard", "best_effort"] = "critical"  # informational
    depends_on: list[str] = Field(default_factory=list)              # blocks scaling while a dependency is degraded
    remediation: RemediationConfig = Field(default_factory=RemediationConfig)

    @field_validator("name", "namespace")
    @classmethod
    def dns_label(cls, value):
        if not _DNS_LABEL.match(value):
            raise ValueError("service name and namespace must be DNS-1123 labels")
        return value


class ServiceRegistry(_Strict):
    version: Literal[1] = 1
    services: list[ServiceSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self):
        names = [service.name for service in self.services]
        if len(names) != len(set(names)):
            raise ValueError("service names must be unique")
        for service in self.services:
            if service.name in service.depends_on:
                raise ValueError(f"{service.name} cannot depend on itself")
            missing = set(service.depends_on) - set(names)
            if missing:
                raise ValueError(f"{service.name} depends on unregistered services {sorted(missing)}")
        return self

    def get(self, service_name: str) -> ServiceSpec | None:
        return next((service for service in self.services if service.name == service_name), None)

    def target_for(self, service_name: str) -> ServiceSpec:
        spec = self.get(service_name)
        if spec is None:
            raise ValueError(f"Service {service_name} is not allowed for remediation")
        return spec

    def allowed_services(self) -> set[str]:
        return {service.name for service in self.services}

    def allowed_namespaces(self) -> set[str]:
        return {service.namespace for service in self.services}

    def allowed_actions(self, service_name: str) -> set[str]:
        return set(self.target_for(service_name).remediation.actions)

    def replica_bounds(self, service_name: str) -> tuple[int, int]:
        policy = self.target_for(service_name).remediation
        return policy.min_replicas, policy.max_replicas

    def max_scale_step(self, service_name: str) -> int:
        return self.target_for(service_name).remediation.max_scale_step

    def scale_allowed(self, service_name: str, current: int, target: int) -> str | None:
        """None when `current -> target` is permitted; otherwise the reason it is not."""
        spec = self.get(service_name)
        if spec is None:
            return f"service {service_name} is not registered"
        policy = spec.remediation
        if "SCALE" not in policy.actions:
            return f"SCALE is not allowed for {service_name}"
        if not policy.min_replicas <= target <= policy.max_replicas:
            return f"replicas must be between {policy.min_replicas} and {policy.max_replicas}"
        if target <= current:
            return "scaling must increase the current replica count"
        if target - current > policy.max_scale_step:
            return f"a single scale step may add at most {policy.max_scale_step} replicas"
        return None

    def scale_down_allowed(self, service_name: str, current: int, target: int) -> str | None:
        """None when right-sizing `current -> target` is permitted; otherwise the reason it is not."""
        return _scale_down_reason(self.get(service_name), service_name, current, target)


def _scale_down_reason(spec, service_name, current, target):
    if spec is None:
        return f"service {service_name} is not registered"
    policy = spec.remediation
    if "SCALE" not in policy.actions:
        return f"SCALE is not allowed for {service_name}"
    if target < policy.min_replicas:
        return f"replicas must stay at or above {policy.min_replicas}"
    if target >= current:
        return "a scale-down must reduce the current replica count"
    if current - target > policy.max_scale_down_step:
        return f"a single scale-down may remove at most {policy.max_scale_down_step} replicas"
    return None


def default_registry() -> ServiceRegistry:
    return ServiceRegistry(version=1, services=[ServiceSpec(
        name="payment-service", namespace="nexus-demo",
        workload=WorkloadSpec(kind="Deployment", name="payment-service"), tier="critical", depends_on=[],
        remediation=RemediationConfig(actions=["ROLLBACK", "SCALE"], min_replicas=1, max_replicas=10,
                                      max_scale_step=7, approval="always"))])


_current_registry: ServiceRegistry | None = None
_current_source: dict | None = None


def _discovery_paths() -> list[Path]:
    return [Path("config/services.yaml"),
            Path(__file__).resolve().parent.parent / "config" / "services.yaml",
            Path("/app/config/services.yaml")]


def _read(path: Path) -> ServiceRegistry:
    if yaml is None:
        raise RegistryError(f"PyYAML is required to read the service registry {path}")
    try:
        raw = path.read_bytes()
        data = yaml.safe_load(raw)
    except OSError as exc:
        raise RegistryError(f"Service registry {path} cannot be read: {exc.strerror}") from exc
    except yaml.YAMLError as exc:
        raise RegistryError(f"Service registry {path} is not valid YAML") from exc
    try:
        registry = ServiceRegistry.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in exc.errors())
        raise RegistryError(f"Service registry {path} is invalid: {problems}") from exc
    global _current_source
    _current_source = {"source": str(path), "sha256": hashlib.sha256(raw).hexdigest()}
    return registry


def load(path: str | Path | None = None) -> ServiceRegistry:
    """Load the registry following the module rules; raises RegistryError, never guesses."""
    global _current_source
    explicit = path if path is not None else os.environ.get("NEXUS_SERVICES_CONFIG")
    if explicit:
        candidate = Path(explicit)
        if not candidate.is_file():
            raise RegistryError(f"Configured service registry {candidate} does not exist")
        return _read(candidate)
    for candidate in _discovery_paths():
        if candidate.is_file():
            return _read(candidate)
    _current_source = {"source": "built-in default (payment-service only)", "sha256": None}
    return default_registry()


def get_registry() -> ServiceRegistry:
    global _current_registry
    if _current_registry is None:
        _current_registry = load()
    return _current_registry


def registry_info() -> dict:
    registry = get_registry()
    return {**(_current_source or {"source": "injected", "sha256": None}),
            "services": sorted(registry.allowed_services())}


def set_registry(registry: ServiceRegistry | None) -> None:
    global _current_registry, _current_source
    _current_registry = registry
    if registry is None:
        _current_source = None
