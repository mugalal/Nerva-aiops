import os

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
        raise NotImplementedError(
            "Real M1 recovery integration is not connected yet"
        )

    raise ValueError(
        f"Unsupported recovery provider: {RECOVERY_PROVIDER}"
    )