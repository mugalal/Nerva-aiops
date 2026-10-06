import os
import json
from pathlib import Path

MOCK_FILE = Path(__file__).resolve().parents[4] / "mocks" / "mock_rca_response.json"
RCA_PROVIDER = os.getenv("RCA_PROVIDER", "mock").lower()
M3_RCA_URL = os.getenv(
    "M3_RCA_URL",
    "http://localhost:8003/internal/rca/analyze"
)
def get_rca_result():
    if RCA_PROVIDER == "mock":
        with open(MOCK_FILE, "r", encoding="utf-8") as file:
            return json.load(file)

    if RCA_PROVIDER == "real":
        raise NotImplementedError(
            "Real M3 RCA integration is not connected yet"
        )

    raise ValueError(
        f"Unsupported RCA provider: {RCA_PROVIDER}"
    )