import os
import requests
from datetime import datetime, timezone

RECOVERY_PROVIDER = os.getenv("RECOVERY_PROVIDER", "mock").lower()

M1_RECOVERY_URL = os.getenv(
    "M1_RECOVERY_URL",
    "http://localhost:8001/internal/recovery/validate"
)


def validate_recovery(incident_id: str):
    if RECOVERY_PROVIDER == "mock":
        return {
            "incident_id": incident_id,
            "success": True,
            "source": "mock"
        }

    if RECOVERY_PROVIDER == "real":
        response = requests.post(
            M1_RECOVERY_URL,
            json={
                "incident_id": incident_id,
                "service": "payment-service",
                "action_completed_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "scenario": "faulty_deployment",
            },
            timeout=10,
        )

    if response.status_code != 200:
        raise ValueError(
            f"M1 recovery request failed with status "
            f"{response.status_code}: {response.text}"
        )

    data = response.json()

    return {
        "incident_id": incident_id,
        "success": data["recovered"],
        "source": "m1",
        "details": data,
    }

    raise ValueError(
        f"Unsupported recovery provider: {RECOVERY_PROVIDER}"
    )