import os
import json
import requests
from pathlib import Path


MOCK_FILE = Path(__file__).resolve().parents[4] / "mocks" / "mock_rca_response.json"
RCA_PROVIDER = os.getenv("RCA_PROVIDER", "mock").lower()
M3_RCA_URL = os.getenv(
    "M3_RCA_URL",
    "http://localhost:8003/internal/rca/analyze"
)
def get_rca_result(incident_id: str | None=None):
    if RCA_PROVIDER == "mock":
        with open(MOCK_FILE, "r", encoding="utf-8") as file:
            return json.load(file)

    if RCA_PROVIDER == "real":
        if incident_id is None:
            raise ValueError(
                "incident_id is required when using real M3 RCA provider"
            )

        response = requests.post(
            M3_RCA_URL,
            json={
                "incident_id": incident_id
            },
            timeout=10,
        )

        if response.status_code != 200:
            raise ValueError(
                f"M3 RCA request failed with status "
                f"{response.status_code}: {response.text}"
            )

        return response.json()

    raise ValueError(
        f"Unsupported RCA provider: {RCA_PROVIDER}"
    )