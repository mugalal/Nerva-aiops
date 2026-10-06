import os
import json
from pathlib import Path

MOCK_FILE = Path(__file__).resolve().parents[4] / "mocks" / "mock_finops_context.json"
FINOPS_PROVIDER = os.getenv("FINOPS_PROVIDER", "mock").lower()
M6_FINOPS_URL = os.getenv(
    "M6_FINOPS_URL",
    "http://localhost:8006/internal/finops/context"
)
def get_finops_context():
    if FINOPS_PROVIDER == "mock":
        with open(MOCK_FILE, "r", encoding="utf-8") as file:
            return json.load(file)

    if FINOPS_PROVIDER == "real":
        raise NotImplementedError(
            "Real M6 FINOPS integration is not connected yet"
        )

    raise ValueError(
        f"Unsupported FINOPS provider: {FINOPS_PROVIDER}"
    )