"""
Tests for the Day 5 / 6 / 9 / 12 pieces: the frozen reference, the pipeline
that scores and correlates, the correlator, and the evidence store.

Needs numpy and scikit-learn (the ruler lives beside the forest):
    pip install scikit-learn numpy

Run from the repo root:   python -m pytest tests/m2 -v
"""

import json
import threading
from datetime import timedelta

import m2_loader
import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from real_like import SERVICE, T0, normal_rows, snapshot  # noqa: E402

from shared.contracts import AnomalyEvent, AnomalyFeatures, Incident  # noqa: E402

model_mod = m2_loader.module("model")
reference_mod = m2_loader.module("reference")
pipeline_mod = m2_loader.module("pipeline")
correlation_mod = m2_loader.module("correlation")
evidence_mod = m2_loader.module("evidence")
features_mod = m2_loader.module("features")

ALERT_WOBBLES = 80.0


@pytest.fixture(scope="module")
def reference():
    detector = model_mod.ZScoreDetector(min_samples=20).fit({SERVICE: normal_rows(120, seed=1)})
    return reference_mod.Reference(
        detector=detector,
        alert=reference_mod.AlertLine(wobbles=ALERT_WOBBLES, normal_max_wobbles=13.0, fault_min_wobbles=480.0),
        trained_on=("capture1.json", "capture2.json"),
        created="2026-10-07T00:00:00Z",
    )


def healthy(offset_s=0, **kw):
    return snapshot(T0 + timedelta(seconds=offset_s), rng=__import__("random").Random(offset_s), **kw)


def bad(offset_s=0, **kw):
    return snapshot(T0 + timedelta(seconds=offset_s), kind="bad", progress=1.0,
                    rng=__import__("random").Random(offset_s), **kw)


def with_metrics(snap, **changes):
    return snap.model_copy(update={"metrics": snap.metrics.model_copy(update=changes)})


# ---------------------------------------------------------------------------
# The frozen reference file
# ---------------------------------------------------------------------------

def test_a_reference_survives_save_and_load_and_scores_identically(reference, tmp_path):
    path = tmp_path / "reference.json"
    reference_mod.save_reference(path, reference)
    loaded = reference_mod.load_reference(path)
    rows = normal_rows(30, seed=9) + [[85, 0, 700, 60, 0.13, 0.05, 0.09, 1.0]]
    assert list(loaded.detector.score_many(SERVICE, rows)) == list(reference.detector.score_many(SERVICE, rows))
    assert loaded.alert == reference.alert
    assert loaded.trained_on == ("capture1.json", "capture2.json")
    assert loaded.services == [SERVICE]


def test_the_reference_is_plain_readable_json_with_where_it_came_from(reference, tmp_path):
    path = tmp_path / "reference.json"
    reference_mod.save_reference(path, reference)
    body = json.loads(path.read_text())
    assert body["detector"] == "z_score_per_service"
    assert body["feature_order"] == list(features_mod.FEATURE_ORDER)
    assert body["alert"] == {"wobbles": 80.0, "normal_max_wobbles": 13.0, "fault_min_wobbles": 480.0}
    assert body["services"][SERVICE]["n"] == 120
    assert len(body["services"][SERVICE]["mean"]) == len(body["services"][SERVICE]["spread"]) == 8


def test_the_alert_line_converts_between_wobbles_and_score(reference):
    line = reference.alert
    assert line.score == pytest.approx(80 / 85)
    assert model_mod.ZScoreDetector.wobbles_of(line.score) == pytest.approx(80.0)
    assert line.margin == pytest.approx(480 / 13)


def test_a_reference_made_for_a_different_feature_order_is_refused(reference, tmp_path):
    path = tmp_path / "reference.json"
    reference_mod.save_reference(path, reference)
    body = json.loads(path.read_text())
    body["feature_order"] = list(reversed(body["feature_order"]))
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="different feature order"):
        reference_mod.load_reference(path)


@pytest.mark.parametrize("change,message", [({"format": 99}, "unsupported"), ({"detector": "forest"}, "unknown detector")])
def test_an_unrecognised_reference_is_refused(reference, tmp_path, change, message):
    path = tmp_path / "reference.json"
    reference_mod.save_reference(path, reference)
    body = json.loads(path.read_text())
    body.update(change)
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match=message):
        reference_mod.load_reference(path)


def test_the_ruler_explains_which_features_are_far_from_normal(reference):
    vector = list(normal_rows(1, seed=3)[0])
    vector[features_mod.FEATURE_ORDER.index("latency_p95_ms")] = 700.0       # only latency moves
    wobbles = dict(zip(features_mod.FEATURE_ORDER, reference.detector.explain(SERVICE, vector)))
    assert wobbles["latency_p95_ms"] > 1000
    assert all(w < 10 for name, w in wobbles.items() if name != "latency_p95_ms")
    assert max(wobbles.values()) == pytest.approx(float(reference.detector.distance_many(SERVICE, [vector])[0]))


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

def test_with_a_reference_the_ruler_scores_and_a_normal_reading_does_not_alert(reference):
    reading = pipeline_mod.Pipeline(reference).process(healthy())
    assert reading.detector == "z_score_per_service" and reading.event.model == "z_score_per_service"
    assert not reading.alert and reading.event.severity == "low" and reading.incident is None
    assert 0.0 <= reading.event.score < reference.alert.score


def test_a_fault_alerts_and_opens_an_incident_candidate(reference):
    reading = pipeline_mod.Pipeline(reference).process(bad())
    assert reading.alert and reading.event.severity == "high"
    assert reading.event.score > 0.99
    assert reading.incident.status == "DETECTED" and reading.incident.affected_services == [SERVICE]
    assert reading.incident.anomaly_ids == [reading.event.anomaly_id]


def test_severity_follows_how_far_past_the_alert_line_a_reading_is(reference):
    def first_reading(rate):
        return pipeline_mod.Pipeline(reference).process(with_metrics(healthy(), request_rate=rate))

    # The request rate's usual wobble is 0.85 (1% of its average, the ruler's minimum),
    # so each extra request a second is about 1.2 wobbles. The alert line is 80 wobbles
    # and "high" starts at 4x that, 320 wobbles.
    just_below = first_reading(85 + 40)        # about 47 wobbles: under the line
    over = first_reading(85 + 100)             # about 118 wobbles: over the line, under 4x it
    far_over = first_reading(85 + 400)         # about 470 wobbles: past 4x the line
    assert (just_below.alert, just_below.event.severity) == (False, "low")
    assert (over.alert, over.event.severity) == (True, "medium")
    assert (far_over.alert, far_over.event.severity) == (True, "high")


def test_an_alert_names_the_signals_that_were_abnormal(reference):
    reading = pipeline_mod.Pipeline(reference).process(bad())
    detail = pipeline_mod.Pipeline(reference)
    detail.process(bad())
    signals = detail.incident_detail(detail.incidents()[0].incident_id)["signals"]
    assert {"latency_p95_ms", "http_5xx_rate", "request_rate", "cpu"} <= set(signals)
    assert "memory" not in signals and "replica_count" not in signals


def test_a_service_with_no_reference_falls_back_to_the_threshold_alarm(reference):
    pipe = pipeline_mod.Pipeline(reference)
    other = healthy(service="checkout-service")
    reading = pipe.process(with_metrics(other, latency_p95_ms=900.0, http_5xx_rate=0.14))
    assert reading.detector == "threshold_baseline" and reading.event.model == "threshold_baseline"
    assert reading.alert and reading.wobbles is None
    assert pipe.uses_ruler_for(SERVICE) and not pipe.uses_ruler_for("checkout-service")


def test_without_any_reference_the_whole_pipeline_is_the_threshold_alarm():
    pipe = pipeline_mod.Pipeline(None)
    assert pipe.detector_name == "threshold_baseline"
    assert pipe.process(healthy()).event.model == "threshold_baseline"


def test_nan_telemetry_is_never_an_alert(reference):
    reading = pipeline_mod.Pipeline(reference).process(with_metrics(healthy(), latency_p95_ms=float("nan")))
    assert reading.event.score == 0.0 and not reading.alert


def test_a_late_reading_does_not_corrupt_the_next_ones_change_features(reference):
    pipe = pipeline_mod.Pipeline(reference)
    pipe.process(healthy(0))
    pipe.process(healthy(30))
    late = pipe.process(with_metrics(healthy(15), request_rate=170.0))        # older than the newest
    assert pipe._previous[SERVICE].timestamp == T0 + timedelta(seconds=30)    # newest is still the newest
    # scored with no "change since last time", so the change feature is quiet
    assert late.wobbles["request_rate_change"] < 5


def test_a_reading_after_a_long_gap_is_not_compared_with_the_old_one(reference):
    """If M1 was away for a while, the first reading back must not look like a sudden jump."""
    pipe = pipeline_mod.Pipeline(reference, change_max_age_s=300)
    pipe.process(bad(0))                                    # last seen: a fault
    soon = pipe.process(healthy(30))                        # 30 s later: a genuine step, so it is a jump
    pipe2 = pipeline_mod.Pipeline(reference, change_max_age_s=300)
    pipe2.process(bad(0))
    later = pipe2.process(healthy(1000))                    # 1000 s later: the comparison is meaningless
    assert soon.wobbles["request_rate_change"] > 100
    assert later.wobbles["request_rate_change"] < 5 and not later.alert


def test_the_age_limit_for_change_features_can_be_widened(reference):
    pipe = pipeline_mod.Pipeline(reference, change_max_age_s=5000)
    pipe.process(bad(0))
    assert pipe.process(healthy(1000)).wobbles["request_rate_change"] > 100


def test_deployment_context_is_attached_when_the_version_changed_just_before(reference):
    pipe = pipeline_mod.Pipeline(reference)
    pipe.process(healthy(0, version="v1"))
    reading = pipe.process(bad(15, version="v2"))
    context = pipe.incident_detail(reading.incident.incident_id)["context"]["version_change"]
    assert (context["from"], context["to"]) == ("v1", "v2")
    assert context["seconds_before_alert"] == 0         # v2 was first seen on the very reading that alerted


def test_the_deployment_context_records_how_long_before_the_alert_the_version_changed(reference):
    pipe = pipeline_mod.Pipeline(reference)
    pipe.process(healthy(0, version="v1"))
    pipe.process(healthy(15, version="v2"))                  # v2 appears, still looks normal
    reading = pipe.process(bad(45, version="v2"))            # the fault shows 30 s later
    context = pipe.incident_detail(reading.incident.incident_id)["context"]["version_change"]
    assert context["seconds_before_alert"] == 30


def test_an_old_version_change_is_not_blamed(reference):
    pipe = pipeline_mod.Pipeline(reference, context_window_s=300)
    pipe.process(healthy(0, version="v1"))
    pipe.process(healthy(15, version="v2"))                    # the change, but no alert
    reading = pipe.process(bad(15 + 400, version="v2"))        # an alert 400 s later
    assert pipe.incident_detail(reading.incident.incident_id)["context"] is None


def test_recent_alerts_are_newest_first_and_only_alerts(reference):
    pipe = pipeline_mod.Pipeline(reference)
    pipe.process(healthy(0))
    first = pipe.process(bad(15)).event
    second = pipe.process(bad(30)).event
    recent = pipe.recent_alerts()
    assert [e.anomaly_id for e in recent] == [second.anomaly_id, first.anomaly_id]
    assert pipe.latest(SERVICE).anomaly_id == second.anomaly_id
    assert pipe.recent_alerts(service="nothing") == []


def test_many_threads_can_feed_the_pipeline_without_losing_or_corrupting_anything(reference, tmp_path):
    store = evidence_mod.EvidenceStore(tmp_path / "evidence.jsonl")
    pipe = pipeline_mod.Pipeline(reference, evidence=store)
    errors = []

    def worker(start):
        try:
            for i in range(25):
                pipe.process(bad(start + i))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n * 100,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    assert len(store.read()) == 100                      # every line complete and parseable
    assert len(pipe.recent_alerts(limit=500)) == 100


# ---------------------------------------------------------------------------
# Correlation: one fault, one candidate
# ---------------------------------------------------------------------------

def make_event(offset_s, service="svc", severity="medium", score=0.95, aid=None):
    return AnomalyEvent(
        anomaly_id=aid or f"A{offset_s}", timestamp=T0 + timedelta(seconds=offset_s), service=service,
        score=score, severity=severity, model="m",
        features=AnomalyFeatures(request_rate=1, latency_p95_ms=1, http_5xx_rate=0, cpu=0.1),
    )


def observe(correlator, event, signals=("latency_p95_ms",), context=None):
    return correlator.observe(event, list(signals), context, {"anomaly_id": event.anomaly_id})


def test_consecutive_alerts_become_one_candidate_not_one_per_alert():
    correlator = correlation_mod.Correlator(gap_s=120)
    candidates = [observe(correlator, make_event(i * 15)) for i in range(20)]
    assert len({c.incident_id for c in candidates}) == 1
    assert len(correlator.candidates()) == 1
    assert len(candidates[-1].anomaly_ids) == 20


def test_a_long_silence_ends_the_incident_and_the_next_alert_starts_a_new_one():
    correlator = correlation_mod.Correlator(gap_s=120)
    first = observe(correlator, make_event(0))
    same = observe(correlator, make_event(100))             # 100 s later: still the same incident
    second = observe(correlator, make_event(100 + 300))     # 300 s of silence
    assert first.incident_id == same.incident_id != second.incident_id
    assert len(correlator.candidates()) == 2


def test_the_gap_is_inclusive_and_configurable():
    tight = correlation_mod.Correlator(gap_s=30)
    a = observe(tight, make_event(0))
    assert observe(tight, make_event(30)).incident_id == a.incident_id        # exactly the gap: joins
    assert observe(tight, make_event(30 + 31)).incident_id != a.incident_id   # one second more: new


def test_each_service_has_its_own_incidents():
    correlator = correlation_mod.Correlator()
    a = observe(correlator, make_event(0, service="payment"))
    b = observe(correlator, make_event(15, service="checkout"))
    assert a.incident_id != b.incident_id
    assert [c.service for c in correlator.candidates(service="checkout")] == ["checkout"]


def test_signals_from_every_alert_are_gathered_into_the_one_incident():
    correlator = correlation_mod.Correlator()
    observe(correlator, make_event(0), signals=["latency_p95_ms"])
    candidate = observe(correlator, make_event(15), signals=["http_5xx_rate", "latency_p95_ms"])
    assert candidate.signals == {"latency_p95_ms", "http_5xx_rate"}


def test_severity_and_peak_score_keep_the_worst_seen():
    correlator = correlation_mod.Correlator()
    observe(correlator, make_event(0, severity="medium", score=0.90))
    observe(correlator, make_event(15, severity="high", score=0.99))
    candidate = observe(correlator, make_event(30, severity="low", score=0.80))
    assert candidate.severity == "high" and candidate.peak_score == 0.99


def test_the_candidate_is_a_valid_frozen_incident_contract_in_the_detected_state():
    correlator = correlation_mod.Correlator()
    candidate = observe(correlator, make_event(0))
    incident = candidate.to_incident()
    assert isinstance(incident, Incident) and incident.status == "DETECTED"
    Incident.model_validate(incident.model_dump(mode="json"))     # the strict model accepts what we emit


def test_candidates_come_newest_first_and_can_be_looked_up():
    correlator = correlation_mod.Correlator(gap_s=10)
    old = observe(correlator, make_event(0))
    new = observe(correlator, make_event(1000))
    assert [c.incident_id for c in correlator.candidates()] == [new.incident_id, old.incident_id]
    assert correlator.get(old.incident_id) is old and correlator.get("INC-nope") is None


def test_old_candidates_are_dropped_once_the_limit_is_reached():
    correlator = correlation_mod.Correlator(gap_s=1, max_candidates=3)
    for i in range(6):
        observe(correlator, make_event(i * 1000))
    assert len(correlator.candidates(limit=50)) == 3


def test_every_alert_in_an_incident_keeps_its_evidence_record():
    correlator = correlation_mod.Correlator()
    candidate = observe(correlator, make_event(0))
    observe(correlator, make_event(15))
    assert len(candidate.events) == 2
    assert all(e["incident_id"] == candidate.incident_id for e in candidate.events)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def test_every_alert_is_saved_with_all_eight_features_and_how_far_each_was_from_normal(reference, tmp_path):
    store = evidence_mod.EvidenceStore(tmp_path / "evidence.jsonl")
    pipe = pipeline_mod.Pipeline(reference, evidence=store)
    reading = pipe.process(bad())
    (record,) = store.read()
    assert record["incident_id"] == reading.incident.incident_id
    assert record["anomaly_id"] == reading.event.anomaly_id
    assert set(record["features"]) == set(features_mod.FEATURE_ORDER)       # all 8, not just the contract's 4
    assert set(record["wobbles"]) == set(features_mod.FEATURE_ORDER)
    assert record["model"] == "z_score_per_service" and record["signals"]
    assert record["features"]["latency_p95_ms"] > 600


def test_healthy_readings_leave_no_evidence(reference, tmp_path):
    store = evidence_mod.EvidenceStore(tmp_path / "evidence.jsonl")
    pipeline_mod.Pipeline(reference, evidence=store).process(healthy())
    assert store.read() == []


def test_evidence_is_only_ever_added_to(tmp_path):
    store = evidence_mod.EvidenceStore(tmp_path / "evidence.jsonl")
    store.append({"n": 1})
    store.append({"n": 2})
    assert [r["n"] for r in store.read()] == [1, 2]
    store.append({"n": 3})
    assert [r["n"] for r in store.read()] == [1, 2, 3]


def test_an_unmeasurable_value_is_written_as_null_so_the_file_stays_valid_json(reference, tmp_path):
    # A reading with a NaN never alerts under the ruler, so use the threshold-alarm
    # fallback (a service with no reference), where another rule can still fire.
    store = evidence_mod.EvidenceStore(tmp_path / "evidence.jsonl")
    pipe = pipeline_mod.Pipeline(reference, evidence=store)
    other = healthy(service="checkout-service")
    reading = pipe.process(with_metrics(other, cpu=float("nan"), http_5xx_rate=0.14))
    assert reading.alert
    raw = (tmp_path / "evidence.jsonl").read_text()
    assert "NaN" not in raw
    assert json.loads(raw)["features"]["cpu"] is None


def test_a_reading_with_a_missing_value_is_unusable_to_the_ruler_even_if_another_feature_is_extreme(reference):
    reading = pipeline_mod.Pipeline(reference).process(with_metrics(bad(), cpu=float("nan")))
    assert reading.event.score == 0.0 and not reading.alert


def test_a_disabled_store_writes_nothing_and_reads_nothing(tmp_path):
    store = evidence_mod.EvidenceStore(None)
    store.append({"n": 1})
    assert not store.enabled and store.read() == []
    assert list(tmp_path.iterdir()) == []
