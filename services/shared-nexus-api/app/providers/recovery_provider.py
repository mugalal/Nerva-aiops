import os
import requests
from pydantic import ValidationError
from app.contracts import RecoveryResult
from app.providers.errors import IntegrationError, RecoveryPending
from app.providers.resilient import Breaker, make_session, resilient_call, CircuitOpen

RECOVERY_PROVIDER = os.getenv("RECOVERY_PROVIDER", "real").lower()
M1_RECOVERY_URL = os.getenv("M1_RECOVERY_URL", os.getenv("M1_TELEMETRY_BASE_URL", "http://localhost:8001").rstrip("/") + "/internal/recovery/validate")

_breaker = Breaker(threshold=5, cooldown=30.0)
_session = make_session(retry_post=True)


def validate_recovery(incident_id: str, scenario: str, action_completed_at: str, service: str = "payment-service"):
    if RECOVERY_PROVIDER == "mock":
        return {"incident_id": incident_id, "success": True, "source": "mock"}
    if RECOVERY_PROVIDER != "real":
        raise ValueError(f"Unsupported recovery provider: {RECOVERY_PROVIDER}")
    m1_scenario = "bad_deployment" if scenario == "faulty_deployment" else scenario
    try:
        response = resilient_call(_breaker, _session, "POST", M1_RECOVERY_URL, json={"incident_id": incident_id, "service": service,
                                  "action_completed_at": action_completed_at, "scenario": m1_scenario}, timeout=30)
    except CircuitOpen as exc:
        raise IntegrationError("M1 recovery validation circuit is open", status_code=503, retryable=True) from exc
    except requests.RequestException as exc:
        raise IntegrationError("M1 recovery validation is unavailable") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise IntegrationError("M1 recovery returned invalid JSON", status_code=502, retryable=False) from exc
    detail = data.get("detail", {}) if isinstance(data, dict) else {}
    if response.status_code == 409 and isinstance(detail, dict) and detail.get("category") == "recovery_pending":
        raise RecoveryPending(detail.get("message", "Recovery measurements are pending"))
    if response.status_code != 200:
        retryable = response.status_code >= 500 or (isinstance(detail, dict) and detail.get("retryable") is True)
        raise IntegrationError(f"M1 recovery request failed with status {response.status_code}",
                               status_code=503 if retryable or response.status_code >= 500 else 502, retryable=retryable)
    try:
        result = RecoveryResult.model_validate(data)
    except ValidationError as exc:
        raise IntegrationError("M1 recovery response violates the frozen contract", status_code=502, retryable=False) from exc
    if result.incident_id != incident_id:
        raise IntegrationError("M1 recovery returned another incident's result", status_code=502, retryable=False)
    return {"incident_id": incident_id, "success": result.recovered and result.slo_restored,
            "source": "m1", "details": result.model_dump(mode="json")}
