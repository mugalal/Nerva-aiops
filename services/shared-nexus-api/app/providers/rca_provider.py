import json
import os
from pathlib import Path
import requests
from pydantic import ValidationError
from app.contracts import RCAResult
from app.providers.errors import IntegrationError
from app.providers.resilient import Breaker, make_session, resilient_call, CircuitOpen

MOCK_FILE = Path(__file__).resolve().parents[4] / "mocks" / "mock_rca_response.json"
RCA_PROVIDER = os.getenv("RCA_PROVIDER", "real").lower()
M3_RCA_URL = os.getenv("M3_RCA_URL", os.getenv("M3_RCA_BASE_URL", "http://localhost:8003").rstrip("/") + "/internal/rca/analyze")

_breaker = Breaker(threshold=5, cooldown=30.0)
_session = make_session(retry_post=True)


def get_rca_result(incident_id: str | None = None):
    if RCA_PROVIDER == "mock":
        data = json.loads(MOCK_FILE.read_text(encoding="utf-8"))
        if incident_id is not None:
            data["incident_id"] = incident_id
    elif RCA_PROVIDER == "real":
        if not incident_id:
            raise ValueError("incident_id is required when using real M3 RCA provider")
        try:
            response = resilient_call(_breaker, _session, "POST", M3_RCA_URL, json={"incident_id": incident_id}, timeout=10)
        except CircuitOpen as exc:
            raise IntegrationError("M3 RCA circuit is open", status_code=503, retryable=True) from exc
        except requests.RequestException as exc:
            raise IntegrationError("M3 RCA is unavailable", status_code=503, retryable=True) from exc
        if response.status_code != 200:
            is_client_error = 400 <= response.status_code < 500
            raise IntegrationError(
                f"M3 RCA request failed with status {response.status_code}",
                status_code=response.status_code if is_client_error else 503,
                retryable=not is_client_error,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise IntegrationError("M3 returned invalid JSON", status_code=502, retryable=False) from exc
    else:
        raise ValueError(f"Unsupported RCA provider: {RCA_PROVIDER}")
    try:
        result = RCAResult.model_validate(data)
    except ValidationError as exc:
        raise IntegrationError("M3 RCA response violates the frozen contract", status_code=502, retryable=False) from exc
    if incident_id is not None and result.incident_id != incident_id:
        raise IntegrationError("M3 RCA returned another incident's evidence", status_code=502, retryable=False)
    return result.model_dump(mode="json")
