"""Five HTTP processes connect M2 detection to the integrated incident flow.

M1 uses its production API with explicit controlled providers. M2 evaluates
raw M1 snapshots, correlates its own alerts, and hands them to the actual M4
API. M3 and M6 use their real HTTP providers. This proves controlled service
integration, not trained-model accuracy or live Kubernetes recovery. No action
is approved or executed.
"""

from datetime import timedelta
import os
import subprocess
import sys
import time

import pytest
import requests

from test_real_http_flow import HttpStack, REPO, available_ports, utc_now


class M2HttpStack(HttpStack):
    def start(self):
        super().start()
        port = available_ports(1)[0]
        self.urls["m2"] = f"http://127.0.0.1:{port}"
        env = dict(os.environ)
        env.update(
            ENVIRONMENT="controlled-http-test",
            LOG_LEVEL="WARNING",
            M1_TELEMETRY_BASE_URL=self.urls["m1"],
            SHARED_NEXUS_API_BASE_URL=self.urls["m4"],
            M2_REFERENCE_PATH="none",
            M2_POLL_ENABLED="false",
            M2_HANDOFF_ENABLED="true",
            M2_HANDOFF_URL=self.urls["m4"],
            M2_EVIDENCE_PATH=str(self.directory / "m2-evidence.jsonl"),
            M2_HANDOFF_SETTLE_ALERTS="4",
            M2_CORRELATION_GAP_S="120",
            M2_JSON_LOGGING="off",
            # M2's shared contracts/config/logging live at the repository root.
            PYTHONPATH=os.pathsep.join(filter(None, (str(REPO), env.get("PYTHONPATH")))),
        )
        path = self.directory / "m2.log"
        log = path.open("w", encoding="utf-8")
        self.logs.append(log)
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port), "--log-level", "warning", "--no-access-log"],
            cwd=REPO / "services/anomaly-engine", env=env, stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.processes.append(("m2", process, path))
        # M2 imports scientific libraries even in threshold mode; their first
        # native-library import can be slower than the API-only services.
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            assert process.poll() is None, f"m2 exited during startup:\n{path.read_text(encoding='utf-8')}"
            try:
                if requests.get(self.urls["m2"] + "/health", timeout=2).status_code == 200:
                    return self
            except requests.RequestException:
                pass
            time.sleep(0.1)
        pytest.fail(f"m2 did not start:\n{path.read_text(encoding='utf-8')}")


@pytest.fixture
def m2_stack(tmp_path):
    stack = M2HttpStack(tmp_path)
    try:
        yield stack.start()
    finally:
        stack.close()


@pytest.mark.parametrize("scenario,phase,action", [
    ("faulty_deployment", "fault", "ROLLBACK"),
    ("traffic_spike", "spike", "SCALE"),
])
def test_m1_m2_correlation_handoff_rca_and_decision(m2_stack, scenario, phase, action):
    stack = m2_stack
    health = stack.request("GET", "m2", "/health").json()
    assert health["detector"] == "threshold_baseline"
    assert health["dependencies"] == {"m1": "disabled", "shared_api": "ok", "reference": "disabled"}
    assert stack.request("GET", "m3", "/health").json()["provider_mode"] == "real"
    assert set(stack.request("GET", "m4", "/health").json()["provider_modes"].values()) == {"real"}

    end = utc_now() - timedelta(seconds=20)
    baseline = stack.request("POST", "m1", "/internal/baselines/measure", json={
        "service": "payment-service", "start": (end - timedelta(seconds=75)).isoformat(),
        "end": end.isoformat(), "step_seconds": 15,
    }).json()
    assert baseline["sample_count"] == 6
    healthy = stack.request("GET", "m1", "/internal/telemetry/snapshot",
                            params={"service": "payment-service"}).json()
    healthy_event = stack.request("POST", "m2", "/internal/anomalies/evaluate", json=healthy).json()
    assert healthy_event["score"] == 0
    assert stack.request("GET", "m2", "/internal/correlation/incidents").json() == []
    assert stack.request("GET", "m4", "/api/incidents/").json() == []

    stack.request("POST", "m1", "/__test/state", json={"phase": phase})
    events = []
    snapshots = []
    for _ in range(4):
        snapshot = stack.request("GET", "m1", "/internal/telemetry/snapshot",
                                 params={"service": "payment-service"}).json()
        event = stack.request("POST", "m2", "/internal/anomalies/evaluate", json=snapshot).json()
        assert event["model"] == "threshold_baseline" and event["score"] >= 0.1
        assert event["timestamp"] == snapshot["timestamp"] and event["service"] == snapshot["service"]
        assert event["features"] == {key: snapshot["metrics"][key]
                                     for key in ("request_rate", "latency_p95_ms", "http_5xx_rate", "cpu")}
        snapshots.append(snapshot)
        events.append(event)

    (candidate,) = stack.request("GET", "m2", "/internal/correlation/incidents").json()
    incident_id = candidate["incident_id"]
    assert candidate["status"] == "DETECTED" and candidate["affected_services"] == ["payment-service"]
    assert candidate["anomaly_ids"] == [event["anomaly_id"] for event in events]
    detail = stack.request("GET", "m2", f"/internal/correlation/incidents/{incident_id}").json()
    assert detail["alert_count"] == 4 and detail["handoff"]["incident_created"] is True
    assert detail["handoff"]["error"] is None
    if phase == "fault":
        assert detail["context"]["version_change"]["from"] == "v1"
        assert detail["context"]["version_change"]["to"] == "v2"
        assert "http_5xx_rate" in detail["signals"]
    else:
        assert {"cpu", "request_rate_change"} <= set(detail["signals"])

    (stored_incident,) = stack.request("GET", "m4", "/api/incidents/").json()
    assert stored_incident["incident_id"] == incident_id
    assert events[0]["anomaly_id"] in stored_incident["anomaly_ids"]
    # The onset has the highest score because it includes actual change features.
    # M2's automatic handoff must preserve the event exactly; no test-created
    # incident or anomaly is posted directly to the core API.
    stored_anomaly = stack.request("GET", "m4", "/internal/anomalies",
                                   params={"incident_id": incident_id}).json()
    assert stored_anomaly == events[0]
    assert stack.request("GET", "m2", "/internal/anomalies",
                         params={"incident_id": incident_id}).json() == stored_anomaly
    assert stack.request("GET", "m2", "/health").json()["dependencies"]["shared_api"] == "ok"

    stack.request("GET", "m1", f"/internal/evidence/{incident_id}", expected=404)
    rca = stack.request("POST", "m3", "/internal/rca/analyze", json={"incident_id": incident_id}).json()
    assert rca["root_cause"] == scenario and rca["confidence"] >= 0.7
    assert rca["affected_component"] == f"payment-service:{snapshots[-1]['version']}"
    decision = stack.request("POST", "m4", f"/internal/decisions/build/{incident_id}").json()
    assert decision["action"] == action
    proposal = stack.request("GET", "m4", f"/api/incidents/{incident_id}/proposal").json()
    assert proposal["recommended_action"] == action and proposal["target"] == "payment-service"
    if action == "SCALE":
        finops = stack.request("GET", "m6", "/internal/finops/context",
                               params={"service": "payment-service"}).json()
        low_risk = [option["replicas"] for option in finops["temporary_scale_options"] if option["risk"] == "LOW"]
        assert proposal["parameters"]["replicas"] == min(low_risk) == 9
    stack.request("GET", "m4", f"/api/incidents/{incident_id}/actions/latest", expected=404)
