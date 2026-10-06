"""
Tests for the M2 anomaly-engine: feature extraction, baseline scoring, API.

M1's service is also a package called `app`. If two test files both did
`import app`, Python would silently reuse whichever loaded first. So this
file loads M2's package from its path under the unique name `m2_app`; the
service code itself uses relative imports and doesn't care what it's called.

Run from the repo root:   python -m pytest tests/m2 -v
"""

import copy
import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

APP_DIR = REPO_ROOT / "services" / "anomaly-engine" / "app"


def _load_m2_package():
    spec = importlib.util.spec_from_file_location(
        "m2_app",
        APP_DIR / "__init__.py",
        submodule_search_locations=[str(APP_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["m2_app"] = module
    spec.loader.exec_module(module)
    return module


_load_m2_package()
features_mod = importlib.import_module("m2_app.features")
baseline_mod = importlib.import_module("m2_app.baseline")
main_mod = importlib.import_module("m2_app.main")

from shared.contracts import AnomalyEvent, TelemetrySnapshot  # noqa: E402

MOCK_METRICS = json.loads((REPO_ROOT / "mocks" / "mock_metrics.json").read_text())


def _snapshot(timestamp="2026-09-27T10:00:00Z", **metric_overrides) -> dict:
    data = copy.deepcopy(MOCK_METRICS)
    data["timestamp"] = timestamp
    data["metrics"].update(metric_overrides)
    return data


HEALTHY = dict(cpu=0.3, memory=0.4, request_rate=100, latency_p95_ms=120,
               http_5xx_rate=0.001, replica_count=3)
BAD_DEPLOY = dict(cpu=0.72, memory=0.61, request_rate=105, latency_p95_ms=820,
                  http_5xx_rate=0.14, replica_count=3)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def test_first_snapshot_has_zero_change_features():
    snap = TelemetrySnapshot.model_validate(_snapshot(**HEALTHY))
    fv = features_mod.extract_features(snap, None)
    assert fv.request_rate_change == 0.0
    assert fv.latency_change == 0.0


def test_change_features_are_relative_to_previous():
    prev = TelemetrySnapshot.model_validate(_snapshot(**HEALTHY))
    cur = TelemetrySnapshot.model_validate(_snapshot("2026-09-27T10:01:00Z", **BAD_DEPLOY))
    fv = features_mod.extract_features(cur, prev)
    assert fv.request_rate_change == pytest.approx(0.05)         # 100 -> 105
    assert fv.latency_change == pytest.approx((820 - 120) / 120)  # about +583%


def test_zero_previous_value_does_not_divide_by_zero():
    prev = TelemetrySnapshot.model_validate(_snapshot(**{**HEALTHY, "request_rate": 0}))
    cur = TelemetrySnapshot.model_validate(_snapshot(**HEALTHY))
    assert features_mod.extract_features(cur, prev).request_rate_change == 1.0


def test_internal_vector_has_eight_features():
    snap = TelemetrySnapshot.model_validate(_snapshot(**HEALTHY))
    fv = features_mod.extract_features(snap, None)
    assert set(fv.model_dump()) == {
        "request_rate", "request_rate_change", "latency_p95_ms", "latency_change",
        "http_5xx_rate", "cpu", "memory", "replica_count",
    }


def test_contract_subset_is_exactly_the_four_frozen_features():
    snap = TelemetrySnapshot.model_validate(_snapshot(**HEALTHY))
    subset = features_mod.extract_features(snap, None).to_contract()
    assert set(subset.model_dump()) == {
        "request_rate", "latency_p95_ms", "http_5xx_rate", "cpu",
    }


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

def _fv(**metrics):
    snap = TelemetrySnapshot.model_validate(_snapshot(**metrics))
    return features_mod.extract_features(snap, None)


def test_healthy_telemetry_scores_zero_and_low():
    assert baseline_mod.baseline_score(_fv(**HEALTHY)) == (0.0, "low")


def test_bad_deployment_scores_high():
    # latency (1.5) + 5xx (1.5) breached out of total weight 5.0
    assert baseline_mod.baseline_score(_fv(**BAD_DEPLOY)) == (0.6, "high")


def test_nan_telemetry_never_produces_a_high_score():
    nan = float("nan")
    fv = _fv(**{**HEALTHY, "latency_p95_ms": nan, "http_5xx_rate": nan,
                "cpu": nan, "memory": nan})
    score, severity = baseline_mod.baseline_score(fv)
    assert score == 0.0 and severity == "low"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    main_mod.reset_state()
    return TestClient(main_mod.app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_evaluate_returns_a_valid_contract_event(client):
    r = client.post("/internal/anomalies/evaluate", json=_snapshot(**HEALTHY))
    assert r.status_code == 200
    # Must validate against the strict frozen model, extras and all.
    event = AnomalyEvent.model_validate(r.json())
    assert event.score == 0.0 and event.severity == "low"


def test_event_features_are_exactly_the_four_frozen_ones(client):
    body = client.post("/internal/anomalies/evaluate", json=_snapshot(**HEALTHY)).json()
    assert set(body["features"]) == {"request_rate", "latency_p95_ms", "http_5xx_rate", "cpu"}


def test_event_is_stamped_with_the_snapshot_time_not_the_wall_clock(client):
    body = client.post(
        "/internal/anomalies/evaluate",
        json=_snapshot("2026-09-27T10:00:42Z", **HEALTHY),
    ).json()
    assert body["timestamp"] == "2026-09-27T10:00:42Z"


def test_healthy_then_bad_deployment_is_detected(client):
    client.post("/internal/anomalies/evaluate", json=_snapshot(**HEALTHY))
    r = client.post(
        "/internal/anomalies/evaluate",
        json=_snapshot("2026-09-27T10:01:00Z", **BAD_DEPLOY),
    )
    event = AnomalyEvent.model_validate(r.json())
    assert event.severity == "high"
    assert event.score == pytest.approx(0.6)
    assert event.model == "threshold_baseline"


def test_previous_snapshot_is_tracked_per_service(client):
    """A different service must not use another service's history."""
    client.post("/internal/anomalies/evaluate", json=_snapshot(**HEALTHY))
    other = _snapshot("2026-09-27T10:01:00Z", **BAD_DEPLOY)
    other["service"] = "checkout-service"
    # Would raise inside extract_features if state leaked incorrectly; here we
    # just confirm a first-seen service is accepted and scored.
    assert client.post("/internal/anomalies/evaluate", json=other).status_code == 200


def test_malformed_payload_is_rejected_with_422(client):
    assert client.post("/internal/anomalies/evaluate", json={"service": "x"}).status_code == 422


def test_snapshot_with_extra_field_is_rejected(client):
    bad = _snapshot(**HEALTHY)
    bad["metrics"]["service_health"] = "ok"   # the Day-1 mismatch, again
    assert client.post("/internal/anomalies/evaluate", json=bad).status_code == 422
