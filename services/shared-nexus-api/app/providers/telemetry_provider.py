"""Read-only M1 telemetry for capacity right-sizing: a measured window and the healthy baseline."""
import os
from datetime import datetime

import requests

from app.providers.errors import IntegrationError
from app.providers.resilient import Breaker, CircuitOpen, make_session, resilient_call

_breaker = Breaker(threshold=5, cooldown=30.0)
_session = make_session()


def m1_base_url():
    explicit = os.getenv("M1_TELEMETRY_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    evidence = os.getenv("M1_EVIDENCE_URL", "http://localhost:8001/internal/evidence/capture")
    return evidence.split("/internal/", 1)[0].rstrip("/")


def _get(path, params=None):
    try:
        response = resilient_call(_breaker, _session, "GET", m1_base_url() + path, params=params, timeout=15)
    except CircuitOpen as exc:
        raise IntegrationError("M1 telemetry circuit is open", status_code=503, retryable=True) from exc
    except requests.RequestException as exc:
        raise IntegrationError("M1 telemetry is unavailable", status_code=503, retryable=True) from exc
    if response.status_code != 200:
        raise IntegrationError(f"M1 telemetry request failed with status {response.status_code}",
                               status_code=503, retryable=response.status_code >= 500)
    try:
        return response.json()
    except ValueError as exc:
        raise IntegrationError("M1 telemetry returned invalid JSON", status_code=502, retryable=False) from exc


def get_window(service: str, start: datetime, end: datetime, step_seconds: int = 15) -> dict:
    return _get("/internal/telemetry/window", {"service": service, "start": start.isoformat(),
                                               "end": end.isoformat(), "step_seconds": step_seconds})


def get_baseline(service: str) -> dict:
    return _get(f"/internal/baselines/{service}")
