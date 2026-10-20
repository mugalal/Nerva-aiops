"""Load features: per ready replica and only above normal, verified on genuine data."""
import json
from pathlib import Path

import m2_loader
import pytest

np = pytest.importorskip("numpy")

model_mod = m2_loader.module("model")
reference_mod = m2_loader.module("reference")
heldout = m2_loader.module("evaluation.heldout")

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs/evidence"
FEATURES = model_mod.FEATURE_ORDER


def vector(request_rate, replicas, latency=10.0):
    values = dict(request_rate=request_rate, request_rate_change=0.0, latency_p95_ms=latency, latency_change=0.0,
                  http_5xx_rate=0.0, cpu=0.24, memory=0.09, replica_count=replicas)
    return [values[name] for name in FEATURES]


def detector(load):
    rows = [vector(85 + (i % 5) - 2, 1, 10 + (i % 3) * 0.5) for i in range(60)]
    return model_mod.ZScoreDetector(load_columns=model_mod.load_columns() if load else ()).fit({"svc": rows})


def test_legacy_detector_scores_exactly_as_before():
    legacy = detector(load=False)
    rate = FEATURES.index("request_rate")
    assert legacy.explain("svc", vector(228, 8))[rate] > 50          # absolute, two-sided
    assert legacy.explain("svc", vector(30, 3))[rate] > 30


def test_load_is_scored_per_replica_and_only_above_normal():
    ruler = detector(load=True)
    rate = FEATURES.index("request_rate")
    assert ruler.explain("svc", vector(228, 8))[rate] == 0           # 28.5/replica: below normal
    assert ruler.explain("svc", vector(30, 3))[rate] == 0            # quiet is not a fault
    assert ruler.explain("svc", vector(548, 1))[rate] > 100          # a spike beyond capacity still scores
    assert ruler.explain("svc", vector(85, 1, latency=300))[FEATURES.index("latency_p95_ms")] > 100


def test_reference_round_trips_the_rule_and_old_files_keep_old_behaviour(tmp_path):
    ruler = detector(load=True)
    line = reference_mod.AlertLine(wobbles=40.0, normal_max_wobbles=5.0, fault_min_wobbles=300.0)
    saved = reference_mod.Reference(ruler, line, ("c1.json",), "2026-10-10T00:00:00Z", load_features=("request_rate",))
    path = tmp_path / "ref.json"
    reference_mod.save_reference(path, saved)
    loaded = reference_mod.load_reference(path)
    assert loaded.load_features == ("request_rate",) and loaded.detector.load_columns == model_mod.load_columns()
    body = json.loads(path.read_text())
    del body["load_features"]
    path.write_text(json.dumps(body))
    assert reference_mod.load_reference(path).detector.load_columns == ()


def test_committed_reference_on_held_out_genuine_data():
    """The frozen reference keeps fault detection on capture3 and stops alarming on a
    healthy, scaled-out workload measured in the isolated cluster."""
    reference = reference_mod.load_reference(ROOT / "services/anomaly-engine/reference/reference.json")
    # payment-service from capture1-2; checkout-service from capture4-5 (both recorded on the kind cluster).
    assert reference.trained_on == ("capture1.json", "capture2.json", "capture4-checkout.json", "capture5-checkout.json")
    faults = heldout.fault_metrics(reference, json.loads((EVIDENCE / "m2/capture3.json").read_text()))
    assert faults["bad_deployment"]["recall"] >= 0.9 and faults["traffic_spike"]["recall"] >= 0.9
    assert faults["bad_deployment"]["detection_latency_s"] <= 60 and faults["traffic_spike"]["detection_latency_s"] <= 60
    assert faults["normal"]["false_alarms"] == 0
    scaled = json.loads((EVIDENCE / "hardening-2026-10-10/m2/window-healthy-8rep-228rps.json").read_text())
    assert heldout.healthy_metrics(reference, scaled)["alarms"] == 0


def test_live_traffic_onset_alert_is_not_scored_by_the_committed_reference():
    """The exact feature vector M2 recorded live at 19:19:54Z when traffic started from idle."""
    detail = json.loads((EVIDENCE / "hardening-2026-10-10/live/onset-alert/m2-detail.json").read_text())
    event = detail["events"][0]
    assert event["wobbles"]["request_rate_change"] > 800          # what the deployed reference saw live
    reference = reference_mod.load_reference(ROOT / "services/anomaly-engine/reference/reference.json")
    features = [event["features"][name] for name in FEATURES]
    wobbles = reference.detector.explain("payment-service", features)
    assert max(wobbles) < reference.alert.wobbles
    assert wobbles[FEATURES.index("request_rate_change")] == 0     # idle -> light load is not degradation


def test_a_spike_beyond_capacity_still_scores_its_change():
    ruler = detector(load=True)
    spike = vector(548, 1)
    spike[FEATURES.index("request_rate_change")] = 5.0
    assert ruler.explain("svc", spike)[FEATURES.index("request_rate_change")] > 0


def test_committed_reference_on_held_out_checkout_capture():
    """checkout-service: capture6 was recorded after the reference was frozen and never used to train it."""
    final = m2_loader.module("realdata.final")
    reference = reference_mod.load_reference(ROOT / "services/anomaly-engine/reference/reference.json")
    assert set(reference.services) == {"payment-service", "checkout-service"}
    assert "capture6-checkout.json" not in reference.trained_on
    results, _ = final.run_evaluate([str(EVIDENCE / "m2/capture6-checkout.json")], reference)
    by_scenario = {r.scenario: r for r in results}
    assert by_scenario["traffic_spike"].detected and by_scenario["traffic_spike"].detection_delay_s <= 60
    assert by_scenario["healthy"].prediction is False
    assert all(r.false_alert_snapshots == 0 for r in results)
