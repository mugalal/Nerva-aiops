import json
import os
from pathlib import Path
import requests
from pydantic import ValidationError
from app.contracts import FinOpsContext
from app.providers.errors import IntegrationError
from app.providers.resilient import Breaker, make_session, resilient_call, CircuitOpen

MOCK_FILE = Path(__file__).resolve().parents[4] / "mocks" / "mock_finops_context.json"
FINOPS_PROVIDER = os.getenv("FINOPS_PROVIDER", "real").lower()
M6_FINOPS_URL = os.getenv("M6_FINOPS_URL", os.getenv("M6_FINOPS_BASE_URL", "http://localhost:8006").rstrip("/") + "/internal/finops/context")

_breaker = Breaker(threshold=5, cooldown=30.0)
_session = make_session(retry_post=False)


def get_finops_context(service: str = "payment-service"):
    if FINOPS_PROVIDER == "mock":
        data = json.loads(MOCK_FILE.read_text(encoding="utf-8"))
        data["service"] = service
    elif FINOPS_PROVIDER == "real":
        try:
            response = resilient_call(_breaker, _session, "GET", M6_FINOPS_URL, params={"service": service}, timeout=10)
        except CircuitOpen as exc:
            raise IntegrationError("M6 FinOps circuit is open", status_code=503, retryable=True) from exc
        except requests.RequestException as exc:
            raise IntegrationError("M6 FinOps is unavailable", status_code=503, retryable=True) from exc
        if response.status_code != 200:
            is_client_error = 400 <= response.status_code < 500
            raise IntegrationError(
                f"M6 FinOps request failed with status {response.status_code}",
                status_code=response.status_code if is_client_error else 503,
                retryable=not is_client_error,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise IntegrationError("M6 returned invalid JSON", status_code=502, retryable=False) from exc
    else:
        raise ValueError(f"Unsupported FINOPS provider: {FINOPS_PROVIDER}")
    try:
        result = FinOpsContext.model_validate(data)
    except ValidationError as exc:
        raise IntegrationError("M6 FinOps response violates the frozen contract", status_code=502, retryable=False) from exc
    if result.service != service:
        raise IntegrationError("M6 returned another service's context", status_code=502, retryable=False)
    return result.model_dump(mode="json")
