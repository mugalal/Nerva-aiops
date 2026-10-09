"""Controlled HTTP evidence tests; these do not claim a live cluster run."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main as main_module
from app.providers.http import HttpEvidenceProviders
from app.service import RCAService


def evidence(scenario="traffic_spike"):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = lambda seconds: (now + timedelta(seconds=seconds)).isoformat()
    stats = lambda value: {"minimum": value, "maximum": value, "average": value, "p95": value}
    faulty = scenario == "faulty_deployment"
    metrics = {"request_rate": 30 if faulty else 90, "latency_p95_ms": 578 if faulty else 35,
               "http_5xx_rate": 0.14 if faulty else 0, "cpu": 0.7 if faulty else 0.95,
               "memory": 0.2, "replica_count": 1}
    incident = {"incident_id": "INC-LIVE", "started_at": stamp(-45), "status": "DETECTED",
                "severity": "high", "affected_services": ["payment-service"], "anomaly_ids": ["AN-LIVE"]}
    anomaly = {"anomaly_id": "AN-LIVE", "timestamp": stamp(-10), "service": "payment-service",
               "score": 0.95, "severity": "high", "model": "controlled-test",
               "features": {name: metrics[name] for name in ("request_rate", "latency_p95_ms", "http_5xx_rate", "cpu")}}
    baseline = {"service": "payment-service", "version": "v1", "measured_at": stamp(-90),
                "window_start": stamp(-180), "window_end": stamp(-105), "sample_count": 6,
                "request_rate": stats(30), "latency_p95_ms": stats(10), "http_5xx_rate": stats(0),
                "cpu": stats(0.3), "memory": stats(0.2), "replica_count": stats(1),
                "thresholds": {"latency_p95_ms_max": 12.5, "http_5xx_rate_max": 0.01}}
    deployment = {"event_id": "DEP-LIVE", "service": "payment-service", "old_version": "v1",
                  "new_version": "v2" if faulty else "v1", "commit_sha": "test-sha",
                  "pipeline_id": "controlled-test", "timestamp": stamp(-52 if faulty else -900),
                  "status": "SUCCESS"}
    preview = {"service": "payment-service", "provider_mode": "real", "collected_at": stamp(0),
               "telemetry": {"timestamp": stamp(-5), "service": "payment-service",
                             "version": "v2" if faulty else "v1", "metrics": metrics},
               "deployment_event": deployment, "selected_logs": [], "provider_errors": [],
               "baseline": baseline, "kubernetes": None, "resource_config": None}
    return now, {"incident": incident, "anomaly": anomaly, "preview": preview}


def client_for(monkeypatch, data, now, overrides=None):
    calls = []
    overrides = overrides or {}

    def handle(request):
        calls.append(request)
        assert request.method == "GET", "RCA must not capture or mutate incident evidence"
        path = request.url.path
        if path in overrides:
            override = overrides[path]
            if isinstance(override, Exception):
                raise override
            return httpx.Response(override, json={"detail": "controlled unavailable"})
        if path == "/api/incidents/":
            return httpx.Response(200, json=[data["incident"]])
        if path == "/api/incidents/INC-LIVE":
            assert request.url.host == "m4"
            return httpx.Response(200, json=data["incident"])
        if path == "/internal/anomalies":
            assert request.url.host == "m4"
            assert request.url.params["incident_id"] == "INC-LIVE"
            return httpx.Response(200, json=data["anomaly"])
        if path == "/internal/evidence/preview":
            assert request.url.host == "m1"
            assert request.url.params["service"] == "payment-service"
            return httpx.Response(200, json=data["preview"])
        return httpx.Response(404, json={"detail": "Incident not found"})

    providers = HttpEvidenceProviders("http://m1", "http://m4", transport=httpx.MockTransport(handle))
    service = RCAService(mode="real", providers=providers, clock=lambda: now)
    monkeypatch.setattr(main_module, "rca_service", service)
    return TestClient(main_module.app), calls


@pytest.mark.parametrize("scenario,component", [("faulty_deployment", "payment-service:v2"),
                                               ("traffic_spike", "payment-service:v1")])
def test_real_http_evidence_classifies_controlled_scenarios(monkeypatch, scenario, component):
    now, data = evidence(scenario)
    client, calls = client_for(monkeypatch, data, now)
    response = client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"})
    assert response.status_code == 200
    result = response.json()
    assert result["incident_id"] == "INC-LIVE"
    assert result["root_cause"] == scenario
    assert result["affected_component"] == component
    assert result["confidence"] >= 0.7
    assert len(calls) == 3
    assert set(result) == {"incident_id", "root_cause", "affected_component", "confidence", "evidence"}
    if scenario == "traffic_spike":
        assert any("measured healthy baseline 30.00" in entry for entry in result["evidence"])


def test_real_traffic_diagnosis_binds_the_observed_version(monkeypatch):
    now, data = evidence()
    data["preview"]["telemetry"]["version"] = "v3"
    data["preview"]["baseline"]["version"] = "v3"
    data["preview"]["deployment_event"].update(old_version="v3", new_version="v3")
    client, _ = client_for(monkeypatch, data, now)
    result = client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"}).json()
    assert result["root_cause"] == "traffic_spike"
    assert result["affected_component"] == "payment-service:v3"


def test_default_mode_and_urls_are_real(monkeypatch):
    for name in ("M3_PROVIDER_MODE", "M1_TELEMETRY_BASE_URL", "SHARED_NEXUS_API_BASE_URL", "M4_DECISION_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    service = RCAService()
    assert service.mode == "real"
    assert service.providers.m1_url == "http://localhost:8001"
    assert service.providers.shared_url == "http://localhost:8004"
    assert not hasattr(service, "incident_provider")


def test_unknown_incident_is_404_and_other_sources_are_not_read(monkeypatch):
    now, data = evidence()
    client, calls = client_for(monkeypatch, data, now)
    response = client.post("/internal/rca/analyze", json={"incident_id": "INC-MISSING"})
    assert response.status_code == 404
    assert len(calls) == 1


@pytest.mark.parametrize("path,status,expected", [("/api/incidents/INC-LIVE", 503, 503),
                                                 ("/internal/evidence/preview", 503, 503),
                                                 ("/api/incidents/INC-LIVE", 404, 404)])
def test_mandatory_http_failure_has_typed_error_and_no_fallback(monkeypatch, path, status, expected):
    now, data = evidence()
    client, _ = client_for(monkeypatch, data, now, {path: status})
    response = client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"})
    assert response.status_code == expected
    assert isinstance(response.json()["detail"], dict)
    assert response.json()["detail"]["provider"] in {"incident", "telemetry"}


@pytest.mark.parametrize("key,value", [("incident_id", "INC-OTHER"), ("started_at", "2026-01-01T10:00:00")])
def test_wrong_or_malformed_incident_is_rejected(monkeypatch, key, value):
    now, data = evidence()
    data["incident"][key] = value
    client, _ = client_for(monkeypatch, data, now)
    assert client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"}).status_code == 502


@pytest.mark.parametrize("change", ["service", "telemetry_service", "mock_mode", "future_telemetry",
                                   "stale_preview", "wrong_deployment", "wrong_version", "wrong_baseline",
                                   "late_baseline", "bad_statistics", "nan_metric", "naive_timestamp"])
def test_preview_identity_time_and_numeric_validation(monkeypatch, change):
    now, data = evidence()
    preview = data["preview"]
    if change == "service": preview["service"] = "orders-service"
    elif change == "telemetry_service": preview["telemetry"]["service"] = "orders-service"
    elif change == "mock_mode": preview["provider_mode"] = "mock"
    elif change == "future_telemetry": preview["telemetry"]["timestamp"] = (now + timedelta(minutes=1)).isoformat()
    elif change == "stale_preview": preview["collected_at"] = (now - timedelta(minutes=5)).isoformat()
    elif change == "wrong_deployment": preview["deployment_event"]["service"] = "orders-service"
    elif change == "wrong_version": preview["deployment_event"]["new_version"] = "v3"
    elif change == "wrong_baseline": preview["baseline"]["service"] = "orders-service"
    elif change == "late_baseline": preview["baseline"]["measured_at"] = now.isoformat()
    elif change == "bad_statistics": preview["baseline"]["request_rate"]["maximum"] = 1
    elif change == "nan_metric": preview["telemetry"]["metrics"]["cpu"] = "NaN"
    else: preview["telemetry"]["timestamp"] = "2026-01-01T10:00:00"
    client, _ = client_for(monkeypatch, data, now)
    response = client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"})
    assert response.status_code == 502
    assert response.json()["detail"]["category"] == "invalid_response"


@pytest.mark.parametrize("change", ["id", "service", "future", "stale"])
def test_unlinked_or_wrong_anomaly_is_rejected(monkeypatch, change):
    now, data = evidence()
    if change == "id": data["anomaly"]["anomaly_id"] = "AN-OTHER"
    elif change == "service": data["anomaly"]["service"] = "orders-service"
    elif change == "future": data["anomaly"]["timestamp"] = (now + timedelta(minutes=1)).isoformat()
    else: data["anomaly"]["timestamp"] = (now - timedelta(minutes=10)).isoformat()
    client, _ = client_for(monkeypatch, data, now)
    assert client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"}).status_code == 502


@pytest.mark.parametrize("change", ["missing_anomaly", "unavailable_deployment", "missing_baseline", "healthy_latency"])
def test_missing_or_insufficient_evidence_is_unknown(monkeypatch, change):
    now, data = evidence()
    overrides = {}
    if change == "missing_anomaly": overrides["/internal/anomalies"] = 404
    elif change == "unavailable_deployment":
        data["preview"]["deployment_event"] = None
        data["preview"]["provider_errors"] = ["Kubernetes deployment evidence unavailable"]
    elif change == "missing_baseline": data["preview"]["baseline"] = None
    else:
        data["anomaly"]["features"]["latency_p95_ms"] = 10
        data["preview"]["telemetry"]["metrics"]["latency_p95_ms"] = 10
    client, _ = client_for(monkeypatch, data, now, overrides)
    response = client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"})
    assert response.status_code == 200
    assert response.json()["root_cause"] == "unknown"
    if change in {"missing_anomaly", "unavailable_deployment"}:
        assert any("Evidence unavailable" in entry for entry in response.json()["evidence"])


def test_transport_timeout_is_typed_and_does_not_use_mock_files(monkeypatch):
    now, data = evidence()
    client, _ = client_for(monkeypatch, data, now, {"/internal/evidence/preview": httpx.ReadTimeout("controlled timeout")})
    response = client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"})
    assert response.status_code == 503
    assert response.json()["detail"]["category"] == "timeout"
    assert response.json()["detail"]["retryable"] is True


def test_health_reports_actual_provider_availability(monkeypatch):
    now, data = evidence()
    client, calls = client_for(monkeypatch, data, now)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["provider_mode"] == "real"
    assert response.json()["status"] == "ok"
    assert {call.url.path for call in calls} == {"/api/incidents/", "/internal/evidence/preview"}
    client, _ = client_for(monkeypatch, data, now, {"/internal/evidence/preview": 503})
    assert client.get("/health").json()["status"] == "unavailable"


def test_recent_version_change_is_not_misclassified_as_traffic(monkeypatch):
    now, data = evidence()
    data["preview"]["telemetry"]["version"] = "v2"
    data["preview"]["deployment_event"].update(new_version="v2", timestamp=(now - timedelta(seconds=52)).isoformat())
    client, _ = client_for(monkeypatch, data, now)
    response = client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"})
    assert response.status_code == 200
    assert response.json()["root_cause"] == "unknown"
    assert any("ambiguous" in entry for entry in response.json()["evidence"])


@pytest.mark.parametrize("scenario", ["faulty_deployment", "traffic_spike"])
def test_measured_slo_boundary_is_healthy_not_a_degradation(monkeypatch, scenario):
    now, data = evidence(scenario)
    for measurements in (data["anomaly"]["features"], data["preview"]["telemetry"]["metrics"]):
        measurements["latency_p95_ms"] = 12.5
        measurements["http_5xx_rate"] = 0.01
    client, _ = client_for(monkeypatch, data, now)
    response = client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"})
    assert response.status_code == 200
    assert response.json()["root_cause"] == "unknown"


@pytest.mark.parametrize("sample,average", [(0.1, 0.10000000000000002), (0.3, 0.29999999999999993)])
def test_valid_baseline_float_roundoff_is_accepted(monkeypatch, sample, average):
    now, data = evidence()
    data["preview"]["baseline"]["cpu"] = {
        "minimum": sample, "maximum": sample, "average": average, "p95": sample}
    client, _ = client_for(monkeypatch, data, now)
    response = client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"})
    assert response.status_code == 200
    assert response.json()["root_cause"] == "traffic_spike"


def test_real_faulty_deployment_without_baseline_is_unknown_and_health_degraded(monkeypatch):
    now, data = evidence("faulty_deployment")
    data["preview"]["baseline"] = None
    client, _ = client_for(monkeypatch, data, now)
    response = client.post("/internal/rca/analyze", json={"incident_id": "INC-LIVE"})
    assert response.status_code == 200
    assert response.json()["root_cause"] == "unknown"
    assert response.json()["confidence"] < 0.6
    assert any("baseline is missing" in item for item in response.json()["evidence"])
    health = client.get("/health").json()
    assert health["status"] == "degraded"
    assert "baseline is missing" in health["dependencies"]["telemetry"]["detail"]
