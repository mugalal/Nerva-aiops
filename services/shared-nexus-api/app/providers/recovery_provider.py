
import os
import requests


RECOVERY_PROVIDER = os.getenv(
    "RECOVERY_PROVIDER",
    "mock"
).lower()

M1_RECOVERY_URL = os.getenv(
    "M1_RECOVERY_URL",
    "http://localhost:8001/internal/recovery/validate"
)


def validate_recovery(
    incident_id: str,
    scenario: str,
    action_completed_at: str
):
    if RECOVERY_PROVIDER == "mock":
        return {
            "incident_id": incident_id,
            "success": True,
            "source": "mock"
        }

    if RECOVERY_PROVIDER == "real":
        # Translate M4 scenario names to M1 scenario names.
        m1_scenario = (
            "bad_deployment"
            if scenario == "faulty_deployment"
            else scenario
        )

        response = requests.post(
            M1_RECOVERY_URL,
            json={
                "incident_id": incident_id,
                "service": "payment-service",
                "action_completed_at": action_completed_at,
                "scenario": m1_scenario,
            },
            timeout=30,
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
