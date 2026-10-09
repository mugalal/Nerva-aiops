"""Serves the no-body /internal/finops/context route that M4's provider points at.

Data and actual deployment resources come from M1's evidence preview.
If Kubernetes is unavailable, resource assumptions must be set explicitly.
"""
import os
import logging
import math

from .m1_client import M1Unavailable, fetch_preview, to_pct_of_request, validate_service, validate_snapshot
from .models import FinOpsContext, ScaleOptionsRequest
from .resource_config import ResourceConfiguration, finite_number
from .scale_options import build_scale_options


class ContextUnavailable(Exception):
    """No real data, so no context is produced (the route answers 503)."""


log = logging.getLogger("finops-engine")


def build_context(base_url: str, service: str | None = None, fetch=fetch_preview):
    try:
        service = validate_service(service if service is not None else os.getenv("FINOPS_DEFAULT_SERVICE", "payment-service"))
        duration = int(os.getenv("FINOPS_SCALE_DURATION_MINUTES", "30"))
        if not 1 <= duration <= 1440:
            raise ValueError("FINOPS_SCALE_DURATION_MINUTES must be between one and 1440")
        max_age = finite_number(os.getenv("FINOPS_M1_MAX_AGE_SECONDS", "60"),
                                "FINOPS_M1_MAX_AGE_SECONDS", positive=True)
        if max_age > 300:
            raise ValueError("FINOPS_M1_MAX_AGE_SECONDS must not exceed 300")
        preview = fetch(base_url, service)
        if not isinstance(preview, dict) or preview.get("provider_mode") != "real":
            raise ValueError("M1 evidence preview must use real providers")
        if preview.get("service") != service:
            raise ValueError("M1 evidence preview service does not match the request")
        snapshot = preview.get("telemetry")
        metrics = validate_snapshot(snapshot, service, max_age_seconds=max_age)
        kubernetes = preview.get("kubernetes")
        if kubernetes is not None:
            if not isinstance(kubernetes, dict) or kubernetes.get("service") != service:
                raise ValueError("M1 Kubernetes context service does not match the request")
            if kubernetes.get("service_health") != "ok" or kubernetes.get("version") != snapshot["version"]:
                raise ValueError("M1 Kubernetes rollout is unready or its version disagrees with telemetry")
            if preview.get("resource_config") is None:
                raise ValueError("M1 has no verified Kubernetes resource configuration")
        if preview.get("resource_config") is not None:
            resources = ResourceConfiguration.from_mapping(preview["resource_config"], "kubernetes")
        else:
            resources = ResourceConfiguration.from_environment()
        observed_cpu_pct = to_pct_of_request(metrics["cpu"], resources.cpu_limit_m, resources.cpu_request_m)
        request = ScaleOptionsRequest(
            service=service,
            current_replicas=metrics["replica_count"],
            cpu_request_m=math.ceil(resources.cpu_request_m),
            memory_request_mb=math.ceil(resources.memory_request_mb),
            observed_cpu_pct=round(min(100.0, observed_cpu_pct), 2),
            scale_duration_minutes=duration,
        )
        result = build_scale_options(request, observed_cpu_pct=observed_cpu_pct)
    except (M1Unavailable, KeyError, ValueError, TypeError, OverflowError) as exc:
        raise ContextUnavailable(f"Real FinOps context unavailable: {exc}") from exc
    log.info("FinOps context service=%s resource_source=%s", service, resources.source)
    return result, request
