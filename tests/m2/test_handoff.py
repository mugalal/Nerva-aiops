"""
Tests for handing M2's incidents to the shared API, for the route M3 calls on
M2, and for the shared settings and JSON logging.

The shared API is replaced by a stand-in that enforces the same rules as the
team's real incident and anomaly routes (see fake_shared_api.py), served over
real HTTP.

Needs numpy and scikit-learn:   pip install scikit-learn numpy
Run from the repo root:         python -m pytest tests/m2 -v
"""

import io
import json
import logging
import random
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import m2_loader
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from fake_shared_api import make_fake_shared_api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from real_like import SERVICE, normal_rows, snapshot  # noqa: E402
from serve import Served  # noqa: E402

from shared.contracts import AnomalyEvent  # noqa: E402

model_mod = m2_loader.module("model")
reference_mod = m2_loader.module("reference")
pipeline_mod = m2_loader.module("pipeline")
handoff_mod = m2_loader.module("handoff")
main_mod = m2_loader.module("main")
cli = m2_loader.module("realdata.__main__")


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def reference():
    detector = model_mod.ZScoreDetector(min_samples=20).fit({SERVICE: normal_rows(120, seed=1)})
    return reference_mod.Reference(
        detector=detector,
        alert=reference_mod.AlertLine(wobbles=80.0, normal_max_wobbles=13.0, fault_min_wobbles=480.0),
        trained_on=("capture1.json",), created="2026-10-07T00:00:00Z",
    )


@pytest.fixture(scope="module")
def shared_server():
    app, state = make_fake_shared_api()
    server = Served(app)
    yield server, state
    server.close()


@pytest.fixture()
def shared(shared_server):
    server, state = shared_server
    state.incidents.clear(); state.anomalies.clear(); state.log.clear()
    state.fail_next = 0
    state.clock_offset_s = 0.0
    return SimpleNamespace(url=server.url, state=state)


def ago(seconds: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def bad(when, seed=0, scale=1.0):
    return snapshot(when, kind="bad", progress=1.0, rng=random.Random(seed), fault_scale=scale)


def healthy(when, seed=0):
    return snapshot(when, rng=random.Random(seed))


def posts(shared, path):
    return [r for r in shared.state.log if r["method"] == "POST" and r["path"] == path]


def feed(pipe, count, start_ago=300, scales=None):
    """`count` bad readings, 15 s apart, all in the past."""
    readings = []
    for i in range(count):
        scale = 1.0 if scales is None else scales[i]
        readings.append(pipe.process(bad(ago(start_ago - 15 * i), seed=i, scale=scale)))
    return readings


def make_pipe(reference, shared, **kw):
    kw.setdefault("handoff_retry_s", 0.0)
    return pipeline_mod.Pipeline(reference, handoff=handoff_mod.SharedApiHandoff(shared.url, timeout=3), **kw)


def event(when, aid="ANO-1", score=0.97, service=SERVICE, **features):
    return AnomalyEvent.model_validate({
        "anomaly_id": aid, "timestamp": when, "service": service, "score": score, "severity": "high",
        "model": "z_score_per_service",
        "features": {"request_rate": 13.0, "latency_p95_ms": 690.0, "http_5xx_rate": 0.13, "cpu": 0.047, **features},
    })


def incident_for(when, incident_id="INC-1", service=SERVICE, severity="high"):
    from shared.contracts import Incident
    return Incident(incident_id=incident_id, started_at=when, status="DETECTED", severity=severity,
                    affected_services=[service], anomaly_ids=[])


# ---------------------------------------------------------------------------
# The client: exactly what the team's operator script sends
# ---------------------------------------------------------------------------

def test_the_incident_is_created_with_exactly_the_five_documented_fields_at_the_exact_path(shared):
    handoff_mod.SharedApiHandoff(shared.url).create_incident(incident_for(ago(60)))
    (request,) = posts(shared, "/api/incidents/")                # the trailing slash is part of the contract
    assert set(request["body"]) == {"incident_id", "started_at", "severity", "affected_services", "anomaly_ids"}
    assert request["body"]["anomaly_ids"] == [] and request["body"]["affected_services"] == [SERVICE]
    assert request["body"]["severity"] == "high" and request["body"]["started_at"].endswith("Z")
    assert shared.state.incidents[0]["status"] == "DETECTED"


def test_an_incident_start_in_the_future_is_capped_at_now_because_the_shared_api_would_refuse_it(shared):
    handoff_mod.SharedApiHandoff(shared.url).create_incident(incident_for(ago(-30)))     # M1's clock is 30 s ahead
    assert len(shared.state.incidents) == 1                                              # accepted, not 422


def test_creating_an_incident_that_already_exists_counts_as_done(shared):
    client = handoff_mod.SharedApiHandoff(shared.url)
    client.create_incident(incident_for(ago(60)))
    client.create_incident(incident_for(ago(60)))                                        # 409 underneath, no error
    assert len(shared.state.incidents) == 1


def test_an_anomaly_is_sent_unchanged_and_is_what_the_shared_api_serves_back(shared):
    client = handoff_mod.SharedApiHandoff(shared.url)
    client.create_incident(incident_for(ago(60)))
    sent = event(ago(30))
    client.link_anomaly("INC-1", sent)
    (request,) = posts(shared, "/internal/anomalies")
    assert request["body"] == sent.model_dump(mode="json") and request["query"] == "INC-1"
    assert shared.state.incidents[0]["anomaly_ids"] == ["ANO-1"]


def test_sending_the_same_anomaly_twice_is_harmless(shared):
    client = handoff_mod.SharedApiHandoff(shared.url)
    client.create_incident(incident_for(ago(60)))
    same = event(ago(30))                                      # one event, sent twice
    client.link_anomaly("INC-1", same)
    client.link_anomaly("INC-1", same)
    assert shared.state.incidents[0]["anomaly_ids"] == ["ANO-1"]


def test_the_same_id_with_different_evidence_is_refused_not_overwritten(shared):
    client = handoff_mod.SharedApiHandoff(shared.url)
    client.create_incident(incident_for(ago(60)))
    client.link_anomaly("INC-1", event(ago(30)))
    with pytest.raises(handoff_mod.HandoffError, match="different evidence") as caught:
        client.link_anomaly("INC-1", event(ago(20), score=0.5))   # same id, other contents
    assert caught.value.status == 409


@pytest.mark.parametrize("make,status,fragment", [
    (lambda: ("INC-NOPE", event(ago(30))), 404, "Incident not found"),
    (lambda: ("INC-1", event(ago(30), service="other-service")), 409, "not affected"),
    (lambda: ("INC-1", event(ago(-60))), 422, "future"),
])
def test_a_refused_anomaly_says_why_and_with_what_status(shared, make, status, fragment):
    client = handoff_mod.SharedApiHandoff(shared.url)
    client.create_incident(incident_for(ago(60)))
    incident_id, bad_event = make()
    with pytest.raises(handoff_mod.HandoffError) as caught:
        client.link_anomaly(incident_id, bad_event)
    assert caught.value.status == status and fragment in str(caught.value)


def test_an_unreachable_shared_api_is_an_error_not_a_crash():
    with pytest.raises(handoff_mod.HandoffError, match="unreachable"):
        handoff_mod.SharedApiHandoff("http://127.0.0.1:1", timeout=1).create_incident(incident_for(ago(60)))


def test_an_event_with_a_value_that_cannot_be_sent_is_refused_before_anything_leaves(shared):
    client = handoff_mod.SharedApiHandoff(shared.url)
    client.create_incident(incident_for(ago(60)))
    shared.state.log.clear()
    with pytest.raises(handoff_mod.HandoffError, match="NaN"):
        client.link_anomaly("INC-1", event(ago(30), cpu=float("nan")))
    assert shared.state.log == []


def test_a_missing_trailing_slash_would_fail_which_is_why_the_exact_path_is_used(shared):
    """The real server answers 307 to a POST without the slash; urllib does not follow it."""
    import urllib.error
    import urllib.request
    request = urllib.request.Request(shared.url + "/api/incidents", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request)
    assert caught.value.code == 307


# ---------------------------------------------------------------------------
# The pipeline's policy: onset at once, the strongest once the incident settles
# ---------------------------------------------------------------------------

def test_nothing_is_sent_when_the_handoff_is_switched_off(reference, shared):
    pipe = pipeline_mod.Pipeline(reference)
    feed(pipe, 5)
    assert shared.state.log == [] and pipe.handoff_status() == "disabled"


def test_the_first_alert_creates_the_incident_and_links_that_alert(reference, shared):
    pipe = make_pipe(reference, shared)
    (first,) = feed(pipe, 1)
    (incident,) = shared.state.incidents
    assert incident["incident_id"] == first.incident.incident_id
    assert incident["anomaly_ids"] == [first.event.anomaly_id]
    assert incident["severity"] == "high" and incident["affected_services"] == [SERVICE]
    assert pipe.handoff_status() == "ok"


def test_m3_sees_the_first_alert_until_the_incident_settles(reference, shared):
    import urllib.request
    pipe = make_pipe(reference, shared)                       # settles after 4 alerts
    readings = feed(pipe, 3)
    incident_id = readings[0].incident.incident_id
    assert len(posts(shared, "/internal/anomalies")) == 1      # only the onset so far
    with urllib.request.urlopen(f"{shared.url}/internal/anomalies?incident_id={incident_id}") as response:
        what_m3_reads = json.loads(response.read())
    assert what_m3_reads["anomaly_id"] == readings[0].event.anomaly_id


def test_after_settling_what_m3_reads_is_the_strongest_alert_not_the_half_developed_first_one(reference, shared):
    import urllib.request
    pipe = make_pipe(reference, shared)
    readings = feed(pipe, 6, scales=[0.03, 0.4, 0.7, 1.0, 1.0, 1.0])      # the first alert is only just over the line
    incident_id = readings[0].incident.incident_id
    with urllib.request.urlopen(f"{shared.url}/internal/anomalies?incident_id={incident_id}") as response:
        what_m3_reads = json.loads(response.read())
    first_score = readings[0].event.score
    assert what_m3_reads["anomaly_id"] != readings[0].event.anomaly_id
    assert what_m3_reads["score"] > first_score


def test_once_settled_the_strongest_alert_is_linked_and_becomes_what_m3_reads(reference, shared):
    pipe = make_pipe(reference, shared)
    scales = [0.3, 0.5, 0.8, 1.0, 1.0, 1.0]                   # the fault develops
    readings = feed(pipe, 6, scales=scales)
    incident_id = readings[0].incident.incident_id
    candidate = pipe.correlator.get(incident_id)
    linked = shared.state.incidents[0]["anomaly_ids"]
    assert len(linked) == 2                                   # the onset and one strongest alert
    assert linked[0] == readings[0].event.anomaly_id
    stronger = shared.state.anomalies[linked[1]]
    assert stronger["score"] >= shared.state.anomalies[linked[0]]["score"]
    assert linked[1] in candidate.handoff.linked and candidate.handoff.peak_done


def test_after_the_peak_is_linked_later_alerts_send_nothing(reference, shared):
    pipe = make_pipe(reference, shared)
    feed(pipe, 6)
    sent_so_far = len(shared.state.log)
    feed(pipe, 6, start_ago=200)
    assert len(shared.state.log) == sent_so_far


def test_when_the_first_alert_is_already_the_strongest_no_second_anomaly_is_sent(reference, shared):
    pipe = make_pipe(reference, shared)
    feed(pipe, 6, scales=[1.0, 0.2, 0.2, 0.2, 0.2, 0.2])
    assert len(posts(shared, "/internal/anomalies")) == 1
    assert len(shared.state.incidents[0]["anomaly_ids"]) == 1


def test_a_second_fault_after_a_long_quiet_spell_is_a_second_incident_on_the_shared_api(reference, shared):
    pipe = make_pipe(reference, shared)
    feed(pipe, 2, start_ago=900)
    feed(pipe, 2, start_ago=100)                              # 800 s later: well past the 120 s gap
    assert len(shared.state.incidents) == 2
    assert len({i["incident_id"] for i in shared.state.incidents}) == 2


# ---------------------------------------------------------------------------
# Failures never stop scoring, and the handoff is retried
# ---------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.moment = datetime.now(timezone.utc)

    def __call__(self):
        return self.moment

    def advance(self, seconds):
        self.moment += timedelta(seconds=seconds)


def test_a_failed_delivery_is_recorded_reported_and_retried_later_without_affecting_scoring(reference, shared):
    clock = Clock()
    pipe = make_pipe(reference, shared, handoff_retry_s=10.0, now=clock)
    shared.state.fail_next = 1                                # the shared API is unwell for one request

    first = pipe.process(bad(ago(100), seed=1))
    assert first.alert and first.event.severity == "high"      # scoring carried on regardless
    assert shared.state.incidents == []
    assert pipe.handoff_status().startswith("degraded") and "503" in pipe.handoff_status()
    candidate = pipe.correlator.get(first.incident.incident_id)
    assert candidate.handoff.error and candidate.handoff.attempts == 1

    requests_before = len(shared.state.log)
    pipe.process(bad(ago(85), seed=2))                          # a moment later: too soon to retry
    assert len(shared.state.log) == requests_before

    clock.advance(11)
    pipe.process(bad(ago(70), seed=3))                          # past the retry interval: tries again
    assert len(shared.state.incidents) == 1 and pipe.handoff_status() == "ok"
    assert candidate.handoff.error is None and candidate.handoff.incident_created


def test_a_half_finished_delivery_resumes_where_it_stopped(reference, shared):
    pipe = make_pipe(reference, shared)
    shared.state.clock_offset_s = -600                          # the shared API's clock is 10 minutes behind
    first = pipe.process(bad(ago(30), seed=1))
    # the incident start is capped at "now" (the shared API's now is far earlier), so even that is refused
    assert pipe.handoff_status().startswith("degraded")
    shared.state.clock_offset_s = 0                             # clocks agree again
    pipe.process(bad(ago(15), seed=2))
    assert shared.state.incidents and pipe.handoff_status() == "ok"
    assert first.event.anomaly_id in shared.state.incidents[0]["anomaly_ids"]


def test_an_alert_the_shared_api_calls_future_is_retried_once_the_time_has_passed(reference, shared):
    pipe = make_pipe(reference, shared)
    shared.state.clock_offset_s = -20                           # the shared API is 20 s behind M1
    pipe.process(bad(ago(10), seed=1))                          # incident accepted (capped), anomaly "in the future"
    assert shared.state.incidents == [] or shared.state.incidents[0]["anomaly_ids"] == []
    assert pipe.handoff_status().startswith("degraded") and "future" in pipe.handoff_status()
    shared.state.clock_offset_s = 0
    pipe.process(bad(ago(5), seed=2))
    assert len(shared.state.incidents[0]["anomaly_ids"]) >= 1 and pipe.handoff_status() == "ok"


def test_an_unreachable_shared_api_degrades_health_but_scoring_continues(reference):
    pipe = pipeline_mod.Pipeline(reference, handoff=handoff_mod.SharedApiHandoff("http://127.0.0.1:1", timeout=1),
                                 handoff_retry_s=0)
    readings = feed(pipe, 3)
    assert all(r.alert for r in readings) and pipe.handoff_status().startswith("degraded")
    assert len(pipe.incidents()) == 1                           # M2's own correlation is unaffected


def test_many_threads_alerting_at_once_create_the_incident_exactly_once(reference, shared):
    class Slow(handoff_mod.SharedApiHandoff):
        def create_incident(self, incident, now=None):
            time.sleep(0.3)                                     # long enough for the others to arrive
            super().create_incident(incident, now)

    pipe = pipeline_mod.Pipeline(reference, handoff=Slow(shared.url, timeout=5), handoff_retry_s=0)
    errors = []

    def worker(i):
        try:
            pipe.process(bad(ago(100 - 5 * i), seed=i))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(posts(shared, "/api/incidents/")) == 1
    assert len(shared.state.incidents) == 1


# ---------------------------------------------------------------------------
# The route M3 calls on M2
# ---------------------------------------------------------------------------

@pytest.fixture()
def serve(monkeypatch, reference, tmp_path):
    path = tmp_path / "reference.json"
    reference_mod.save_reference(path, reference)

    def make(**env):
        for name, value in {"M2_REFERENCE_PATH": str(path), **env}.items():
            monkeypatch.setenv(name, value)
        main_mod.reset_state()
        return TestClient(main_mod.app)

    return make


def body(snap):
    return snap.model_dump(mode="json")


def open_incident(client, count=6, scales=None):
    for i in range(count):
        scale = 1.0 if scales is None else scales[i]
        client.post("/internal/anomalies/evaluate", json=body(bad(ago(300 - 15 * i), seed=i, scale=scale)))
    return client.get("/internal/correlation/incidents").json()[0]["incident_id"]


def test_the_route_m3_calls_returns_one_anomaly_event_in_exactly_the_frozen_shape(serve):
    client = serve()
    incident_id = open_incident(client)
    r = client.get("/internal/anomalies", params={"incident_id": incident_id})
    assert r.status_code == 200
    assert set(r.json()) == {"anomaly_id", "timestamp", "service", "score", "severity", "model", "features"}
    assert set(r.json()["features"]) == {"request_rate", "latency_p95_ms", "http_5xx_rate", "cpu"}
    AnomalyEvent.model_validate(r.json())


def test_the_route_can_return_the_first_the_strongest_or_the_latest_alert(serve):
    client = serve()
    incident_id = open_incident(client, scales=[0.3, 0.6, 1.0, 1.0, 0.9, 0.8])
    get = lambda pick: client.get("/internal/anomalies", params={"incident_id": incident_id, "pick": pick}).json()
    first, peak, latest = get("first"), get("peak"), get("latest")
    assert first["timestamp"] < latest["timestamp"]
    assert peak["score"] >= first["score"] and peak["score"] >= latest["score"]
    assert client.get("/internal/anomalies", params={"incident_id": incident_id}).json() == peak     # the default


def test_the_route_answers_unknown_and_missing_ids_with_clear_errors(serve):
    client = serve()
    unknown = client.get("/internal/anomalies", params={"incident_id": "INC-NOPE"})
    assert unknown.status_code == 404 and unknown.json()["detail"]["category"] == "unknown_incident"
    assert client.get("/internal/anomalies").status_code == 422
    assert client.get("/internal/anomalies", params={"incident_id": "x", "pick": "oldest"}).status_code == 422


def test_the_older_endpoints_still_work_beside_the_new_route(serve):
    client = serve()
    open_incident(client, count=3)
    assert client.get("/internal/anomalies/recent").status_code == 200
    assert client.get("/internal/correlation/incidents").status_code == 200
    assert client.post("/internal/anomalies/evaluate", json=body(healthy(ago(1)))).status_code == 200


# ---------------------------------------------------------------------------
# The service wiring
# ---------------------------------------------------------------------------

def test_with_the_handoff_switched_on_the_service_gives_the_shared_api_its_incident(serve, shared):
    client = serve(M2_HANDOFF_ENABLED="true", M2_HANDOFF_URL=shared.url)
    incident_id = open_incident(client, count=6)
    assert [i["incident_id"] for i in shared.state.incidents] == [incident_id]
    assert len(shared.state.incidents[0]["anomaly_ids"]) == 2
    assert client.get("/health").json()["dependencies"]["shared_api"] == "ok"
    detail = client.get(f"/internal/correlation/incidents/{incident_id}").json()
    assert detail["handoff"]["incident_created"] and len(detail["handoff"]["linked_anomaly_ids"]) == 2
    assert detail["handoff"]["error"] is None


def test_the_shared_settings_name_is_used_when_no_m2_specific_address_is_given(serve, shared):
    client = serve(M2_HANDOFF_ENABLED="true", SHARED_NEXUS_API_BASE_URL=shared.url)
    open_incident(client, count=2)
    assert len(shared.state.incidents) == 1


def test_by_default_the_service_sends_nothing_anywhere(serve, shared):
    client = serve(SHARED_NEXUS_API_BASE_URL=shared.url)       # an address alone does not switch it on
    open_incident(client, count=6)
    assert shared.state.log == []
    assert client.get("/health").json()["dependencies"]["shared_api"] == "disabled"


def test_an_unreachable_shared_api_degrades_health_and_the_service_still_answers(serve):
    client = serve(M2_HANDOFF_ENABLED="true", M2_HANDOFF_URL="http://127.0.0.1:1")
    r = client.post("/internal/anomalies/evaluate", json=body(bad(ago(30))))
    assert r.status_code == 200 and r.json()["severity"] == "high"
    health = client.get("/health").json()
    assert health["status"] == "degraded" and health["dependencies"]["shared_api"].startswith("degraded")


# ---------------------------------------------------------------------------
# Shared settings and JSON logging
# ---------------------------------------------------------------------------

def test_the_service_identity_comes_from_the_shared_settings(serve, monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "m2-prod")
    monkeypatch.setenv("SERVICE_VERSION", "2.0.1")
    monkeypatch.setenv("ENVIRONMENT", "staging")
    health = serve().get("/health").json()
    assert (health["service"], health["version"], health["environment"]) == ("m2-prod", "2.0.1", "staging")


def test_starting_the_service_switches_on_structured_json_logs_with_the_shared_fields(serve, monkeypatch):
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    stream = io.StringIO()
    try:
        monkeypatch.setattr(sys, "stderr", stream)
        monkeypatch.setenv("M2_JSON_LOGGING", "on")
        monkeypatch.setenv("SERVICE_VERSION", "7.7.7")
        monkeypatch.setenv("ENVIRONMENT", "staging")
        monkeypatch.setenv("LOG_LEVEL", "info")
        client = serve()
        with client:                                            # starting up configures the logging
            logging.getLogger("m2.pipeline").info(
                "hello", extra={"incident_id": "INC-9", "service_name": SERVICE, "provider": "m2"})
        line = next(l for l in stream.getvalue().splitlines() if '"hello"' in l)
        record = json.loads(line)
        assert record["service"] == "anomaly-engine" and record["version"] == "7.7.7"
        assert record["environment"] == "staging" and record["level"] == "INFO" and record["message"] == "hello"
        assert record["incident_id"] == "INC-9" and record["provider"] == "m2" and "timestamp" in record
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_opening_an_incident_is_logged_with_its_id_and_service(reference, caplog):
    pipe = pipeline_mod.Pipeline(reference)
    with caplog.at_level(logging.INFO, logger="m2.pipeline"):
        reading = pipe.process(bad(ago(30)))
    opened = [r for r in caplog.records if r.getMessage() == "incident opened"]
    assert len(opened) == 1 and opened[0].incident_id == reading.incident.incident_id
    assert opened[0].service_name == SERVICE


def test_a_failed_handoff_is_logged_as_a_warning_with_an_error_category(reference, caplog):
    pipe = pipeline_mod.Pipeline(reference, handoff=handoff_mod.SharedApiHandoff("http://127.0.0.1:1", timeout=1),
                                 handoff_retry_s=0)
    with caplog.at_level(logging.INFO, logger="m2.pipeline"):
        pipe.process(bad(ago(30)))
    failed = [r for r in caplog.records if r.getMessage().startswith("handoff failed")]
    assert failed and failed[0].levelno == logging.WARNING and failed[0].error_category == "handoff_failed"
    assert failed[0].provider == "shared-api"


# ---------------------------------------------------------------------------
# The manual command: give the shared API an incident from the running M2
# ---------------------------------------------------------------------------

@pytest.fixture()
def running_m2(serve):
    client = serve()
    server = Served(main_mod.app)
    yield SimpleNamespace(client=client, url=server.url)
    server.close()


def test_the_handoff_command_creates_links_and_shows_what_m3_will_read(running_m2, shared, capsys):
    open_incident(running_m2.client, count=6, scales=[0.3, 0.6, 1.0, 1.0, 1.0, 1.0])
    code = cli.main(["handoff", "--m2-url", running_m2.url, "--to", shared.url])
    out = capsys.readouterr().out
    assert code == 0
    assert "created the incident" in out and "linked the first alert" in out and "linked the strongest" in out
    assert "M3 will read:" in out and "model=z_score_per_service" in out
    (incident,) = shared.state.incidents
    assert len(incident["anomaly_ids"]) == 2


def test_running_the_handoff_command_twice_is_harmless(running_m2, shared, capsys):
    open_incident(running_m2.client, count=5)
    assert cli.main(["handoff", "--m2-url", running_m2.url, "--to", shared.url]) == 0
    assert cli.main(["handoff", "--m2-url", running_m2.url, "--to", shared.url]) == 0
    assert len(shared.state.incidents) == 1 and len(shared.state.incidents[0]["anomaly_ids"]) == 2


def test_the_handoff_command_can_pick_an_incident_by_id(running_m2, shared, capsys):
    incident_id = open_incident(running_m2.client, count=3)
    assert cli.main(["handoff", "--m2-url", running_m2.url, "--to", shared.url, "--incident", incident_id]) == 0
    assert shared.state.incidents[0]["incident_id"] == incident_id


def test_the_handoff_command_explains_when_there_is_nothing_to_hand_over(running_m2, shared, capsys):
    assert cli.main(["handoff", "--m2-url", running_m2.url, "--to", shared.url]) == 2
    assert "no incident candidates yet" in capsys.readouterr().out
    open_incident(running_m2.client, count=2)
    assert cli.main(["handoff", "--m2-url", running_m2.url, "--to", shared.url, "--incident", "INC-NOPE"]) == 2
    assert "no incident 'INC-NOPE'" in capsys.readouterr().out


def test_the_handoff_command_explains_an_unreachable_shared_api_or_m2(running_m2, shared, capsys):
    open_incident(running_m2.client, count=2)
    assert cli.main(["handoff", "--m2-url", running_m2.url, "--to", "http://127.0.0.1:1"]) == 2
    assert "unreachable" in capsys.readouterr().out
    assert cli.main(["handoff", "--m2-url", "http://127.0.0.1:1", "--to", shared.url]) == 2
    assert "could not reach M2" in capsys.readouterr().out


def test_the_handoff_command_warns_when_the_evidence_is_old(running_m2, shared, capsys):
    for i in range(3):
        running_m2.client.post("/internal/anomalies/evaluate", json=body(bad(ago(3600 - 15 * i), seed=i)))
    assert cli.main(["handoff", "--m2-url", running_m2.url, "--to", shared.url]) == 0
    assert "minutes old" in capsys.readouterr().out
