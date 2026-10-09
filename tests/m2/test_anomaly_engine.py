"""
Tests for the M2 anomaly-engine: feature extraction, baseline scoring, API.

Mechanism tests pin an explicit BaselineConfig, so they test how scoring
works and keep passing when the tuned numbers are re-tuned. Tests of the
running service check behaviour (alerts, severity, shape), not exact scores.

Run from the repo root:   python -m pytest tests/m2 -v
"""

import copy
import json

import m2_loader
import pytest
from fastapi.testclient import TestClient

from shared.contracts import AnomalyEvent, TelemetrySnapshot

features_mod = m2_loader.module("features")
baseline_mod = m2_loader.module("baseline")
main_mod = m2_loader.module("main")

REPO_ROOT = m2_loader.REPO_ROOT
MOCK_METRICS = json.loads((REPO_ROOT / "mocks" / "mock_metrics.json").read_text())

# The Day-1 rules, pinned. Four absolute rules, total weight 5.
PLACEHOLDER = baseline_mod.PLACEHOLDER_CONFIG


def _snapshot(timestamp="2026-09-27T10:00:00Z", **metric_overrides) -> dict:
    data = copy.deepcopy(MOCK_METRICS)
    data["timestamp"] = timestamp
    data["metrics"].update(metric_overrides)
    return data


HEALTHY = dict(cpu=0.3, memory=0.4, request_rate=100, latency_p95_ms=120,
               http_5xx_rate=0.001, replica_count=3)
BAD_DEPLOY = dict(cpu=0.72, memory=0.61, request_rate=105, latency_p95_ms=820,
                  http_5xx_rate=0.14, replica_count=3)


def _fv(**metrics):
    snap = TelemetrySnapshot.model_validate(_snapshot(**metrics))
    return features_mod.extract_features(snap, None)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def test_first_snapshot_has_zero_change_features():
    fv = _fv(**HEALTHY)
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
    assert set(_fv(**HEALTHY).model_dump()) == {
        "request_rate", "request_rate_change", "latency_p95_ms", "latency_change",
        "http_5xx_rate", "cpu", "memory", "replica_count",
    }


def test_contract_subset_is_exactly_the_four_frozen_features():
    assert set(_fv(**HEALTHY).to_contract().model_dump()) == {
        "request_rate", "latency_p95_ms", "http_5xx_rate", "cpu",
    }


# ---------------------------------------------------------------------------
# Baseline scoring (explicit configs, so these don't depend on tuned numbers)
# ---------------------------------------------------------------------------

def test_healthy_telemetry_scores_zero_and_low():
    assert baseline_mod.baseline_score(_fv(**HEALTHY), PLACEHOLDER) == (0.0, "low")


def test_bad_deployment_scores_high_under_placeholder_rules():
    # latency (1.5) + 5xx (1.5) breached, out of total weight 5.0
    assert baseline_mod.baseline_score(_fv(**BAD_DEPLOY), PLACEHOLDER) == (0.6, "high")


def test_custom_thresholds_are_respected():
    strict = baseline_mod.BaselineConfig(latency_p95_ms=100.0, http_5xx_rate=None,
                                         cpu=None, memory=None)
    # only latency is active, and 120 ms > 100 ms, so the whole weight is breached
    assert baseline_mod.baseline_score(_fv(**HEALTHY), strict)[0] == 1.0


def test_a_switched_off_rule_is_left_out_of_the_total():
    only_latency = baseline_mod.BaselineConfig(
        latency_p95_ms=500.0, http_5xx_rate=None, cpu=None, memory=None)
    thresholds, total = only_latency.compiled()
    assert total == 1.5                      # not 5.0 or 7.0
    assert thresholds.count(float("inf")) == 5


def test_nan_telemetry_never_produces_a_high_score():
    nan = float("nan")
    fv = _fv(**{**HEALTHY, "latency_p95_ms": nan, "http_5xx_rate": nan, "cpu": nan, "memory": nan})
    for config in (PLACEHOLDER, baseline_mod.DEFAULT_CONFIG):
        score, severity = baseline_mod.baseline_score(fv, config)
        assert score == 0.0 and severity == "low"


def test_alert_needs_the_score_to_reach_alert_score():
    config = baseline_mod.BaselineConfig(alert_score=0.3)
    assert baseline_mod.is_alert(0.3, config)
    assert baseline_mod.is_alert(0.6, config)
    assert not baseline_mod.is_alert(0.2, config)


def test_all_rules_off_scores_zero_instead_of_dividing_by_zero():
    off = baseline_mod.BaselineConfig(latency_p95_ms=None, http_5xx_rate=None,
                                      cpu=None, memory=None)
    assert baseline_mod.baseline_score(_fv(**BAD_DEPLOY), off) == (0.0, "low")


# ---------------------------------------------------------------------------
# API (runs with the service's real DEFAULT_CONFIG)
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    main_mod.reset_state()
    return TestClient(main_mod.app)


def test_health_has_the_four_fields_the_runtime_conventions_require(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"service", "status", "version", "environment"}
    assert body["status"] in {"ok", "degraded", "unavailable"}
    assert body["service"] == "anomaly-engine"


def test_health_reads_the_shared_runtime_variables(client, monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "m2-test")
    monkeypatch.setenv("SERVICE_VERSION", "9.9.9")
    monkeypatch.setenv("ENVIRONMENT", "integration")
    assert client.get("/health").json() == {
        "service": "m2-test", "status": "ok", "version": "9.9.9", "environment": "integration",
    }


def test_evaluate_returns_a_valid_contract_event(client):
    r = client.post("/internal/anomalies/evaluate", json=_snapshot(**HEALTHY))
    assert r.status_code == 200
    event = AnomalyEvent.model_validate(r.json())   # strict: extras rejected
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


def test_healthy_then_bad_deployment_raises_a_high_alert(client):
    client.post("/internal/anomalies/evaluate", json=_snapshot(**HEALTHY))
    r = client.post(
        "/internal/anomalies/evaluate",
        json=_snapshot("2026-09-27T10:01:00Z", **BAD_DEPLOY),
    )
    event = AnomalyEvent.model_validate(r.json())
    assert baseline_mod.is_alert(event.score)
    assert event.severity == "high"
    assert event.model == "threshold_baseline"


def test_healthy_telemetry_does_not_raise_an_alert(client):
    event = AnomalyEvent.model_validate(
        client.post("/internal/anomalies/evaluate", json=_snapshot(**HEALTHY)).json())
    assert not baseline_mod.is_alert(event.score)


def test_previous_snapshot_is_tracked_per_service(client):
    """A service's first snapshot must have zero change features even if
    another service has already been seen."""
    client.post("/internal/anomalies/evaluate", json=_snapshot(**HEALTHY))
    other = _snapshot("2026-09-27T10:01:00Z", **BAD_DEPLOY)
    other["service"] = "checkout-service"
    # With no history for checkout-service, the latency_change rule can't
    # fire, so the score must equal scoring the same reading with no history.
    expected = baseline_mod.baseline_score(
        features_mod.extract_features(TelemetrySnapshot.model_validate(other), None))[0]
    assert client.post("/internal/anomalies/evaluate", json=other).json()["score"] == expected


def test_malformed_payload_is_rejected_with_422(client):
    assert client.post("/internal/anomalies/evaluate", json={"service": "x"}).status_code == 422


def test_snapshot_with_extra_field_is_rejected(client):
    bad = _snapshot(**HEALTHY)
    bad["metrics"]["service_health"] = "ok"   # the Day-1 mismatch, again
    assert client.post("/internal/anomalies/evaluate", json=bad).status_code == 422
