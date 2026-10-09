import json
import os
from pathlib import Path
import requests
from pydantic import ValidationError
from app.contracts import FinOpsContext
from app.providers.errors import IntegrationError

MOCK_FILE = Path(__file__).resolve().parents[4] / "mocks" / "mock_finops_context.json"
FINOPS_PROVIDER = os.getenv("FINOPS_PROVIDER", "real").lower()
M6_FINOPS_URL = os.getenv("M6_FINOPS_URL", os.getenv("M6_FINOPS_BASE_URL", "http://localhost:8006").rstrip("/") + "/internal/finops/context")

def get_finops_context(service: str = "payment-service"):
    if FINOPS_PROVIDER == "mock":
        data = json.loads(MOCK_FILE.read_text(encoding="utf-8"))
        data["service"] = service
    elif FINOPS_PROVIDER == "real":
        try:
            response = requests.get(M6_FINOPS_URL, params={"service": service}, timeout=10)
        except requests.RequestException as exc:
            raise IntegrationError("M6 FinOps is unavailable") from exc
        if response.status_code != 200:
            raise IntegrationError(f"M6 FinOps request failed with status {response.status_code}")
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
