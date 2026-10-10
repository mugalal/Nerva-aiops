"""Verify an already started, mock-mode M5 Compose stack (synthetic data only)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.fixtures import demo_records

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "services/incident-memory/compose.yml")]
BASE = "http://127.0.0.1:" + os.getenv("M5_HOST_PORT", "8005")


def compose(*args):
    subprocess.run([*COMPOSE, *args], check=True, cwd=ROOT)


def ready():
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            health = httpx.get(BASE + "/health", timeout=5).json()
            if health["storage"] == "postgresql" and health["status"] != "unavailable":
                assert health["ui_mode"] == "mock", "Verification requires explicit mock mode"
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError("M5 PostgreSQL storage did not become ready")


def post(path, payload, status=200):
    response = httpx.post(BASE + path, json=payload, timeout=15)
    assert response.status_code == status, response.text
    return response.json()


def verify():
    ready()
    compose("exec", "-T", "memory", "python", "scripts/seed_demo.py")
    compose("exec", "-T", "memory", "sh", "-c", 'M5_TEST_DATABASE_URL="$DATABASE_URL" python -m unittest discover -s tests -v')
    before = httpx.get(BASE + "/internal/memory/INC-001?source=mock").json()
    compose("restart", "memory")
    ready()
    assert httpx.get(BASE + "/internal/memory/INC-001?source=mock").json() == before
    compose("restart", "postgres")
    ready()
    assert httpx.get(BASE + "/internal/memory/INC-001?source=mock").json() == before
    print("PASS: exact stored record survives service and PostgreSQL restarts")
    payload = demo_records()[2]
    payload["memory"]["incident_id"] = "VERIFY-" + uuid4().hex
    try:
        compose("stop", "postgres")
        assert httpx.get(BASE + "/health", timeout=15).json()["status"] == "unavailable"
        for path, body in [("/internal/memory/store", payload), ("/internal/memory/search", {"source": "mock", "service": "payment-service"}), ("/internal/copilot/query", {"source": "mock", "incident_id": "INC-001", "question": "What happened?"})]:
            assert post(path, body, 503)["status"] == "unavailable"
    finally:
        compose("start", "postgres")
        ready()
    assert post("/internal/memory/store", payload)["status"] == "stored"
    assert post("/internal/memory/store", payload)["status"] == "already_stored"
    print("PASS: database outage is explicit; retained producer request retries successfully")
    for path in ["/", "/assets/app.js", "/assets/styles.css", "/internal/ui/overview", "/internal/ui/incidents/INC-001"]:
        assert httpx.get(BASE + path).status_code == 200
    result = post("/internal/memory/search", {"source": "mock", "incident_type": "faulty_deployment", "service": "payment-service", "exclude_incident_id": "INC-001"})
    assert result["results"][0]["record"]["memory"]["incident_id"] == "INC-DEMO-HISTORY"
    result = post("/internal/copilot/query", {"source": "mock", "incident_id": "INC-001", "question": "What is the root cause?"})
    assert result["status"] == "ok" and result["citations"]
    result = post("/internal/copilot/query", {"source": "mock", "incident_id": "INC-DEMO-TRAFFIC", "question": "What changed before the incident?"})
    assert result["status"] == "insufficient_evidence" and not result["citations"]
    print("PASS: packaged UI, retrieval, cited answers and missing evidence")
    print(json.dumps({"status": "passed", "source": "mock", "storage": "postgresql"}))


if __name__ == "__main__":
    verify()
