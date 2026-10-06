import os
import requests
import time

JENKINS_URL = os.getenv("JENKINS_URL")
JENKINS_USER = os.getenv("JENKINS_USER")
JENKINS_API_TOKEN = os.getenv("JENKINS_API_TOKEN")
JENKINS_JOB = "nerva-m4-remediation"


def trigger_jenkins_remediation(action: str, replicas: int | None = None):
    if not JENKINS_URL or not JENKINS_USER or not JENKINS_API_TOKEN:
        raise ValueError("Jenkins configuration is missing")

    params = {
        "ACTION": action,
        "REPLICAS": replicas if replicas is not None else 3,
    }

    response = requests.post(
        f"{JENKINS_URL}/job/{JENKINS_JOB}/buildWithParameters",
        params=params,
        auth=(JENKINS_USER, JENKINS_API_TOKEN),
        timeout=10,
    )

    if response.status_code != 201:
        raise ValueError(
            f"Jenkins trigger failed with status {response.status_code}: {response.text}"
        )

    queue_location = response.headers.get("Location")
    if not queue_location:
        raise ValueError("Jenkins response missing queue location")

    return wait_for_jenkins_result(queue_location)
def wait_for_jenkins_result(queue_location: str, timeout: int = 90):
    start = time.time()

    while time.time() - start < timeout:
        queue_response = requests.get(
            f"{queue_location}api/json",
            auth=(JENKINS_USER, JENKINS_API_TOKEN),
            timeout=10,
        )
        queue_response.raise_for_status()

        queue_data = queue_response.json()

        executable = queue_data.get("executable")
        if executable:
            build_url = executable["url"]

            while time.time() - start < timeout:
                build_response = requests.get(
                    f"{build_url}api/json",
                    auth=(JENKINS_USER, JENKINS_API_TOKEN),
                    timeout=10,
                )
                build_response.raise_for_status()

                build_data = build_response.json()

                if not build_data.get("building", False):
                    return {
                        "success": build_data.get("result") == "SUCCESS",
                        "result": build_data.get("result"),
                        "build_url": build_url,
                    }

                time.sleep(2)

        time.sleep(1)

    raise ValueError("Jenkins build timed out")