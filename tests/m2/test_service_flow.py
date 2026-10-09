"""
Tests for the running service: the endpoints, polling M1, and what happens
when M1's data is missing, stale, repeated, malformed or unreachable.

Needs numpy and scikit-learn:   pip install scikit-learn numpy
Run from the repo root:         python -m pytest tests/m2 -v
"""

import asyncio
import json
import random
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import m2_loader
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from fastapi.testclient import TestClient  # noqa: E402
from real_like import SERVICE, T0, normal_rows, snapshot  # noqa: E402

from shared.contracts import AnomalyEvent, Incident  # noqa: E402

model_mod = m2_loader.module("model")
reference_mod = m2_loader.module("reference")
pipeline_mod = m2_loader.module("pipeline")
poller_mod = m2_loader.module("poller")
client_mod = m2_loader.module("realdata.m1_client")
main_mod = m2_loader.module("main")


def healthy(offset_s=0, **kw):
    return snapshot(T0 + timedelta(seconds=offset_s), rng=random.Random(offset_s), **kw)


def bad(offset_s=0, **kw):
    return snapshot(T0 + timedelta(seconds=offset_s), kind="bad", progress=1.0, rng=random.Random(offset_s), **kw)


def body(snap):
    return snap.model_dump(mode="json")


@pytest.fixture(scope="module")
def reference():
    detector = model_mod.ZScoreDetector(min_samples=20).fit({SERVICE: normal_rows(120, seed=1)})
    return reference_mod.Reference(
        detector=detector,
        alert=reference_mod.AlertLine(wobbles=80.0, normal_max_wobbles=13.0, fault_min_wobbles=480.0),
        trained_on=("capture1.json",), created="2026-10-07T00:00:00Z",
    )


@pytest.fixture()
def serve(monkeypatch, reference, tmp_path):
    """A test client for the service, configured the way a real run would be."""
    path = tmp_path / "reference.json"
    reference_mod.save_reference(path, reference)

    def make(**env):
        env = {"M2_REFERENCE_PATH": str(path), **env}
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        main_mod.reset_state()
        return TestClient(main_mod.app)

    return make


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def test_with_a_reference_loaded_the_service_scores_with_the_ruler(serve):
    client = serve()
    ok = client.post("/internal/anomalies/evaluate", json=body(healthy()))
    assert ok.status_code == 200
    event = AnomalyEvent.model_validate(ok.json())
    assert event.model == "z_score_per_service" and event.severity == "low"
    flagged = AnomalyEvent.model_validate(client.post("/internal/anomalies/evaluate", json=body(bad(15))).json())
    assert flagged.severity == "high"


def test_health_says_which_detector_is_running_and_what_it_depends_on(serve):
    health = serve().get("/health").json()
    assert health["status"] == "ok" and health["detector"] == "z_score_per_service"
    assert health["dependencies"] == {"m1": "disabled", "shared_api": "disabled", "reference": "loaded"}


def test_a_missing_reference_is_fine_and_the_threshold_alarm_takes_over(serve, tmp_path):
    client = serve(M2_REFERENCE_PATH=str(tmp_path / "does-not-exist.json"))
    health = client.get("/health").json()
    assert health["status"] == "ok" and health["detector"] == "threshold_baseline"
    assert health["dependencies"]["reference"] == "missing"
    assert client.post("/internal/anomalies/evaluate", json=body(healthy())).json()["model"] == "threshold_baseline"


def test_a_broken_reference_is_survived_and_reported_not_crashed_on(serve, tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{ this is not json")
    client = serve(M2_REFERENCE_PATH=str(broken))
    health = client.get("/health").json()
    assert health["status"] == "degraded" and health["dependencies"]["reference"].startswith("invalid")
    assert client.post("/internal/anomalies/evaluate", json=body(bad())).status_code == 200


def test_a_reference_made_for_another_feature_order_degrades_health(serve, reference, tmp_path):
    path = tmp_path / "old.json"
    reference_mod.save_reference(path, reference)
    data = json.loads(path.read_text())
    data["feature_order"] = data["feature_order"][::-1]
    path.write_text(json.dumps(data))
    health = serve(M2_REFERENCE_PATH=str(path)).get("/health").json()
    assert health["status"] == "degraded" and "different feature order" in health["dependencies"]["reference"]


def test_a_fault_over_many_readings_is_one_incident_candidate(serve):
    client = serve()
    client.post("/internal/anomalies/evaluate", json=body(healthy(0)))
    for i in range(1, 9):
        client.post("/internal/anomalies/evaluate", json=body(bad(15 * i)))
    listed = client.get("/internal/correlation/incidents").json()
    assert len(listed) == 1
    incident = Incident.model_validate(listed[0])
    assert incident.status == "DETECTED" and len(incident.anomaly_ids) == 8


def test_an_incident_comes_with_the_evidence_behind_it(serve):
    client = serve()
    for i in range(3):
        client.post("/internal/anomalies/evaluate", json=body(bad(15 * i)))
    incident_id = client.get("/internal/correlation/incidents").json()[0]["incident_id"]
    detail = client.get(f"/internal/correlation/incidents/{incident_id}").json()
    assert detail["alert_count"] == 3 and len(detail["events"]) == 3
    assert {"latency_p95_ms", "http_5xx_rate"} <= set(detail["signals"])
    assert set(detail["events"][0]["features"]) >= {"memory", "replica_count", "request_rate_change"}
    assert detail["peak_score"] > 0.99


def test_an_unknown_incident_is_a_404_with_the_shared_error_shape(serve):
    r = serve().get("/internal/correlation/incidents/INC-nope")
    assert r.status_code == 404
    assert r.json()["detail"] == {"category": "unknown_incident", "message": "no incident 'INC-nope'", "retryable": False}


def test_recent_alerts_are_newest_first_and_filterable(serve):
    client = serve()
    for i in range(4):
        client.post("/internal/anomalies/evaluate", json=body(bad(15 * i)))
    client.post("/internal/anomalies/evaluate", json=body(healthy(1000)))            # long after: not an alert
    recent = client.get("/internal/anomalies/recent").json()
    assert len(recent) == 4
    assert [e["timestamp"] for e in recent] == sorted((e["timestamp"] for e in recent), reverse=True)
    assert len(client.get("/internal/anomalies/recent?limit=2").json()) == 2
    assert client.get("/internal/anomalies/recent?service=other").json() == []
    assert client.get("/internal/anomalies/recent?limit=0").status_code == 422


def test_alerts_are_saved_as_evidence_when_a_path_is_set(serve, tmp_path):
    evidence = tmp_path / "evidence.jsonl"
    client = serve(M2_EVIDENCE_PATH=str(evidence))
    client.post("/internal/anomalies/evaluate", json=body(healthy(0)))
    client.post("/internal/anomalies/evaluate", json=body(bad(15)))
    lines = evidence.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["service"] == SERVICE


# ---------------------------------------------------------------------------
# A fake M1 that answers the snapshot endpoint from a script
# ---------------------------------------------------------------------------

class ScriptedM1:
    """Answers GET /internal/telemetry/snapshot with the next scripted response
    (the last one repeats). A response is a TelemetrySnapshot, or
    ("error", status, category), or ("raw", dict)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                step = outer.script[min(outer.calls, len(outer.script) - 1)]
                outer.calls += 1
                if isinstance(step, tuple) and step[0] == "error":
                    status, payload = step[1], {"detail": {"category": step[2], "message": f"M1 says {step[2]}", "retryable": True}}
                elif isinstance(step, tuple) and step[0] == "raw":
                    status, payload = 200, step[1]
                else:
                    status, payload = 200, step.model_dump(mode="json")
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def m1():
    created = []

    def make(script):
        server = ScriptedM1(script)
        created.append(server)
        return server

    yield make
    for server in created:
        server.close()


def make_poller(server, reference, services=(SERVICE,), now_offset_s=0, stale_after_s=120):
    pipe = pipeline_mod.Pipeline(reference)
    poller = poller_mod.Poller(
        client_mod.M1Client(server.url, timeout=2), pipe, list(services),
        stale_after_s=stale_after_s, now=lambda: T0 + timedelta(seconds=now_offset_s),
    )
    return poller, pipe


# ---------------------------------------------------------------------------
# The poller: good data
# ---------------------------------------------------------------------------

def test_a_polled_reading_is_scored_and_marked_ok(m1, reference):
    poller, pipe = make_poller(m1([bad(0)]), reference)
    (reading,) = poller.poll_once()
    assert reading.alert and pipe.latest(SERVICE).anomaly_id == reading.event.anomaly_id
    assert poller.status[SERVICE]["state"] == "ok" and poller.status[SERVICE]["last_ok"]
    assert poller.dependency_state() == "ok"


def test_the_same_reading_repeated_is_scored_only_once(m1, reference):
    poller, pipe = make_poller(m1([bad(0)]), reference)      # M1 repeats the last reading until there is a newer one
    assert len(poller.poll_once()) == 1
    assert poller.poll_once() == [] and poller.poll_once() == []
    assert len(pipe.recent_alerts()) == 1
    assert poller.status[SERVICE]["state"] == "ok"


def test_new_readings_keep_flowing_and_form_one_incident(m1, reference):
    poller, pipe = make_poller(m1([bad(0), bad(15), bad(30), bad(45)]), reference, now_offset_s=45)
    for _ in range(4):
        poller.poll_once()
    assert len(pipe.incidents()) == 1 and len(pipe.recent_alerts()) == 4


# ---------------------------------------------------------------------------
# The poller: bad data (runbook Day 12). Never a reading, never a made-up one
# ---------------------------------------------------------------------------

def test_a_stale_reading_is_ignored_and_says_so(m1, reference):
    poller, pipe = make_poller(m1([bad(0)]), reference, now_offset_s=600, stale_after_s=120)
    assert poller.poll_once() == []
    assert poller.status[SERVICE]["state"] == "stale" and "600s old" in poller.status[SERVICE]["message"]
    assert pipe.latest(SERVICE) is None and pipe.incidents() == []
    assert poller.dependency_state().startswith("degraded") and SERVICE in poller.dependency_state()


def test_an_error_from_m1_keeps_m1s_own_category_and_scores_nothing(m1, reference):
    poller, pipe = make_poller(m1([("error", 409, "stale_data")]), reference)
    assert poller.poll_once() == []
    assert poller.status[SERVICE]["state"] == "stale_data"
    assert pipe.latest(SERVICE) is None and pipe.incidents() == []


def test_an_unreachable_m1_is_reported_and_scores_nothing(reference):
    pipe = pipeline_mod.Pipeline(reference)
    poller = poller_mod.Poller(client_mod.M1Client("http://127.0.0.1:1", timeout=1), pipe, [SERVICE], now=lambda: T0)
    assert poller.poll_once() == []
    assert poller.status[SERVICE]["state"] == "unreachable" and pipe.latest(SERVICE) is None


def test_a_malformed_reading_is_rejected_and_scores_nothing(m1, reference):
    good = healthy(0).model_dump(mode="json")
    good["metrics"]["service_health"] = "ok"                      # a field outside the frozen contract
    poller, pipe = make_poller(m1([("raw", good)]), reference)
    assert poller.poll_once() == []
    assert poller.status[SERVICE]["state"] == "bad_response" and pipe.latest(SERVICE) is None


def test_one_failing_service_does_not_stop_the_others(m1, reference):
    class Router:
        def __init__(self):
            self.calls = 0

        def snapshot(self, service):
            if service == "broken-service":
                raise client_mod.M1Error("down", category="unreachable")
            return bad(0)

    pipe = pipeline_mod.Pipeline(reference)
    poller = poller_mod.Poller(Router(), pipe, ["broken-service", SERVICE], now=lambda: T0)
    readings = poller.poll_once()
    assert [r.event.service for r in readings] == [SERVICE]
    assert poller.status["broken-service"]["state"] == "unreachable" and poller.status[SERVICE]["state"] == "ok"
    assert "broken-service unreachable" in poller.dependency_state()


def test_the_poller_recovers_when_m1_comes_back(m1, reference):
    poller, pipe = make_poller(m1([("error", 503, "unreachable"), ("error", 503, "unreachable"), bad(0)]), reference)
    poller.poll_once()
    poller.poll_once()
    assert poller.dependency_state().startswith("degraded")
    assert len(poller.poll_once()) == 1
    assert poller.dependency_state() == "ok" and poller.status[SERVICE]["state"] == "ok"


def test_the_polling_loop_survives_an_unexpected_error():
    calls = []

    class Flaky(poller_mod.Poller):
        def poll_once(self):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("something unexpected")
            return []

    flaky = Flaky(client=None, pipeline=None, services=[])

    async def run_briefly():
        task = asyncio.create_task(flaky.run(0.02))
        await asyncio.sleep(0.25)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_briefly())
    assert len(calls) >= 3                       # the first error did not stop it


# ---------------------------------------------------------------------------
# Live polling wired into the service
# ---------------------------------------------------------------------------

def test_the_service_polls_m1_by_itself_and_reports_health_honestly(m1, serve):
    script = [bad(15 * i) for i in range(1, 40)]
    server = m1(script)
    client = serve(M2_POLL_ENABLED="true", M1_TELEMETRY_BASE_URL=server.url, M2_POLL_INTERVAL_S="0.05",
                   M2_STALE_AFTER_S="1000000000", M2_POLL_SERVICES=SERVICE)
    with client:                                  # entering starts the background polling
        deadline = time.time() + 5
        while time.time() < deadline and len(client.get("/internal/anomalies/recent").json()) < 3:
            time.sleep(0.05)
        recent = client.get("/internal/anomalies/recent").json()
        health = client.get("/health").json()
        incidents = client.get("/internal/correlation/incidents").json()
    assert len(recent) >= 3
    assert health["status"] == "ok" and health["dependencies"]["m1"] == "ok"
    assert len(incidents) == 1                    # many alerts, one incident
    calls_after_shutdown = server.calls
    time.sleep(0.3)
    assert server.calls == calls_after_shutdown   # leaving the block stopped the polling


def test_a_service_polling_an_unreachable_m1_reports_degraded_and_still_answers_evaluate(serve):
    client = serve(M2_POLL_ENABLED="true", M1_TELEMETRY_BASE_URL="http://127.0.0.1:1", M2_POLL_INTERVAL_S="0.05")
    with client:
        deadline = time.time() + 5
        while time.time() < deadline and client.get("/health").json()["status"] != "degraded":
            time.sleep(0.05)
        health = client.get("/health").json()
        direct = client.post("/internal/anomalies/evaluate", json=body(healthy()))
    assert health["status"] == "degraded" and "unreachable" in health["dependencies"]["m1"]
    assert direct.status_code == 200             # M2 can still score what it is sent
