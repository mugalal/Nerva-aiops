"""
Tests for the final tools: `train` (frozen reference + alert line), `evaluate`
(the results table), `live-check` (running M2 against the drill's answer key),
and the command line's handling of mistakes.

Needs numpy and scikit-learn:   pip install scikit-learn numpy
Run from the repo root:         python -m pytest tests/m2 -v
"""

import dataclasses
import json
import math
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import m2_loader
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from real_like import SERVICE, make_capture  # noqa: E402

capture_mod = m2_loader.module("realdata.capture")
train_mod = m2_loader.module("realdata.train")
final_mod = m2_loader.module("realdata.final")
reference_mod = m2_loader.module("reference")
pipeline_mod = m2_loader.module("pipeline")
cli = m2_loader.module("realdata.__main__")


@pytest.fixture()
def captures(tmp_path):
    """Two captures to train on and a third the reference never sees."""
    paths = {}
    for name, seed, noise in (("capture1.json", 11, 1.0), ("capture2.json", 12, 5.0), ("capture3.json", 13, 1.0)):
        paths[name] = str(tmp_path / name)
        capture_mod.save_capture(paths[name], make_capture(seed, noise_bad=noise))
    return paths


@pytest.fixture()
def trained(captures, tmp_path):
    out = tmp_path / "reference" / "reference.json"
    reference, report = train_mod.train_reference([captures["capture1.json"], captures["capture2.json"]], out)
    return reference, report, out


# ---------------------------------------------------------------------------
# train
# ---------------------------------------------------------------------------

def test_training_finds_a_clean_gap_and_puts_the_line_in_the_middle_of_it(trained):
    reference, _, out = trained
    line = reference.alert
    assert line.fault_min_wobbles > line.normal_max_wobbles
    assert line.wobbles == pytest.approx(math.sqrt(max(line.normal_max_wobbles, 1.0) * line.fault_min_wobbles))
    assert line.normal_max_wobbles < line.wobbles < line.fault_min_wobbles
    assert line.margin > 10
    assert out.exists()


def test_the_saved_reference_loads_back_and_remembers_what_it_was_trained_on(trained):
    reference, _, out = trained
    loaded = reference_mod.load_reference(out)
    assert loaded.trained_on == ("capture1.json", "capture2.json")
    assert loaded.alert == reference.alert and loaded.services == [SERVICE]
    assert loaded.detector.to_dict()["services"][SERVICE]["n"] == 128         # 40 + 12 + 12, in both captures


def test_the_training_report_shows_the_margin_and_admits_its_own_limits(trained):
    _, report, out = trained
    assert "Alert line:" in report and "margin:" in report and "wobbles from normal" in report
    assert "clean by design" in report and "not an independent test" in report
    assert f"Saved the reference to {out}" in report


def test_training_needs_a_ruler_that_never_saw_the_run_it_judges(captures):
    prepared = train_mod.prepare_all(
        [train_mod.Run(r.run_id, r.service, r.scenario, r.fault_start, r.snapshots)
         for r in capture_mod.capture_to_runs(capture_mod.load_capture(captures["capture1.json"]))[0]])
    # Judging each run by a ruler trained on its siblings: with only 3 runs, each ruler has the other two.
    distances = train_mod.leave_one_out_distances(prepared)
    assert len(distances) == 3 and all(len(d) == len(p.run.snapshots) for d, p in zip(distances, prepared))


def test_when_faults_cannot_be_told_from_normal_no_line_is_chosen_and_nothing_is_written(tmp_path):
    path = tmp_path / "faint.json"
    capture_mod.save_capture(path, make_capture(5, fault_scale=0.0004))     # faults far too small to see
    out = tmp_path / "reference.json"
    reference, report = train_mod.train_reference([str(path)], out)
    assert reference is None and not out.exists()
    assert "NO ALERT LINE CHOSEN" in report and "no clean gap" in report


def test_training_refuses_when_there_is_too_little_normal_data_to_judge_a_run(tmp_path):
    capture = make_capture(5)
    only_bad = dataclasses.replace(capture, runs=[r for r in capture.runs if r.scenario == "bad_deployment"])
    path = tmp_path / "small.json"
    capture_mod.save_capture(path, only_bad)
    with pytest.raises(train_mod.TrainError, match="need 20"):
        train_mod.train_reference([str(path)], tmp_path / "ref.json")


def test_training_refuses_a_capture_with_no_readings_yet(tmp_path):
    path = tmp_path / "empty.json"
    capture_mod.save_capture(path, make_capture(5, with_points=False))
    with pytest.raises(train_mod.TrainError, match="no readings yet"):
        train_mod.train_reference([str(path)], tmp_path / "ref.json")


def test_a_capture_with_no_fault_cannot_place_a_line(tmp_path):
    capture = make_capture(5)
    healthy_only = dataclasses.replace(capture, runs=[r for r in capture.runs if r.scenario == "healthy"] * 1)
    path = tmp_path / "healthy.json"
    capture_mod.save_capture(path, healthy_only)
    # one healthy run has no sibling to learn from, so this stops earlier, for the same underlying reason
    with pytest.raises(train_mod.TrainError):
        train_mod.train_reference([str(path)], tmp_path / "ref.json")


# ---------------------------------------------------------------------------
# From training to the running pipeline: one incident per fault
# ---------------------------------------------------------------------------

def test_the_trained_reference_makes_the_pipeline_open_one_incident_per_fault_and_none_for_healthy(trained, captures):
    reference, _, _ = trained
    held_out = capture_mod.load_capture(captures["capture3.json"])
    pipe = pipeline_mod.Pipeline(reference)
    alerts_by_run = {}
    for spec in held_out.runs:
        alerts_by_run[spec.run_id] = sum(
            pipe.process(p).alert for p in held_out.points if spec.start <= p.timestamp <= spec.end)
    assert alerts_by_run["healthy-1"] == 0
    assert alerts_by_run["bad_deployment-1"] > 10 and alerts_by_run["traffic_spike-1"] > 10
    incidents = pipe.incidents()
    assert len(incidents) == 2                                          # one per fault, not one per alert
    assert all(i.status == "DETECTED" for i in incidents)


# ---------------------------------------------------------------------------
# evaluate
# ---------------------------------------------------------------------------

def test_the_frozen_reference_catches_both_faults_on_a_capture_it_has_never_seen(trained, captures):
    reference, _, _ = trained
    results, text = final_mod.run_evaluate([captures["capture3.json"]], reference)
    by_run = {r.run_id: r for r in results}
    assert not by_run["healthy-1"].prediction and by_run["healthy-1"].false_alert_snapshots == 0
    assert by_run["bad_deployment-1"].detected and by_run["traffic_spike-1"].detected
    assert "precision 1.000" in text and "IN-SAMPLE" not in text


def test_a_capture_the_reference_was_built_from_is_flagged_as_in_sample(trained, captures):
    reference, _, _ = trained
    _, text = final_mod.run_evaluate([captures["capture1.json"]], reference)
    assert "IN-SAMPLE" in text and "capture1.json" in text


def test_evaluating_a_service_the_reference_never_saw_is_an_error(trained, captures, tmp_path):
    reference, _, _ = trained
    foreign = dataclasses.replace(make_capture(7), service="checkout-service")
    path = tmp_path / "foreign.json"
    capture_mod.save_capture(path, foreign)
    with pytest.raises(ValueError, match="no entry for service 'checkout-service'"):
        final_mod.run_evaluate([str(path)], reference)


def test_the_evaluation_table_has_the_runbooks_columns(trained, captures, tmp_path, capsys):
    out = trained[2]
    csv_path = tmp_path / "results.csv"
    code = cli.main(["evaluate", captures["capture3.json"], "--reference", str(out), "--csv", str(csv_path)])
    assert code == 0 and "Wrote the results table" in capsys.readouterr().out
    header = csv_path.read_text().splitlines()[0]
    assert header == "run,scenario,ground_truth,detected,score,detection_delay,prediction"


# ---------------------------------------------------------------------------
# live-check, against a fake M2
# ---------------------------------------------------------------------------

class FakeM2:
    def __init__(self, incidents, details=None):
        self.incidents = incidents
        self.details = details or {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/internal/correlation/incidents":
                    payload, status = outer.incidents, 200
                elif path.startswith("/internal/correlation/incidents/"):
                    key = path.rsplit("/", 1)[1]
                    payload, status = outer.details.get(key, {"alert_count": 5, "signals": ["latency_p95_ms"], "peak_score": 0.99}), 200
                else:
                    payload, status = {"detail": "nope"}, 404
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


def incident(incident_id, when, service=SERVICE):
    return {"incident_id": incident_id, "started_at": when.isoformat().replace("+00:00", "Z"), "status": "DETECTED",
            "severity": "high", "affected_services": [service], "anomaly_ids": ["a"]}


@pytest.fixture()
def drill():
    return make_capture(21)


@pytest.fixture()
def fake_m2():
    created = []

    def make(incidents, details=None):
        server = FakeM2(incidents, details)
        created.append(server)
        return server

    yield make
    for server in created:
        server.close()


def faults(capture):
    return {r.scenario: r for r in capture.runs if r.fault_start is not None}


def test_each_fault_is_matched_with_the_incident_m2_opened_and_the_delay_measured(drill, fake_m2):
    f = faults(drill)
    server = fake_m2([
        incident("INC-bad", f["bad_deployment"].fault_start + timedelta(seconds=15)),
        incident("INC-spike", f["traffic_spike"].fault_start + timedelta(seconds=30)),
    ])
    result = final_mod.live_check(drill, server.url)
    outcomes = {o.scenario: o for o in result.faults}
    assert outcomes["bad_deployment"].incident_id == "INC-bad" and outcomes["bad_deployment"].delay_s == 15
    assert outcomes["traffic_spike"].incident_id == "INC-spike" and outcomes["traffic_spike"].delay_s == 30
    assert outcomes["bad_deployment"].signals == ["latency_p95_ms"] and result.false_alarms == []


def test_a_fault_with_no_incident_is_reported_as_missed(drill, fake_m2):
    f = faults(drill)
    server = fake_m2([incident("INC-bad", f["bad_deployment"].fault_start + timedelta(seconds=15))])
    result = final_mod.live_check(drill, server.url)
    outcomes = {o.scenario: o for o in result.faults}
    assert outcomes["traffic_spike"].incident_id is None and outcomes["traffic_spike"].delay_s is None
    assert "MISSED" in final_mod.format_live_check(result)


def test_an_incident_that_matches_no_fault_is_counted_as_a_false_alarm(drill, fake_m2):
    f = faults(drill)
    healthy_run = next(r for r in drill.runs if r.fault_start is None)
    server = fake_m2([
        incident("INC-bad", f["bad_deployment"].fault_start + timedelta(seconds=15)),
        incident("INC-spike", f["traffic_spike"].fault_start + timedelta(seconds=15)),
        incident("INC-stray", healthy_run.start + timedelta(seconds=200)),         # during the healthy stretch
    ])
    result = final_mod.live_check(drill, server.url)
    assert [i for i, _ in result.false_alarms] == ["INC-stray"]
    assert "FALSE ALARMS: 1" in final_mod.format_live_check(result)


def test_two_incidents_for_one_fault_is_flagged_because_one_fault_should_be_one_incident(drill, fake_m2):
    start = faults(drill)["bad_deployment"].fault_start
    server = fake_m2([incident("INC-1", start + timedelta(seconds=15)), incident("INC-2", start + timedelta(seconds=200))])
    outcome = next(o for o in final_mod.live_check(drill, server.url).faults if o.scenario == "bad_deployment")
    assert outcome.incident_id == "INC-1" and outcome.extra_candidates == 1
    assert "extra candidate" in final_mod.format_live_check(final_mod.live_check(drill, server.url))


def test_incidents_outside_the_drill_or_for_other_services_are_ignored(drill, fake_m2):
    start = faults(drill)["bad_deployment"].fault_start
    server = fake_m2([
        incident("INC-bad", start + timedelta(seconds=15)),
        incident("INC-other-service", start + timedelta(seconds=15), service="checkout-service"),
        incident("INC-yesterday", start - timedelta(days=1)),
    ])
    result = final_mod.live_check(drill, server.url)
    assert result.false_alarms == []


def test_live_check_says_when_m2_cannot_be_reached(drill):
    with pytest.raises(final_mod.LiveCheckError, match="could not reach M2"):
        final_mod.live_check(drill, "http://127.0.0.1:1")


def test_the_command_exits_zero_only_when_every_fault_is_caught_and_nothing_false_alarms(drill, fake_m2, tmp_path, capsys):
    path = tmp_path / "drill.json"
    capture_mod.save_capture(path, drill)
    f = faults(drill)
    good = fake_m2([incident("A", f["bad_deployment"].fault_start + timedelta(seconds=15)),
                    incident("B", f["traffic_spike"].fault_start + timedelta(seconds=15))])
    assert cli.main(["live-check", str(path), "--m2-url", good.url]) == 0
    missed = fake_m2([incident("A", f["bad_deployment"].fault_start + timedelta(seconds=15))])
    assert cli.main(["live-check", str(path), "--m2-url", missed.url]) == 1
    assert cli.main(["live-check", str(path), "--m2-url", "http://127.0.0.1:1"]) == 2
    assert "Could not check" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The command line: mistakes get a plain explanation, never a traceback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["train", "nope.json"],
    ["evaluate", "nope.json", "--reference", "nope-ref.json"],
    ["report", "nope.json"],
    ["fetch", "nope.json"],
    ["live-check", "nope.json"],
])
def test_a_missing_file_is_explained_not_crashed_on(argv, capsys):
    assert cli.main(argv) == 2
    out = capsys.readouterr().out
    assert "nope" in out and "Traceback" not in out


def test_a_file_that_is_not_a_capture_is_explained(tmp_path, capsys):
    junk = tmp_path / "junk.json"
    junk.write_text("{ not json")
    assert cli.main(["report", str(junk)]) == 2
    assert "not a usable capture file" in capsys.readouterr().out


def test_train_from_the_command_line_writes_the_reference(captures, tmp_path, capsys):
    out = tmp_path / "out" / "reference.json"
    code = cli.main(["train", captures["capture1.json"], captures["capture2.json"], "--out", str(out)])
    assert code == 0 and out.exists() and "Alert line:" in capsys.readouterr().out


def test_train_from_the_command_line_exits_with_an_error_when_no_line_is_honest(tmp_path, capsys):
    path = tmp_path / "faint.json"
    capture_mod.save_capture(path, make_capture(5, fault_scale=0.0004))
    out = tmp_path / "reference.json"
    assert cli.main(["train", str(path), "--out", str(out)]) == 2
    assert not out.exists() and "NO ALERT LINE CHOSEN" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The alert-line rule itself, with distances written by hand
# ---------------------------------------------------------------------------

@pytest.fixture()
def one_faulty_run():
    run = next(r for r in capture_mod.capture_to_runs(make_capture(5))[0] if r.scenario == "bad_deployment")
    return train_mod.prepare_all([run]), run


def with_distances(monkeypatch, prepared, normal, fault):
    monkeypatch.setattr(train_mod, "leave_one_out_distances", lambda p, min_samples=20: [normal + fault])


def test_the_first_readings_of_a_fault_are_not_held_against_the_line(monkeypatch, one_faulty_run):
    prepared, run = one_faulty_run
    normal = [1.0, 2.0, 3.0] * (run.fault_start // 3) + [1.0] * (run.fault_start % 3)
    fault = [0.5, 2.0, 5.0, 20.0] + [100.0] * (len(run.snapshots) - run.fault_start - 4)      # a slow ramp, then settled
    with_distances(monkeypatch, prepared, normal, fault)
    choice = train_mod.choose_alert_line(prepared)
    assert choice.alert is not None                               # the 0.5 and 2.0 ramp readings did not veto the line
    assert choice.alert.fault_min_wobbles == 100.0 and choice.alert.normal_max_wobbles == 3.0
    assert choice.alert.wobbles == pytest.approx(math.sqrt(3.0 * 100.0))


def test_a_settled_fault_that_is_no_louder_than_normal_means_no_line(monkeypatch, one_faulty_run):
    prepared, run = one_faulty_run
    normal = [4.0] * run.fault_start
    fault = [50.0] * 4 + [3.0] * (len(run.snapshots) - run.fault_start - 4)                    # settled readings under the normal max
    with_distances(monkeypatch, prepared, normal, fault)
    choice = train_mod.choose_alert_line(prepared)
    assert choice.alert is None and "no clean gap" in choice.reason


def test_a_perfectly_flat_normal_still_gets_a_sensible_line(monkeypatch, one_faulty_run):
    prepared, run = one_faulty_run
    with_distances(monkeypatch, prepared, [0.2] * run.fault_start, [0.0] * 4 + [400.0] * (len(run.snapshots) - run.fault_start - 4))
    choice = train_mod.choose_alert_line(prepared)
    assert choice.alert.wobbles == pytest.approx(math.sqrt(1.0 * 400.0))        # the 1-wobble floor, not 0.2


def test_the_per_run_table_counts_misses_and_the_first_alert_at_the_chosen_line(monkeypatch, one_faulty_run):
    prepared, run = one_faulty_run
    normal = [1.0] * run.fault_start
    fault = [0.0, 0.0, 50.0, 60.0] + [100.0] * (len(run.snapshots) - run.fault_start - 4)
    with_distances(monkeypatch, prepared, normal, fault)
    (row,) = train_mod.choose_alert_line(prepared).runs
    assert row.false_alarms == 0 and row.caught == row.settled_readings
    assert row.first_alert_s == 2 * 15 and row.normal_readings == run.fault_start
