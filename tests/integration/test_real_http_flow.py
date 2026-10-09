"""Four actual HTTP processes, controlled M1 inputs, and a mock-only actuator.

These tests prove service integration, not live telemetry or Kubernetes.
"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4
import pytest
import requests

REPO = Path(__file__).resolve().parents[2]


def utc_now():
    return datetime.now(timezone.utc)


def available_ports(count):
    listeners = []
    try:
        for _ in range(count):
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listeners.append(listener)
        return [listener.getsockname()[1] for listener in listeners]
    finally:
        for listener in listeners:
            listener.close()


class HttpStack:
    def __init__(self, directory):
        self.directory = directory
        self.urls = {name: f"http://127.0.0.1:{port}"
                     for name, port in zip(("m1", "m3", "m4", "m6"), available_ports(4))}
        self.processes, self.logs = [], []

    def start(self):
        env = dict(os.environ)
        env.update(ENVIRONMENT="controlled-http-test", LOG_LEVEL="WARNING", DATABASE_URL="",
                   M1_PROVIDER_MODE="real", M3_PROVIDER_MODE="real", RCA_PROVIDER="real",
                   FINOPS_PROVIDER="real", EVIDENCE_PROVIDER="real", RECOVERY_PROVIDER="real",
                   REMEDIATION_BACKEND="mock", M1_DATA_DIR=str(self.directory / "m1-data"),
                   M1_REQUEST_RATE_WINDOW="5s", M1_LATENCY_WINDOW="5s", M1_HISTORY_STEP_SECONDS="5",
                   M1_RECOVERY_HOLD_SECONDS="10", M1_RECOVERY_MIN_SAMPLES="3",
                   M1_TELEMETRY_BASE_URL=self.urls["m1"], SHARED_NEXUS_API_BASE_URL=self.urls["m4"],
                   M4_DECISION_BASE_URL=self.urls["m4"], M3_RCA_BASE_URL=self.urls["m3"], M6_FINOPS_BASE_URL=self.urls["m6"],
                   M1_EVIDENCE_URL=self.urls["m1"] + "/internal/evidence/capture",
                   M1_RECOVERY_URL=self.urls["m1"] + "/internal/recovery/validate",
                   M3_RCA_URL=self.urls["m3"] + "/internal/rca/analyze",
                   M6_FINOPS_URL=self.urls["m6"] + "/internal/finops/context")
        layouts = {"m1": (REPO / "services/telemetry-intelligence", "controlled_m1_server:app", REPO / "tests/integration"),
                   "m3": (REPO / "services/root-cause-analysis", "app.main:app", None),
                   "m4": (REPO / "services/shared-nexus-api", "app.main:app", None),
                   "m6": (REPO / "services/finops-engine", "app.main:app", None)}
        for name in ("m1", "m6", "m4", "m3"):
            cwd, application, app_dir = layouts[name]
            command = [sys.executable, "-m", "uvicorn", application, "--host", "127.0.0.1",
                       "--port", self.urls[name].rsplit(":", 1)[1], "--log-level", "warning", "--no-access-log"]
            if app_dir is not None:
                command += ["--app-dir", str(app_dir)]
            path = self.directory / f"{name}.log"
            log = path.open("w", encoding="utf-8")
            self.logs.append(log)
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.processes.append((name, process, path))
        for name, process, path in self.processes:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                assert process.poll() is None, f"{name} exited during startup:\n{path.read_text(encoding='utf-8')}"
                try:
                    if requests.get(self.urls[name] + "/health", timeout=2).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail(f"{name} did not start:\n{path.read_text(encoding='utf-8')}")
        return self

    def close(self):
        # Terminate only the exact child process handles created by this fixture.
        for _, process, _ in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in self.logs:
            log.close()

    def request(self, method, member, route, expected=200, **kwargs):
        response = requests.request(method, self.urls[member] + route, timeout=20, **kwargs)
        assert response.status_code == expected, f"{member} {route}: HTTP {response.status_code} {response.text}"
        return response


@pytest.fixture
def stack(tmp_path):
    stack = HttpStack(tmp_path)
    try:
        yield stack.start()
    finally:
        stack.close()


@pytest.mark.parametrize("scenario,action", [("faulty_deployment", "ROLLBACK"), ("traffic_spike", "SCALE")])
def test_real_http_incident_action_and_measured_recovery(stack, scenario, action):
    incident_id = "INC-HTTP-" + uuid4().hex
    health = stack.request("GET", "m4", "/health").json()
    assert set(health["provider_modes"].values()) == {"real"}
    assert health["remediation_backend"] == "mock"
    assert stack.request("GET", "m3", "/health").json()["provider_mode"] == "real"
    # The actual baseline API checks a controlled 75s historical window.
    end = utc_now() - timedelta(seconds=20)
    baseline = stack.request("POST", "m1", "/internal/baselines/measure", json={
        "service": "payment-service", "start": (end - timedelta(seconds=75)).isoformat(),
        "end": end.isoformat(), "step_seconds": 15}).json()
    assert baseline["sample_count"] == 6
    assert baseline["thresholds"]["latency_p95_ms_max"] == 12.5
    stack.request("POST", "m1", "/__test/state", json={"phase": "fault" if action == "ROLLBACK" else "spike"})
    incident = stack.request("POST", "m4", "/api/incidents/", json={
        "incident_id": incident_id, "severity": "high", "affected_services": ["payment-service"]}).json()
    assert datetime.fromisoformat(baseline["measured_at"]) < datetime.fromisoformat(incident["started_at"])
    snapshot = stack.request("GET", "m1", "/internal/telemetry/snapshot?service=payment-service").json()
    anomaly = {"anomaly_id": "AN-HTTP-" + uuid4().hex, "timestamp": snapshot["timestamp"], "service": snapshot["service"],
               "score": 0.95, "severity": "high", "model": "controlled-http-test",
               "features": {key: snapshot["metrics"][key] for key in ("request_rate", "latency_p95_ms", "http_5xx_rate", "cpu")}}
    stack.request("POST", "m4", "/internal/anomalies", params={"incident_id": incident_id}, json=anomaly)
    # Diagnosis succeeds before capture, resolving the prior circular dependency.
    stack.request("GET", "m1", f"/internal/evidence/{incident_id}", expected=404)
    rca = stack.request("POST", "m3", "/internal/rca/analyze", json={"incident_id": incident_id}).json()
    assert rca["incident_id"] == incident_id and rca["root_cause"] == scenario and rca["confidence"] >= 0.7
    assert rca["affected_component"] == f"payment-service:{snapshot['version']}"
    stack.request("GET", "m1", f"/internal/evidence/{incident_id}", expected=404)
    stack.request("POST", "m3", "/internal/rca/analyze", json={"incident_id": "INC-NOT-FOUND"}, expected=404)
    if action == "SCALE":
        finops = stack.request("GET", "m6", "/internal/finops/context", params={"service": "payment-service"}).json()
        assert finops["current_cpu_request_m"] == 100 and finops["observed_cpu_pct"] == 100
        # 0.9 * 500m / 100m = 450% actual request utilization: risk needs 9 replicas.
        low_risk = [option for option in finops["temporary_scale_options"] if option["risk"] == "LOW"]
        assert min(option["replicas"] for option in low_risk) == 9
        assert not any(option["replicas"] == 2 and option["risk"] == "LOW" for option in finops["temporary_scale_options"])
    decision = stack.request("POST", "m4", f"/internal/decisions/build/{incident_id}").json()
    assert decision["action"] == action
    proposal = stack.request("GET", "m4", f"/api/incidents/{incident_id}/proposal").json()
    assert proposal["recommended_action"] == action and proposal["target"] == "payment-service"
    capture = stack.request("GET", "m1", f"/internal/evidence/{incident_id}").json()
    assert capture["scenario"] == ("bad_deployment" if action == "ROLLBACK" else "traffic_spike")
    assert capture["baseline"] == baseline
    result = stack.request("POST", "m4", f"/api/incidents/{incident_id}/approve", json={"approved": True}).json()
    assert result["incident_id"] == incident_id and result["action"] == action and result["status"] == "SUCCESS"
    assert datetime.fromisoformat(result["started_at"]) <= datetime.fromisoformat(result["completed_at"])
    assert datetime.fromisoformat(capture["captured_at"]) <= datetime.fromisoformat(result["started_at"])
    assert stack.request("GET", "m4", f"/api/incidents/{incident_id}/actions/latest").json() == result
    assert stack.request("POST", "m4", f"/api/incidents/{incident_id}/approve", json={"approved": True}).json() == result
    pending = stack.request("POST", "m4", f"/internal/recovery/validate/{incident_id}", expected=202)
    assert pending.json()["detail"]["category"] == "recovery_pending"
    assert pending.json()["detail"]["retryable"] is True and pending.headers["Retry-After"] == "15"
    assert stack.request("GET", "m4", f"/api/incidents/{incident_id}").json()["status"] == "VALIDATING"
    stack.request("POST", "m4", "/internal/recovery/validate", expected=409,
                  json={"incident_id": incident_id, "success": True})
    replicas = 1 if action == "ROLLBACK" else proposal["parameters"]["replicas"]
    stack.request("POST", "m1", "/__test/state", json={
        "phase": "rolled_back" if action == "ROLLBACK" else "scaled", "replicas": replicas})
    deadline = time.monotonic() + 25
    poll_statuses = []
    while time.monotonic() < deadline:
        response = requests.post(stack.urls["m4"] + f"/internal/recovery/validate/{incident_id}", timeout=20)
        poll_statuses.append(response.status_code)
        if response.status_code == 200:
            assert response.json()["status"] == "RESOLVED"
            break
        assert response.status_code == 202, response.text
        assert stack.request("GET", "m4", f"/api/incidents/{incident_id}").json()["status"] == "VALIDATING"
        time.sleep(0.5)
    else:
        pytest.fail(f"Controlled recovery did not complete; statuses={poll_statuses}")
    recovery = stack.request("GET", "m1", f"/internal/recovery/{incident_id}").json()
    assert recovery["action_completed_at"] == result["completed_at"]
    assert recovery["result"]["recovered"] is True and recovery["result"]["slo_restored"] is True
    assert recovery["result"]["incident_id"] == incident_id
    assert recovery["measurement_window"]["step_seconds"] == 5
    assert len(recovery["measurement_window"]["points"]) == 3
    assert all(point["metrics"]["latency_p95_ms"] <= 12.5 for point in recovery["measurement_window"]["points"])
    assert recovery["after"]["metrics"]["request_rate"] == snapshot["metrics"]["request_rate"]
    if action == "SCALE":
        assert recovery["after"]["metrics"]["replica_count"] == 9
    else:
        assert recovery["after"]["version"] == "v1" and recovery["before"]["version"] == "v2"
    assert stack.request("POST", "m4", f"/internal/recovery/validate/{incident_id}").json()["status"] == "RESOLVED"
