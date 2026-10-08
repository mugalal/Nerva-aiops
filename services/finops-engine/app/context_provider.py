"""Serves the no-body /internal/finops/context route that M4's provider points at.

Real data: replica count and CPU come from M1's snapshot.
Defaults (documented assumptions, set by environment variables): the demo's
requests and limits, and the scale duration.
"""
import os

from .m1_client import M1Unavailable, fetch_snapshot, to_pct_of_request
from .models import FinOpsContext, ScaleOptionsRequest
from .scale_options import build_scale_options


class ContextUnavailable(Exception):
    """No real data, so no context is produced (the route answers 503)."""


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def build_context(base_url: str, service: str | None = None, fetch=fetch_snapshot):
    service = service or os.getenv("FINOPS_DEFAULT_SERVICE", "payment-service")
    cpu_request_m = _env_int("FINOPS_CPU_REQUEST_M", 100)
    memory_request_mb = _env_int("FINOPS_MEMORY_REQUEST_MB", 128)
    cpu_limit_m = _env_int("FINOPS_CPU_LIMIT_M", 500)
    duration = _env_int("FINOPS_SCALE_DURATION_MINUTES", 30)

    try:
        metrics = fetch(base_url, service)["metrics"]
        replicas = int(metrics["replica_count"])
        cpu_fraction = float(metrics["cpu"])
        if replicas < 1:
            raise ValueError("M1 reports no running replicas")
    except (M1Unavailable, KeyError, ValueError, TypeError) as exc:
        raise ContextUnavailable(f"M1 data unavailable: {exc}") from exc

    observed_cpu_pct = round(
        min(100.0, to_pct_of_request(cpu_fraction, cpu_limit_m, cpu_request_m)), 2
    )
    request = ScaleOptionsRequest(
        service=service,
        current_replicas=replicas,
        cpu_request_m=cpu_request_m,
        memory_request_mb=memory_request_mb,
        observed_cpu_pct=observed_cpu_pct,
        scale_duration_minutes=duration,
    )
    return build_scale_options(request), request