"""
Tests for the Day-3 real-data tools: the M1 client, capture files, the drill,
the report, and the command line.

No Docker is needed. M1 is replaced by a small real HTTP server that answers
like M1 does, and the drill runs against a fake clock and a fake demo stack,
so the whole timeline (including the clean-up when it is interrupted) is
exercised for real.

Run from the repo root:   python -m pytest tests/m2 -v
"""

import dataclasses
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import m2_loader
import pytest
from pydantic import ValidationError

from shared.contracts import TelemetrySnapshot

baseline_mod = m2_loader.module("baseline")
scenarios = m2_loader.module("evaluation.scenarios")
metrics = m2_loader.module("evaluation.metrics")
client_mod = m2_loader.module("realdata.m1_client")
capture_mod = m2_loader.module("realdata.capture")
drill_mod = m2_loader.module("realdata.drill")
report_mod = m2_loader.module("realdata.report")
cli = m2_loader.module("realdata.__main__")

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
STEP = 15


# ---------------------------------------------------------------------------
# A fake M1: a real HTTP server that answers the way M1 does
# ---------------------------------------------------------------------------

class FakeM1:
    def __init__(self, snapshots, extra_metric=False):
        self.snapshots = list(snapshots)
        self.extra_metric = extra_metric
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                url = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(url.query).items()}
                if url.path == "/health":
                    self._send(200, {"service": "telemetry-intelligence", "status": "ok",
                                     "version": "0.1.0", "environment": "test",
                                     "provider_mode": "real", "dependencies": {}})
                elif url.path == "/internal/telemetry/window":
                    service = query["service"]
                    if service == "missing-service":
                        self._send(404, {"detail": {"category": "unknown_service",
                                                    "message": "no such service", "retryable": False}})
                        return
                    if service == "stale-service":
                        self._send(409, {"detail": {"category": "stale_data",
                                                    "message": "no recent samples", "retryable": True}})
                        return
                    start = datetime.fromisoformat(query["start"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(query["end"].replace("Z", "+00:00"))
                    points = []
                    for s in outer.snapshots:
                        if start <= s.timestamp <= end:
                            m = s.metrics.model_dump(mode="json")
                            if outer.extra_metric:
                                m["service_health"] = "ok"
                            points.append({"timestamp": s.timestamp.isoformat().replace("+00:00", "Z"),
                                           "metrics": m})
                    self._send(200, {"service": service, "version": "v1", "start": query["start"],
                                     "end": query["end"], "step_seconds": int(query["step_seconds"]),
                                     "points": points})
                elif url.path == "/internal/telemetry/snapshot":
                    self._send(200, outer.snapshots[-1].model_dump(mode="json"))
                else:
                    self._send(404, {"detail": "not found"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def timeline():
    """Three real-looking runs of payment-service laid end to end, plus the
    answer key, built from the synthetic generator but re-timed."""
    runs = [
        scenarios.generate_run(scenarios.PROFILES[0], "healthy", seed=1),
        scenarios.generate_run(scenarios.PROFILES[0], "bad_deployment", seed=2),
        scenarios.generate_run(scenarios.PROFILES[0], "traffic_spike", seed=3),
    ]
    points, specs = [], []
    cursor = T0
    for run in runs:
        stamped = []
        for i, snap in enumerate(run.snapshots):
            moment = cursor + timedelta(seconds=STEP * i)
            # M1 serves one version per window, so every reading here carries the same one
            stamped.append(snap.model_copy(update={"timestamp": moment, "service": "payment-service",
                                                   "version": "v1"}))
        points.extend(stamped)
        specs.append(capture_mod.RunSpec(
            run_id=f"{run.scenario}-1", scenario=run.scenario,
            start=stamped[0].timestamp, end=stamped[-1].timestamp,
            fault_start=None if run.fault_start is None else stamped[run.fault_start].timestamp,
        ))
        cursor = stamped[-1].timestamp + timedelta(seconds=300)     # a gap between runs
    capture = capture_mod.Capture(
        service="payment-service", step_seconds=STEP, m1_url="http://example", recorded_at=cursor,
        events=[{"at": "2026-10-07T12:00:00Z", "name": "drill_start"}], runs=specs, points=points,
    )
    return capture, runs


@pytest.fixture()
def fake_m1(timeline):
    server = FakeM1(timeline[0].points)
    yield server
    server.close()


# ---------------------------------------------------------------------------
# M1 client
# ---------------------------------------------------------------------------

def test_window_returns_the_snapshots_m1_serves(timeline, fake_m1):
    capture, _ = timeline
    got = client_mod.M1Client(fake_m1.url).window("payment-service", T0, capture.span()[1] + timedelta(seconds=15))
    assert got == sorted(capture.points, key=lambda s: s.timestamp)
    assert all(isinstance(s, TelemetrySnapshot) for s in got)


def test_window_only_returns_the_requested_range(timeline, fake_m1):
    got = client_mod.M1Client(fake_m1.url).window("payment-service", T0, T0 + timedelta(seconds=60))
    assert [s.timestamp for s in got] == [T0 + timedelta(seconds=15 * i) for i in range(5)]


def test_an_empty_window_is_an_error_not_an_empty_success(fake_m1):
    far = T0 + timedelta(days=30)
    with pytest.raises(client_mod.M1Error) as caught:
        client_mod.M1Client(fake_m1.url).window("payment-service", far, far + timedelta(minutes=5))
    assert caught.value.category == "no_data"


def test_m1_errors_keep_m1s_own_category_message_and_status(fake_m1):
    with pytest.raises(client_mod.M1Error) as caught:
        client_mod.M1Client(fake_m1.url).window("missing-service", T0, T0 + timedelta(minutes=1))
    err = caught.value
    assert (err.category, err.message, err.status, err.retryable) == ("unknown_service", "no such service", 404, False)
    assert "unknown_service" in str(err) and "404" in str(err)


def test_a_retryable_m1_error_says_so(fake_m1):
    with pytest.raises(client_mod.M1Error) as caught:
        client_mod.M1Client(fake_m1.url).window("stale-service", T0, T0 + timedelta(minutes=1))
    assert caught.value.retryable is True and caught.value.status == 409


def test_an_unreachable_m1_is_an_error_never_a_fallback():
    with pytest.raises(client_mod.M1Error) as caught:
        client_mod.M1Client("http://127.0.0.1:1", timeout=1).window("payment-service", T0, T0 + timedelta(minutes=1))
    assert caught.value.category == "unreachable" and caught.value.retryable


def test_a_metric_outside_the_frozen_contract_is_rejected(timeline):
    server = FakeM1(timeline[0].points, extra_metric=True)
    try:
        with pytest.raises(ValidationError):
            client_mod.M1Client(server.url).window("payment-service", T0, T0 + timedelta(minutes=1))
    finally:
        server.close()


def test_health_and_snapshot(timeline, fake_m1):
    m1 = client_mod.M1Client(fake_m1.url)
    assert m1.health()["status"] == "ok"
    assert m1.snapshot("payment-service") == timeline[0].points[-1]


@pytest.mark.parametrize("defect", ["service", "version", "step", "bounds", "duplicate", "order", "outside", "alignment"])
def test_history_adapter_rejects_mismatched_metadata_and_points(timeline, defect):
    points = [{"timestamp": point.timestamp.isoformat(), "metrics": point.metrics.model_dump()}
              for point in timeline[0].points[:3]]
    body = {"service": "payment-service", "version": "v1", "start": T0.isoformat(),
            "end": (T0 + timedelta(seconds=30)).isoformat(), "step_seconds": STEP, "points": points}
    if defect == "service":
        body["service"] = "wrong-service"
    elif defect == "version":
        body.pop("version")
    elif defect == "step":
        body["step_seconds"] = 30
    elif defect == "bounds":
        body["start"] = (T0 - timedelta(seconds=15)).isoformat()
    elif defect == "duplicate":
        body["points"] = [points[0], points[0]]
    elif defect == "order":
        body["points"] = points[::-1]
    elif defect == "outside":
        points[-1]["timestamp"] = (T0 + timedelta(seconds=45)).isoformat()
    elif defect == "alignment":
        points[1]["timestamp"] = (T0 + timedelta(seconds=16)).isoformat()
    client = client_mod.M1Client()
    client._get = lambda *args: body
    with pytest.raises(client_mod.M1Error) as caught:
        client.window("payment-service", T0, T0 + timedelta(seconds=30), STEP)
    assert caught.value.category == "bad_response"


def test_naive_timestamps_are_refused():
    with pytest.raises(ValueError):
        client_mod.M1Client().window("payment-service", datetime(2026, 1, 1), datetime(2026, 1, 2))


# ---------------------------------------------------------------------------
# Capture files
# ---------------------------------------------------------------------------

def test_a_capture_survives_save_and_load(timeline, tmp_path):
    capture, _ = timeline
    path = tmp_path / "capture.json"
    capture_mod.save_capture(path, capture)
    assert capture_mod.load_capture(path) == capture


def test_a_capture_without_history_can_be_saved_and_loaded(timeline, tmp_path):
    capture = dataclasses.replace(timeline[0], points=None)
    path = tmp_path / "answer-key-only.json"
    capture_mod.save_capture(path, capture)
    assert capture_mod.load_capture(path).points is None


def test_an_unknown_capture_format_is_refused(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"format": 99}))
    with pytest.raises(ValueError, match="unsupported"):
        capture_mod.load_capture(path)


def test_runs_are_cut_from_the_history_using_the_answer_key(timeline):
    capture, originals = timeline
    runs, notes = capture_mod.capture_to_runs(capture)
    assert notes == []
    assert [r.scenario for r in runs] == ["healthy", "bad_deployment", "traffic_spike"]
    for made, original in zip(runs, originals):
        assert made.fault_start == original.fault_start
        assert len(made.snapshots) == len(original.snapshots)
        assert [s.metrics for s in made.snapshots] == [s.metrics for s in original.snapshots]


def test_the_fault_index_is_the_first_reading_at_or_after_the_fault_time(timeline):
    capture, _ = timeline
    bad = capture.runs[1]
    late = dataclasses.replace(bad, fault_start=bad.fault_start + timedelta(seconds=5))
    capture = dataclasses.replace(capture, runs=[capture.runs[0], late, capture.runs[2]])
    runs, _ = capture_mod.capture_to_runs(capture)
    original_index = runs[1].snapshots.index(
        next(s for s in runs[1].snapshots if s.timestamp >= bad.fault_start))
    assert runs[1].fault_start == original_index + 1        # the next reading, not the earlier one


def test_a_run_with_too_little_normal_lead_in_is_left_out_and_explained(timeline):
    capture, _ = timeline
    bad = capture.runs[1]
    squeezed = dataclasses.replace(bad, start=bad.fault_start - timedelta(seconds=30))   # 2 readings
    capture = dataclasses.replace(capture, runs=[squeezed])
    runs, notes = capture_mod.capture_to_runs(capture)
    assert runs == [] and "only 2 readings before the fault" in notes[0]


def test_a_run_with_no_readings_after_the_fault_is_left_out_and_explained(timeline):
    capture, _ = timeline
    bad = capture.runs[1]
    cut = dataclasses.replace(bad, fault_start=bad.end + timedelta(seconds=60))
    runs, notes = capture_mod.capture_to_runs(dataclasses.replace(capture, runs=[cut]))
    assert runs == [] and "no readings after the fault started" in notes[0]


def test_converting_a_capture_without_history_is_refused(timeline):
    with pytest.raises(ValueError, match="no readings yet"):
        capture_mod.capture_to_runs(dataclasses.replace(timeline[0], points=None))


def test_real_runs_work_in_the_existing_evaluation_pipeline_unchanged(timeline):
    runs, _ = capture_mod.capture_to_runs(timeline[0])
    results = metrics.evaluate(metrics.prepare_all(runs), baseline_mod.DEFAULT_CONFIG)
    assert len(results) == 3 and metrics.summarize(results).runs == 3


# ---------------------------------------------------------------------------
# The drill, on a fake clock and a fake demo stack
# ---------------------------------------------------------------------------

class FakeWorld:
    """A clock, a demo service that changes version some seconds after a swap
    command, and a traffic generator that just writes down what it was told."""

    def __init__(self, swap_delay_s=7, commands_work=True, fault_works=True, build_output=""):
        self.elapsed = 0.0
        self.swap_delay_s = swap_delay_s
        self.commands_work = commands_work
        self.fault_works = fault_works
        self.build_output = build_output
        self.flips = []                        # (elapsed, version)
        self.commands = []
        self.traffic_log = []                  # (elapsed, workers)
        self.said = []
        self.sleeps = []                       # every wait the drill asked for
        self.interrupt_after_elapsed = None    # raise KeyboardInterrupt once this much time has passed
        self.interrupt_when_version = None     # ... or while the demo is on this version
        self.jump_at = None                    # simulate the laptop sleeping once this much time has passed
        self.jump_by = 0

    def now(self):
        return T0 + timedelta(seconds=self.elapsed)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if self.interrupt_after_elapsed is not None and self.elapsed >= self.interrupt_after_elapsed:
            raise KeyboardInterrupt
        if self.interrupt_when_version is not None and self.version() == self.interrupt_when_version:
            raise KeyboardInterrupt
        self.elapsed += seconds
        if self.jump_at is not None and self.elapsed >= self.jump_at:
            self.elapsed += self.jump_by           # the wall clock leaps forward; nothing was measured
            self.jump_at = None

    def version(self):
        current = "v1"
        for when, version in self.flips:
            if when <= self.elapsed:
                current = version
        return current

    def run_command(self, command):
        self.commands.append((self.elapsed, command))
        if "build" in command:
            return self.commands_work, self.build_output
        wanted = "v2" if any("faulty" in part for part in command) else "v1"
        if wanted == "v2" and not self.fault_works:
            return self.commands_work, "ok"
        self.flips.append((self.elapsed + self.swap_delay_s, wanted))
        return self.commands_work, "" if self.commands_work else "docker was not found"

    def traffic(self):
        world = self

        class Traffic:
            def set_concurrency(self, workers):
                world.traffic_log.append((world.elapsed, workers))

            def stop(self):
                world.traffic_log.append((world.elapsed, "stopped"))

        return Traffic()

    def drill(self, plan=None):
        return drill_mod.Drill(
            plan=plan or drill_mod.DrillPlan.quick(),
            traffic=self.traffic(), run_command=self.run_command, get_version=self.version,
            now=self.now, sleep=self.sleep, say=self.said.append,
        )


def test_the_drill_records_each_fault_at_the_moment_it_goes_live():
    world = FakeWorld(swap_delay_s=7)
    capture = world.drill().run()
    bad = next(r for r in capture.runs if r.scenario == "bad_deployment")
    command_time, _ = world.commands[0]
    # the fault starts when /version first says v2: 7 s after the command,
    # found by polling once a second, so never more than one poll late
    assert 7 <= (bad.fault_start - (T0 + timedelta(seconds=command_time))).total_seconds() <= 8


def test_the_drill_produces_the_three_runs_in_order():
    capture = FakeWorld().drill().run()
    assert [r.scenario for r in capture.runs] == ["healthy", "bad_deployment", "traffic_spike"]
    assert [r.run_id for r in capture.runs] == ["healthy-1", "bad_deployment-1", "traffic_spike-1"]
    assert capture.runs[0].fault_start is None
    assert capture.points is None            # history is fetched from M1 afterwards


def test_each_faulty_run_starts_with_healthy_lead_in_and_ends_when_the_fault_does():
    plan = drill_mod.DrillPlan.quick()
    capture = FakeWorld().drill(plan).run()
    _, bad, spike = capture.runs
    lead = timedelta(seconds=plan.context_s)
    assert bad.fault_start - bad.start == lead
    assert spike.fault_start - spike.start == lead
    events = {e["name"]: e["at"] for e in capture.events}
    assert events["bad_deployment_end"].startswith(bad.end.isoformat().replace("+00:00", "Z")[:19])


def test_runs_never_overlap_the_settling_period_after_a_fault_ends():
    plan = drill_mod.DrillPlan.quick()
    healthy, bad, spike = FakeWorld().drill(plan).run().runs
    assert bad.start >= healthy.end - timedelta(seconds=plan.context_s) - timedelta(seconds=1)
    assert spike.start - bad.end >= timedelta(seconds=plan.settle_s)


def test_healthy_run_lasts_exactly_the_healthy_phase():
    plan = drill_mod.DrillPlan.quick()
    healthy = FakeWorld().drill(plan).run().runs[0]
    assert (healthy.end - healthy.start).total_seconds() == plan.healthy_s


def test_traffic_goes_base_then_spike_then_base_then_stops():
    plan = drill_mod.DrillPlan.quick()
    world = FakeWorld()
    world.drill(plan).run()
    assert [w for _, w in world.traffic_log] == [plan.base_workers, plan.spike_workers, plan.base_workers, "stopped"]


def test_the_spike_is_recorded_when_the_load_is_raised():
    world = FakeWorld()
    capture = world.drill().run()
    raised_at = next(t for t, w in world.traffic_log if w == world.drill().plan.spike_workers)
    spike = capture.runs[2]
    assert spike.fault_start == T0 + timedelta(seconds=raised_at)


def test_the_drill_logs_what_it_did():
    capture = FakeWorld().drill().run()
    names = [e["name"] for e in capture.events]
    assert names == ["drill_start", "healthy_start", "healthy_end", "bad_deployment_start",
                     "bad_deployment_end", "traffic_spike_start", "traffic_spike_end", "drill_end"]


def test_it_swaps_with_the_commands_from_m1s_readme_without_building_during_the_drill():
    world = FakeWorld()
    world.drill().run()
    fault, restore = (c for _, c in world.commands)
    assert fault[:2] == ["docker", "compose"] and "observability/docker-compose.faulty.yml" in fault
    assert "build" not in fault and "--build" not in fault and fault[-1] == "payment-service"
    assert "--force-recreate" in restore and "observability/docker-compose.faulty.yml" not in restore


def test_the_faulty_image_is_built_before_the_timeline_not_during_it():
    world = FakeWorld()
    drill = world.drill()
    drill.build_faulty_image()
    assert len(world.commands) == 1 and "build" in world.commands[0][1]
    assert "observability/docker-compose.faulty.yml" in world.commands[0][1]
    world.commands.clear()
    drill.run()
    assert not any("build" in part for _, command in world.commands for part in command)


def test_a_failed_build_stops_everything_early_and_points_at_the_network():
    world = FakeWorld(commands_work=False,
                      build_output='failed to fetch oauth token: lookup auth.docker.io: i/o timeout')
    with pytest.raises(drill_mod.DrillError) as caught:
        world.drill().build_faulty_image()
    message = str(caught.value)
    assert "could not build the faulty version" in message and "auth.docker.io" in message
    assert "wsl --shutdown" in message and "getent hosts auth.docker.io" in message


def test_a_build_failure_that_is_not_the_network_gets_no_network_advice():
    world = FakeWorld(commands_work=False, build_output="no space left on device")
    with pytest.raises(drill_mod.DrillError) as caught:
        world.drill().build_faulty_image()
    assert "no space left" in str(caught.value) and "wsl --shutdown" not in str(caught.value)


def test_if_docker_cannot_be_run_it_tells_you_what_to_run_and_waits_for_the_swap():
    world = FakeWorld(commands_work=False)        # the swap still happens, as if you ran it by hand
    capture = world.drill().run()
    text = "\n".join(world.said)
    assert "could not run it automatically" in text
    assert "docker compose -f observability/docker-compose.yml -f observability/docker-compose.faulty.yml" in text
    assert [r.scenario for r in capture.runs] == ["healthy", "bad_deployment", "traffic_spike"]


def test_if_the_fault_never_goes_live_the_drill_stops_and_cleans_up():
    world = FakeWorld(fault_works=False)
    with pytest.raises(drill_mod.DrillError, match="never reported version v2"):
        world.drill().run()
    assert world.traffic_log[-1][1] == "stopped"
    assert any("--force-recreate" in c for _, c in world.commands)      # it asked for v1 back


def test_stopping_during_the_bad_deployment_puts_the_service_back_to_healthy():
    world = FakeWorld()
    world.interrupt_when_version = "v2"            # Ctrl+C while the faulty version is live
    with pytest.raises(KeyboardInterrupt):
        world.drill().run()
    assert world.traffic_log[-1][1] == "stopped"
    last_command = world.commands[-1][1]
    assert "--force-recreate" in last_command and "faulty" not in " ".join(last_command)
    assert "putting payment-service back" in "\n".join(world.said)


def test_stopping_before_any_fault_does_not_run_a_restore():
    world = FakeWorld()
    world.interrupt_after_elapsed = 100            # during warm-up / healthy
    with pytest.raises(KeyboardInterrupt):
        world.drill().run()
    assert world.commands == []
    assert world.traffic_log[-1][1] == "stopped"


def test_long_waits_are_taken_in_short_chunks_so_a_sleep_is_noticed_quickly():
    world = FakeWorld()
    drill = world.drill()
    drill.run()
    assert max(world.sleeps) <= 30        # a sleep must be noticed within half a minute
    plan = drill.plan
    phases = plan.warmup_s + plan.healthy_s + plan.fault_s + plan.recovery_s + plan.spike_s + plan.tail_s
    assert sum(world.sleeps) >= phases


def test_a_laptop_sleep_during_the_healthy_stretch_stops_the_drill_cleanly():
    world = FakeWorld()
    world.jump_at, world.jump_by = 100, 600          # inside the healthy stretch (quick plan: 45s warm-up, then 120s healthy)
    with pytest.raises(drill_mod.DrillError) as caught:
        world.drill().run()
    message = str(caught.value)
    assert "went to sleep" in message and "plugged in" in message and "600s" in message
    assert world.traffic_log[-1][1] == "stopped"
    assert world.commands == []                      # no fault was live, so nothing to restore


def test_a_laptop_sleep_during_the_bad_deployment_also_restores_healthy_v1():
    plan = drill_mod.DrillPlan.quick()
    world = FakeWorld()
    world.jump_at = plan.warmup_s + plan.healthy_s + 40         # well inside the fault stretch
    world.jump_by = 600
    with pytest.raises(drill_mod.DrillError, match="went to sleep"):
        world.drill(plan).run()
    assert world.traffic_log[-1][1] == "stopped"
    last_command = world.commands[-1][1]
    assert "--force-recreate" in last_command and "faulty" not in " ".join(last_command)


def test_ordinary_timing_noise_is_not_mistaken_for_a_sleep():
    world = FakeWorld()
    original = world.sleep

    def slightly_slow(seconds):
        original(seconds)
        world.elapsed += 0.4                          # every wait overruns a little

    world.sleep = slightly_slow
    FakeWorld.drill(world).run()                      # must finish without complaint


def test_preflight_wants_the_stack_up_and_on_healthy_v1():
    world = FakeWorld()
    drill = world.drill()
    drill.preflight(lambda: {"status": "degraded"})          # degraded is fine: no Kubernetes attached
    assert "payment-service: v1" in "\n".join(world.said)

    drill.get_version = lambda: None
    with pytest.raises(drill_mod.DrillError, match="cannot reach payment-service"):
        drill.preflight()
    drill.get_version = lambda: "v2"
    with pytest.raises(drill_mod.DrillError, match="running v2"):
        drill.preflight()
    drill.get_version = lambda: "v1"
    with pytest.raises(drill_mod.DrillError, match="unavailable"):
        drill.preflight(lambda: {"status": "unavailable"})


@pytest.mark.parametrize("bad", [
    dict(healthy_s=100, context_s=180),
    dict(recovery_s=200, settle_s=90, context_s=180),
    dict(base_workers=10, spike_workers=10),
])
def test_a_plan_that_cannot_produce_usable_runs_is_refused(bad):
    with pytest.raises(ValueError):
        dataclasses.replace(drill_mod.DrillPlan(), **bad)


def test_threaded_traffic_starts_scales_and_stops_workers():
    sent = []
    traffic = drill_mod.ThreadedTraffic(lambda: sent.append(1), delay_s=0.001)
    traffic.set_concurrency(3)
    time.sleep(0.1)
    assert len(traffic._workers) == 3 and len(sent) > 0
    traffic.set_concurrency(1)
    assert len(traffic._workers) == 1
    traffic.stop()
    assert traffic._workers == []
    time.sleep(0.05)
    settled = len(sent)
    time.sleep(0.1)
    assert len(sent) == settled                    # nothing is still sending


def test_fetching_history_covers_every_run(timeline, fake_m1):
    capture = dataclasses.replace(timeline[0], points=None)
    drill_mod.fetch_points(capture, client_mod.M1Client(fake_m1.url))
    assert len(capture.points) == len(timeline[0].points)


def test_rollout_history_is_split_and_ambiguous_reading_is_recorded(timeline):
    template = timeline[0].points[0]
    points = [template.model_copy(update={"timestamp": T0 + timedelta(seconds=i * STEP),
                                         "version": "v1" if i < 3 else "v2"}) for i in range(7)]
    capture = capture_mod.Capture("payment-service", STEP, "http://m1", T0 + timedelta(seconds=120),
        runs=[capture_mod.RunSpec("rollout", "bad_deployment", T0, points[-1].timestamp, points[3].timestamp)])
    calls = []

    class StrictM1:
        def window(self, service, start, end, step):
            calls.append((start, end))
            inside = [point for point in points if start <= point.timestamp <= end]
            if len({point.version for point in inside}) > 1 or points[3] in inside:
                raise client_mod.M1Error("Historical window contains mixed versions for payment-service",
                                         category="telemetry_missing", status=503)
            return inside

    drill_mod.fetch_points(capture, StrictM1())
    assert capture.points == points[:3] + points[4:]
    assert all(start < end <= capture.recorded_at for start, end in calls)
    assert len(capture.events) == 1
    assert capture.events[0]["at"] == points[3].timestamp.isoformat().replace("+00:00", "Z")
    assert capture.events[0]["name"] == "history_point_unavailable"


@pytest.mark.parametrize("category,message", [
    ("provider_unavailable", "Prometheus unavailable"),
    ("telemetry_missing", "Prometheus returned incomplete historical samples"),
])
def test_history_provider_failure_is_not_hidden_by_splitting(timeline, category, message):
    capture = dataclasses.replace(timeline[0], points=None, events=list(timeline[0].events))

    class FailedM1:
        def window(self, *args):
            raise client_mod.M1Error(message, category=category, status=503)

    with pytest.raises(client_mod.M1Error, match=message):
        drill_mod.fetch_points(capture, FailedM1())
    assert capture.points is None
    assert capture.events == timeline[0].events


def test_history_fetch_never_requests_a_future_drill_window(timeline):
    capture = dataclasses.replace(timeline[0], recorded_at=timeline[0].runs[0].start, points=None)
    with pytest.raises(ValueError, match="completed"):
        drill_mod.fetch_points(capture, None)


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def test_auc_is_the_chance_a_fault_reading_outscores_a_normal_one():
    assert report_mod.auc([1.0, 2.0], [0.0, 0.5]) == 1.0
    assert report_mod.auc([0.0], [1.0]) == 0.0
    assert report_mod.auc([1.0], [1.0]) == 0.5                      # ties count half
    assert report_mod.auc([0.15, 0.5, 0.6], [0.1, 0.2, 0.1]) == pytest.approx(8 / 9)
    assert report_mod.auc([], [1.0]) != report_mod.auc([], [1.0])    # nan


def test_separation_counts_fault_readings_above_the_highest_normal_one():
    run = scenarios.generate_run(scenarios.PROFILES[0], "bad_deployment", seed=1)
    f = run.fault_start
    scores = [0.1, 0.2, 0.1] + [0.0] * (f - 3) + [0.15, 0.5, 0.6] + [0.0] * (len(run.snapshots) - f - 3)
    scores[:f] = [0.2 if i == 1 else 0.1 for i in range(f)]                 # normal ceiling 0.2
    got = report_mod.separation(run, scores, "x")
    assert got.normal_max == 0.2 and got.fault_max == 0.6
    assert got.caught_share == pytest.approx(2 / (len(run.snapshots) - f))
    assert got.first_alert_s == 15.0                                          # second fault reading


def test_a_detector_that_never_clears_the_normal_ceiling_never_alerts():
    run = scenarios.generate_run(scenarios.PROFILES[0], "bad_deployment", seed=1)
    got = report_mod.separation(run, [0.5] * len(run.snapshots), "flat")
    assert got.caught_share == 0.0 and got.first_alert_s is None and got.auc == 0.5


def test_the_report_has_all_three_sections_and_names_the_detectors(timeline):
    pytest.importorskip("sklearn")
    runs, _ = capture_mod.capture_to_runs(timeline[0])
    text = report_mod.build_report(runs)
    assert "WHAT THE REAL READINGS LOOK LIKE" in text
    assert "FROZEN BASELINE ON REAL READINGS" in text
    assert "CAN EACH DETECTOR SEPARATE" in text
    for name in ("baseline (frozen)", "ruler", "forest", "latency_p95_ms", "bad_deployment-1", "traffic_spike-1"):
        assert name in text


def test_on_obvious_faults_the_ruler_and_forest_separate_them_cleanly(timeline):
    pytest.importorskip("sklearn")
    runs, _ = capture_mod.capture_to_runs(timeline[0])
    prepared = metrics.prepare_all(runs)
    text = "\n".join(report_mod.detector_separation(prepared, baseline_mod.DEFAULT_CONFIG))
    ruler_rows = [line for line in text.splitlines() if line.strip().startswith("ruler")]
    assert len(ruler_rows) == 2 and all(" 1.000" in row for row in ruler_rows)


def test_not_enough_normal_history_is_reported_not_hidden(timeline):
    pytest.importorskip("sklearn")
    runs, _ = capture_mod.capture_to_runs(timeline[0])
    small = [r for r in runs if r.scenario == "bad_deployment"]       # no other runs to learn from
    text = "\n".join(report_mod.detector_separation(metrics.prepare_all(small), baseline_mod.DEFAULT_CONFIG))
    assert "ruler and forest skipped" in text and "only 0 normal readings" in text


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def test_fetch_attaches_history_to_a_saved_capture(timeline, fake_m1, tmp_path, capsys):
    path = tmp_path / "c.json"
    capture_mod.save_capture(path, dataclasses.replace(timeline[0], points=None))
    assert cli.main(["fetch", str(path), "--m1-url", fake_m1.url]) == 0
    assert len(capture_mod.load_capture(path).points) == len(timeline[0].points)
    assert "Saved" in capsys.readouterr().out


def test_fetch_failure_keeps_the_answer_key_and_says_how_to_retry(timeline, tmp_path, capsys):
    path = tmp_path / "c.json"
    capture_mod.save_capture(path, dataclasses.replace(timeline[0], points=None))
    assert cli.main(["fetch", str(path), "--m1-url", "http://127.0.0.1:1"]) == 1
    out = capsys.readouterr().out
    assert "Could not get the history from M1" in out and f"fetch {path}" in out
    assert len(capture_mod.load_capture(path).runs) == 3


def test_report_command_prints_a_report(timeline, tmp_path, capsys):
    pytest.importorskip("sklearn")
    path = tmp_path / "c.json"
    capture_mod.save_capture(path, timeline[0])
    assert cli.main(["report", str(path)]) == 0
    assert "FROZEN BASELINE ON REAL READINGS" in capsys.readouterr().out


def test_report_command_refuses_a_capture_with_no_history(timeline, tmp_path, capsys):
    path = tmp_path / "c.json"
    capture_mod.save_capture(path, dataclasses.replace(timeline[0], points=None))
    assert cli.main(["report", str(path)]) == 2
    assert "has no readings yet" in capsys.readouterr().out


def test_report_over_several_captures_keeps_their_runs_apart(timeline, tmp_path, capsys):
    pytest.importorskip("sklearn")
    for name in ("monday", "tuesday"):
        capture_mod.save_capture(tmp_path / f"{name}.json", timeline[0])
    assert cli.main(["report", str(tmp_path / "monday.json"), str(tmp_path / "tuesday.json")]) == 0
    out = capsys.readouterr().out
    assert "monday:bad_deployment-1" in out and "tuesday:bad_deployment-1" in out


def test_drill_command_explains_how_to_get_m1s_stack_when_it_is_missing(tmp_path, capsys):
    assert cli.main(["drill", "--stack-dir", str(tmp_path)]) == 2
    out = capsys.readouterr().out
    assert "observability/docker-compose.yml" in out and "git worktree add" in out


def test_detectors_are_trained_on_other_runs_only_never_on_the_run_being_judged(timeline):
    """The honesty rule of the report: the number of normal readings a
    detector trained on must equal those of the OTHER runs, excluding the
    run's own."""
    pytest.importorskip("sklearn")
    runs, _ = capture_mod.capture_to_runs(timeline[0])

    def normal_count(run):
        return len(run.snapshots) if run.fault_start is None else run.fault_start

    healthy, bad, spike = runs
    text = "\n".join(report_mod.detector_separation(metrics.prepare_all(runs), baseline_mod.DEFAULT_CONFIG))
    assert f"bad_deployment-1  (trained on {normal_count(healthy) + normal_count(spike)} normal readings" in text
    assert f"traffic_spike-1  (trained on {normal_count(healthy) + normal_count(bad)} normal readings" in text


# ---------------------------------------------------------------------------
# The real HTTP and shell helpers, against a tiny fake demo service
# ---------------------------------------------------------------------------

class FakePayment:
    """Answers POST /pay and GET /version the way the demo payment-service does."""

    def __init__(self):
        self.version = "v1"
        self.pays = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, body, status=200):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.pays += 1
                if outer.version == "v2":
                    self._json({"ok": False}, status=500)      # faulty v2 fails (the helper must shrug)
                else:
                    self._json({"ok": True})

            def do_GET(self):
                self._json({"service": "payment-service", "version": outer.version})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def payment():
    server = FakePayment()
    yield server
    server.close()


def test_http_version_reports_what_the_demo_service_says(payment):
    version = drill_mod.http_version(payment.url)
    assert version() == "v1"
    payment.version = "v2"
    assert version() == "v2"


def test_http_version_is_none_when_nothing_answers():
    assert drill_mod.http_version("http://127.0.0.1:1", timeout=1)() is None


def test_http_pay_sends_a_request_and_shrugs_off_errors(payment):
    pay = drill_mod.http_pay(payment.url)
    pay()
    payment.version = "v2"                           # now every reply is a 500
    pay()
    assert payment.pays == 2


def test_http_pay_does_not_raise_when_the_service_is_down():
    drill_mod.http_pay("http://127.0.0.1:1", timeout=1)()


def test_shell_command_reports_success_failure_and_a_missing_program(tmp_path):
    run = drill_mod.shell_command(str(tmp_path))
    ok, output = run([sys.executable, "-c", "print('hello')"])
    assert ok and output == "hello"
    ok, output = run([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])
    assert not ok and "boom" in output
    ok, output = run(["definitely-not-a-real-program"])
    assert not ok and "not found" in output


def test_shell_command_runs_in_the_folder_it_was_given(tmp_path):
    (tmp_path / "marker.txt").write_text("x")
    ok, output = drill_mod.shell_command(str(tmp_path))([sys.executable, "-c", "import os; print(os.listdir('.'))"])
    assert ok and "marker.txt" in output


def test_a_short_drill_with_real_http_and_the_real_clock(payment):
    """Real threads sending real requests to a real (fake) service, and the
    fault start found by really polling /version."""
    plan = drill_mod.DrillPlan(warmup_s=0, healthy_s=2, fault_s=1, recovery_s=2, spike_s=1, tail_s=0,
                               context_s=1, settle_s=1, base_workers=2, spike_workers=4, poll_s=0.05,
                               version_timeout_s=10)

    def swap(command):
        wanted = "v2" if any("faulty" in part for part in command) else "v1"
        threading.Timer(0.3, lambda: setattr(payment, "version", wanted)).start()
        return True, ""

    drill = drill_mod.Drill(
        plan=plan,
        traffic=drill_mod.ThreadedTraffic(drill_mod.http_pay(payment.url), delay_s=0.01),
        run_command=swap,
        get_version=drill_mod.http_version(payment.url),
        say=lambda _: None,
    )
    started = datetime.now(timezone.utc)
    capture = drill.run()
    bad = capture.runs[1]

    assert payment.pays > 20                                     # the traffic really flowed
    assert payment.version == "v1"                               # and it ended healthy
    waited = (bad.fault_start - started).total_seconds()
    assert 2.2 < waited < 3.2                                    # healthy 2 s, then the swap's 0.3 s


# ---------------------------------------------------------------------------
# Command line: building first
# ---------------------------------------------------------------------------

def _stack_dir(tmp_path):
    (tmp_path / "observability").mkdir()
    (tmp_path / "observability" / "docker-compose.yml").write_text("services: {}\n")
    return str(tmp_path)


def test_drill_command_stops_before_starting_if_the_faulty_build_fails(tmp_path, monkeypatch, capsys):
    calls = []

    class StubDrill:
        def __init__(self, **kwargs):
            pass

        def preflight(self, health):
            pass

        def build_faulty_image(self):
            calls.append("build")
            raise drill_mod.DrillError("could not build the faulty version: no network")

        def run(self):
            calls.append("run")

    monkeypatch.setattr(cli, "Drill", StubDrill)
    assert cli.main(["drill", "--stack-dir", _stack_dir(tmp_path), "--yes"]) == 2
    assert calls == ["build"]                          # never got as far as the timeline
    assert "no network" in capsys.readouterr().out


def test_drill_command_skips_the_build_when_asked(tmp_path, monkeypatch, capsys):
    calls = []

    class StubDrill:
        def __init__(self, **kwargs):
            self.plan = kwargs["plan"]

        def preflight(self, health):
            pass

        def build_faulty_image(self):
            calls.append("build")

        def run(self):
            calls.append("run")
            raise drill_mod.DrillError("stop here")

    monkeypatch.setattr(cli, "Drill", StubDrill)
    cli.main(["drill", "--stack-dir", _stack_dir(tmp_path), "--yes", "--skip-build"])
    assert calls == ["run"]
    assert "PLUGGED IN AND AWAKE" in capsys.readouterr().out
