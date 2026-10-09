
import os
import requests


M1_EVIDENCE_URL = os.getenv(
    "M1_EVIDENCE_URL",
    "http://localhost:8001/internal/evidence/capture"
)


def capture_incident_evidence(
    incident_id: str,
    service: str,
    scenario: str
):
    m1_scenario = (
        "bad_deployment"
        if scenario == "faulty_deployment"
        else scenario
    )


    response = requests.post(
        M1_EVIDENCE_URL,
        json={
            "incident_id": incident_id,
            "service": service,
            "scenario": m1_scenario,
        },
        timeout=10,
    )

    if response.status_code != 200:
        raise ValueError(
            f"M1 evidence capture failed with status "
            f"{response.status_code}: {response.text}"
        )

    return response.json()
