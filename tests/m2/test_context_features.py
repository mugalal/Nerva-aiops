"""
The replica count is context, not a symptom.

M4's own remediation changes it (a traffic spike is cured by scaling 1 to 10
copies). A ruler that learned "normal = 1 replica" would call the cure a fault
and keep calling it one while the extra copies run. So the replica count is
recorded with every alert but never scored.

These tests check that, that old reference files get the same treatment without
retraining, and that nothing validated on the real drills changes (the replica
count never moved in them, so it never contributed to a score).

Needs numpy and scikit-learn:   pip install scikit-learn numpy
Run from the repo root:         python -m pytest tests/m2 -v
"""

import dataclasses
import json
import random
from datetime import datetime, timedelta, timezone

import m2_loader
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from real_like import SERVICE, make_capture, normal_rows, snapshot  # noqa: E402

capture_mod = m2_loader.module("realdata.capture")
final_mod = m2_loader.module("realdata.final")
train_mod = m2_loader.module("realdata.train")
features_mod = m2_loader.module("features")
model_mod = m2_loader.module("model")
pipeline_mod = m2_loader.module("pipeline")
reference_mod = m2_loader.module("reference")
main_mod = m2_loader.module("main")

FEATURE_ORDER = features_mod.FEATURE_ORDER
REPLICAS = FEATURE_ORDER.index("replica_count")


def ago(seconds):
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def reading(replicas, seconds_ago=60, kind="none", progress=0.0, seed=0):
    snap = snapshot(ago(seconds_ago), kind=kind, progress=progress, rng=random.Random(seed))
    return snap.model_copy(update={"metrics": snap.metrics.model_copy(update={"replica_count": replicas})})


def make_reference(**kw):
    detector = model_mod.ZScoreDetector(min_samples=20).fit({SERVICE: normal_rows(120, seed=1)})
    return reference_mod.Reference(
        detector=detector,
        alert=reference_mod.AlertLine(wobbles=80.0, normal_max_wobbles=13.0, fault_min_wobbles=480.0),
        trained_on=("capture1.json",), created="2026-10-07T00:00:00Z", **kw)


@pytest.fixture(scope="module")
def reference():
    return make_reference()


# ---------------------------------------------------------------------------
# The ruler
# ---------------------------------------------------------------------------

def test_scaling_the_service_up_is_not_scored(reference):
    ruler = reference.detector
    for replicas in (2, 10, 50):
        row = list(normal_rows(1, seed=3)[0])
        row[REPLICAS] = float(replicas)
        assert ruler.distance_many(SERVICE, [row])[0] < 10         # as quiet as a normal reading


def test_the_ignored_feature_explains_as_zero_while_every_other_feature_still_counts(reference):
    row = list(normal_rows(1, seed=3)[0])
    row[REPLICAS] = 10.0
    row[FEATURE_ORDER.index("latency_p95_ms")] = 700.0
    wobbles = dict(zip(FEATURE_ORDER, reference.detector.explain(SERVICE, row)))
    assert wobbles["replica_count"] == 0.0
    assert wobbles["latency_p95_ms"] > 1000


def test_scoring_every_column_is_still_possible_and_shows_what_the_problem_was():
    scored_everywhere = model_mod.ZScoreDetector(min_samples=20, ignore_columns=()).fit({SERVICE: normal_rows(120, seed=1)})
    row = list(normal_rows(1, seed=3)[0])
    row[REPLICAS] = 10.0
    assert scored_everywhere.distance_many(SERVICE, [row])[0] > 800     # 9 extra replicas / 0.01 wobble


def test_a_detector_over_some_other_width_is_untouched_by_the_default():
    rows = [[(-1) ** i, 7.0, 2.0 * (-1) ** i] for i in range(100)]
    ruler = model_mod.ZScoreDetector().fit({"toy": rows})
    assert ruler.distance_many("toy", [[0.0, 8.0, 0.0]])[0] > 10         # column 1 still counts


def test_any_columns_can_be_named_to_ignore():
    ruler = model_mod.ZScoreDetector(ignore_columns=(0,)).fit({"toy": [[(-1) ** i, 7.0] for i in range(100)]})
    assert ruler.distance_many("toy", [[500.0, 7.0]])[0] == 0.0


def test_context_columns_maps_names_to_positions_and_rejects_unknown_names():
    assert model_mod.context_columns() == (REPLICAS,)
    assert model_mod.context_columns(("cpu", "memory")) == (FEATURE_ORDER.index("cpu"), FEATURE_ORDER.index("memory"))
    with pytest.raises(ValueError, match="not features"):
        model_mod.context_columns(("replicas",))


# ---------------------------------------------------------------------------
# The pipeline: the cure is not a fault, and a real fault still is
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("replicas", [2, 10])
def test_after_m4_scales_the_service_m2_does_not_alert_on_that(reference, replicas):
    pipe = pipeline_mod.Pipeline(reference)
    result = pipe.process(reading(replicas))
    assert not result.alert and result.incident is None


def test_the_extra_copies_do_not_keep_an_incident_alive_after_the_fault_is_cured(reference):
    pipe = pipeline_mod.Pipeline(reference)
    for i in range(5):                                                      # the spike: an incident opens
        pipe.process(reading(1, seconds_ago=900 - 15 * i, kind="spike", progress=1.0, seed=i))
    alerts_before = len(pipe.recent_alerts(limit=500))
    assert len(pipe.incidents()) == 1
    for i in range(4):                                                      # later: 10 replicas, normal load
        pipe.process(reading(10, seconds_ago=300 - 15 * i, seed=10 + i))
    assert len(pipe.recent_alerts(limit=500)) == alerts_before
    assert len(pipe.incidents()) == 1


def test_a_real_fault_is_still_caught_while_many_replicas_run(reference):
    pipe = pipeline_mod.Pipeline(reference)
    result = pipe.process(reading(10, kind="bad", progress=1.0))
    assert result.alert and result.event.severity == "high"
    signals = pipe.incident_detail(result.incident.incident_id)["signals"]
    assert "latency_p95_ms" in signals and "replica_count" not in signals


def test_the_replica_count_is_still_recorded_in_the_evidence(reference, tmp_path):
    store = m2_loader.module("evidence").EvidenceStore(tmp_path / "evidence.jsonl")
    pipe = pipeline_mod.Pipeline(reference, evidence=store)
    pipe.process(reading(10, kind="bad", progress=1.0))
    (record,) = store.read()
    assert record["features"]["replica_count"] == 10.0          # the raw value is kept
    assert record["wobbles"]["replica_count"] == 0.0            # and marked as not scored
    assert "replica_count" not in record["signals"]


# ---------------------------------------------------------------------------
# The reference file
# ---------------------------------------------------------------------------

def test_the_reference_file_says_which_features_are_context(reference, tmp_path):
    path = tmp_path / "reference.json"
    reference_mod.save_reference(path, reference)
    assert json.loads(path.read_text())["context_features"] == ["replica_count"]
    assert reference_mod.load_reference(path).context_features == ("replica_count",)


def test_an_older_file_without_the_list_gets_the_default_without_retraining(reference, tmp_path):
    path = tmp_path / "old.json"
    reference_mod.save_reference(path, reference)
    body = json.loads(path.read_text())
    del body["context_features"]                                 # as written before this change
    path.write_text(json.dumps(body))
    loaded = reference_mod.load_reference(path)
    assert loaded.context_features == ("replica_count",)
    result = pipeline_mod.Pipeline(loaded).process(reading(10))
    assert not result.alert


def test_a_file_that_lists_no_context_features_scores_everything(reference, tmp_path):
    path = tmp_path / "strict.json"
    reference_mod.save_reference(path, make_reference(context_features=()))
    loaded = reference_mod.load_reference(path)
    assert loaded.context_features == ()
    assert pipeline_mod.Pipeline(loaded).process(reading(10)).alert


def test_a_file_naming_an_unknown_context_feature_is_refused(reference, tmp_path):
    path = tmp_path / "bad.json"
    reference_mod.save_reference(path, reference)
    body = json.loads(path.read_text())
    body["context_features"] = ["replicas"]
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="not features"):
        reference_mod.load_reference(path)


def test_the_service_reports_such_a_reference_as_invalid_and_keeps_scoring(reference, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    path = tmp_path / "bad.json"
    reference_mod.save_reference(path, reference)
    body = json.loads(path.read_text())
    body["context_features"] = ["replicas"]
    path.write_text(json.dumps(body))
    monkeypatch.setenv("M2_REFERENCE_PATH", str(path))
    main_mod.reset_state()
    client = TestClient(main_mod.app)
    health = client.get("/health").json()
    assert health["status"] == "degraded" and health["dependencies"]["reference"].startswith("invalid")
    scored = client.post("/internal/anomalies/evaluate", json=reading(1, seconds_ago=5).model_dump(mode="json"))
    assert scored.status_code == 200 and scored.json()["model"] == "threshold_baseline"      # fell back, kept scoring


# ---------------------------------------------------------------------------
# Training, and the proof that nothing validated has moved
# ---------------------------------------------------------------------------

def test_training_writes_the_context_features_into_the_reference(tmp_path):
    path = tmp_path / "c.json"
    capture_mod.save_capture(path, make_capture(11))
    other = tmp_path / "d.json"
    capture_mod.save_capture(other, make_capture(12, noise_bad=5.0))
    out = tmp_path / "reference.json"
    reference, _ = train_mod.train_reference([str(path), str(other)], out)
    assert reference.context_features == ("replica_count",)
    assert json.loads(out.read_text())["context_features"] == ["replica_count"]


def test_results_on_drill_data_are_identical_with_or_without_the_replica_count(tmp_path):
    """The replica count never moved in the real drills, so it never contributed
    to a score. Ignoring it must therefore change no result on such data."""
    runs, _ = capture_mod.capture_to_runs(make_capture(21))
    other_runs, _ = capture_mod.capture_to_runs(make_capture(22, noise_bad=5.0))
    rows = {SERVICE: [row for p in train_mod.prepare_all(other_runs) for row in train_mod.normal_rows(p)]}

    with_context = model_mod.ZScoreDetector(min_samples=20).fit(rows)
    scored_everywhere = model_mod.ZScoreDetector(min_samples=20, ignore_columns=()).fit(rows)

    def results(detector):
        ref = reference_mod.Reference(detector, reference_mod.AlertLine(48.9, 6.3, 379.0), ("x",), "now")
        return final_mod.evaluate_frozen(runs, ref)

    assert results(with_context) == results(scored_everywhere)
    assert all(r.detected for r in results(with_context) if r.ground_truth)


def test_the_alert_line_chosen_from_drill_data_is_unchanged_by_ignoring_the_replica_count(monkeypatch):
    runs = []
    for i, capture in enumerate((make_capture(31), make_capture(32, noise_bad=5.0))):
        found, _ = capture_mod.capture_to_runs(capture)
        runs += [dataclasses.replace(r, run_id=f"{i}:{r.run_id}") for r in found]
    prepared = train_mod.prepare_all(runs)

    line_with_context = train_mod.choose_alert_line(prepared).alert

    original = model_mod.ZScoreDetector.__init__
    monkeypatch.setattr(model_mod.ZScoreDetector, "__init__",
                        lambda self, min_samples=model_mod.MIN_TRAINING_SAMPLES, ignore_columns=None:
                        original(self, min_samples, ()))                  # every column scored
    line_scoring_everything = train_mod.choose_alert_line(prepared).alert

    assert line_with_context == line_scoring_everything
