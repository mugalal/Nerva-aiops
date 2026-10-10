from datetime import datetime, timedelta, timezone
import math

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main, m1_client
from app.context_provider import ContextUnavailable, build_context
from app.models import FinOpsContext, LiveRecommendRequest
from app.resource_config import ResourceConfiguration


RESOURCE_ENV = {
    "FINOPS_CPU_REQUEST_M": "100", "FINOPS_CPU_LIMIT_M": "1000",
    "FINOPS_MEMORY_REQUEST_MB": "128", "FINOPS_MEMORY_LIMIT_MB": "512",
}


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    for name in [*RESOURCE_ENV, "FINOPS_DEFAULT_SERVICE", "FINOPS_SCALE_DURATION_MINUTES", "FINOPS_M1_MAX_AGE_SECONDS"]:
        monkeypatch.delenv(name, raising=False)


def preview(cpu=0.08, service="payment-service"):
    return {
        "provider_mode": "real", "service": service,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "telemetry": {
            "service": service, "version": "v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": {"cpu": cpu, "memory": 0.1, "request_rate": 20,
                        "latency_p95_ms": 10, "http_5xx_rate": 0, "replica_count": 3},
        },
        "kubernetes": {"service": service, "service_health": "ok", "version": "v1",
                       "desired_replicas": 3, "ready_replicas": 3},
        "resource_config": {"cpu_request_m": 100, "cpu_limit_m": 1000,
                            "memory_request_mb": 128, "memory_limit_mb": 512, "memory_unit": "MiB"},
    }


def context(data):
    return build_context("http://m1", "payment-service", fetch=lambda *args: data)[0]


def explicit_environment(monkeypatch):
    for name, value in RESOURCE_ENV.items():
        monkeypatch.setenv(name, value)


def test_actual_resources_override_conflicting_environment(monkeypatch):
    explicit_environment(monkeypatch)
    monkeypatch.setenv("FINOPS_CPU_LIMIT_M", "500")
    result = context(preview())
    assert result.observed_cpu_pct == pytest.approx(80)
    assert result.current_cpu_request_m == 100
    assert [option.risk for option in result.temporary_scale_options] == ["MEDIUM", "LOW"]


def test_docker_configuration_is_explicit_and_uses_1000m(monkeypatch):
    explicit_environment(monkeypatch)
    data = preview()
    data["kubernetes"] = data["resource_config"] = None
    assert context(data).observed_cpu_pct == pytest.approx(80)


def test_missing_explicit_configuration_never_guesses_500m():
    data = preview()
    data["kubernetes"] = data["resource_config"] = None
    with pytest.raises(ContextUnavailable, match="FINOPS_CPU_LIMIT_M"):
        context(data)


@pytest.mark.parametrize("value", [0, -1, "NaN", "Infinity", True, "100m"])
def test_invalid_explicit_resource_configuration_is_unavailable(monkeypatch, value):
    explicit_environment(monkeypatch)
    monkeypatch.setenv("FINOPS_CPU_LIMIT_M", str(value))
    data = preview()
    data["kubernetes"] = data["resource_config"] = None
    with pytest.raises(ContextUnavailable):
        context(data)


@pytest.mark.parametrize("field,value", [
    ("cpu_limit_m", 0), ("cpu_request_m", 2000), ("memory_limit_mb", 100),
    ("memory_unit", "MB"), ("memory_request_mb", float("inf")),
])
def test_bad_actual_resources_are_not_replaced_with_environment(monkeypatch, field, value):
    explicit_environment(monkeypatch)
    data = preview()
    data["resource_config"][field] = value
    with pytest.raises(ContextUnavailable):
        context(data)


def test_fractional_quantities_keep_exact_conversion():
    data = preview(cpu=0.1)
    data["resource_config"].update(cpu_request_m=100.5, cpu_limit_m=505, memory_request_mb=128.25)
    result = context(data)
    assert result.observed_cpu_pct == round(0.1 * 505 / 100.5 * 100, 2)
    assert result.current_cpu_request_m == 101  # Integer contract/cost reservation rounds upward.


def test_request_and_limit_use_the_same_memory_unit():
    configuration = ResourceConfiguration.from_mapping(preview()["resource_config"], "kubernetes")
    assert m1_client.to_pct_of_request(0.25, configuration.memory_limit_mb, configuration.memory_request_mb) == 100


def test_risk_preserves_overload_above_contract_display_cap():
    result = context(preview(cpu=0.4))  # Actual usage is 400% of the 100m request.
    assert result.observed_cpu_pct == 100
    assert all(option.risk == "HIGH" for option in result.temporary_scale_options if option.replicas <= 10)
    low_risk = [option for option in result.temporary_scale_options if option.risk == "LOW"]
    assert low_risk and min(option.replicas for option in low_risk) > 10


def test_real_saturation_adds_sufficient_capacity_with_truthful_risk():
    data = preview(cpu=0.95)
    data["resource_config"]["cpu_limit_m"] = 500
    data["telemetry"]["metrics"]["replica_count"] = 1
    data["kubernetes"].update(desired_replicas=1, ready_replicas=1)
    result = context(data)  # 475% of the request needs substantial extra capacity.
    options = {option.replicas: option for option in result.temporary_scale_options}
    assert result.observed_cpu_pct == 100
    assert options[2].risk == "HIGH"
    assert options[7].risk == "MEDIUM"
    assert options[10].risk == "LOW"
    assert options[10].estimated_cost_delta > options[7].estimated_cost_delta > 0


def test_computed_targets_are_deduplicated_and_preserve_normal_options():
    normal = context(preview())
    assert [option.replicas for option in normal.temporary_scale_options] == [4, 6]
    data = preview(cpu=0.11)
    data["telemetry"]["metrics"]["replica_count"] = 1
    data["kubernetes"].update(desired_replicas=1, ready_replicas=1)
    result = context(data)
    assert [option.replicas for option in result.temporary_scale_options] == [2, 3]
    assert result.temporary_scale_options[-1].risk == "LOW"


@pytest.mark.parametrize("age", [90, -90])
def test_stale_or_future_snapshot_is_unavailable(age):
    data = preview()
    data["telemetry"]["timestamp"] = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    with pytest.raises(ContextUnavailable, match="stale or dated"):
        context(data)


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(provider_mode="mock"),
    lambda data: data.update(service="other-service"),
    lambda data: data["telemetry"].update(service="other-service"),
    lambda data: data["telemetry"].update(version=""),
    lambda data: data["telemetry"].update(timestamp="2026-10-09T08:00:00"),
    lambda data: data["telemetry"]["metrics"].update(cpu=float("nan")),
    lambda data: data["telemetry"]["metrics"].update(memory=-1),
    lambda data: data["telemetry"]["metrics"].update(http_5xx_rate=2),
    lambda data: data["telemetry"]["metrics"].update(replica_count=1.5),
    lambda data: data["telemetry"]["metrics"].update(replica_count=True),
    lambda data: data["telemetry"]["metrics"].pop("request_rate"),
])
def test_invalid_real_provider_evidence_is_unavailable(mutation):
    data = preview()
    mutation(data)
    with pytest.raises(ContextUnavailable):
        context(data)


@pytest.mark.parametrize("health,version", [("degraded", "v1"), ("ok", "v2")])
def test_unready_rollout_never_falls_back_to_environment(monkeypatch, health, version):
    explicit_environment(monkeypatch)
    data = preview()
    data["kubernetes"].update(service_health=health, version=version)
    data["resource_config"] = None
    with pytest.raises(ContextUnavailable, match="rollout"):
        context(data)


def test_known_kubernetes_without_resources_is_unavailable(monkeypatch):
    explicit_environment(monkeypatch)
    data = preview()
    data["resource_config"] = None
    with pytest.raises(ContextUnavailable, match="verified Kubernetes"):
        context(data)


@pytest.mark.parametrize("age", [90, -90])
def test_fresh_telemetry_does_not_authorize_stale_resource_collection(age):
    data = preview()
    data["collected_at"] = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    with pytest.raises(ContextUnavailable, match="resource collection"):
        context(data)


@pytest.mark.parametrize("field,value", [("desired_replicas", 4), ("ready_replicas", 2),
                                         ("ready_replicas", True), ("desired_replicas", 3.0)])
def test_scale_context_requires_matching_actual_replica_state(field, value):
    data = preview()
    data["kubernetes"][field] = value
    with pytest.raises(ContextUnavailable, match="replica state"):
        context(data)


def test_orphan_resource_configuration_cannot_be_used_as_verified_kubernetes_data(monkeypatch):
    explicit_environment(monkeypatch)
    data = preview()
    data["kubernetes"] = None
    with pytest.raises(ContextUnavailable, match="matching Kubernetes"):
        context(data)


def test_http_get_matching_m4_returns_frozen_context_for_requested_service(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=preview(service=request.url.params["service"]))
    with httpx.Client(transport=httpx.MockTransport(handler)) as upstream:
        monkeypatch.setattr(m1_client.httpx, "get", upstream.get)
        response = TestClient(main.app).get("/internal/finops/context?service=checkout-service")
    assert response.status_code == 200
    result = FinOpsContext(**response.json())
    assert result.service == "checkout-service"
    assert set(response.json()) == {"service", "current_replicas", "current_cpu_request_m", "observed_cpu_pct", "temporary_scale_options"}
    assert calls[0].url.path == "/internal/evidence/preview"
    assert calls[0].url.params["service"] == "checkout-service"


@pytest.mark.parametrize("status,payload", [(503, {"detail": "stale"}), (200, []), (200, {"provider_mode": "mock"})])
def test_route_returns_503_for_dependency_or_provider_failures(monkeypatch, status, payload):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload))) as upstream:
        monkeypatch.setattr(m1_client.httpx, "get", upstream.get)
        response = TestClient(main.app).get("/internal/finops/context?service=payment-service")
    assert response.status_code == 503
    assert "temporary_scale_options" not in response.json()


@pytest.mark.parametrize("service", ["", "payment/service", "payment service"])
def test_bad_service_query_is_422(service):
    assert TestClient(main.app).get("/internal/finops/context", params={"service": service}).status_code == 422


def test_live_recommendation_requires_explicit_limits():
    payload = {
        "service": "payment-service", "current": {"replicas": 1, "cpu_request_m": 100, "memory_request_mb": 128},
        "window_start": "2026-10-08T08:00:00Z", "window_end": "2026-10-08T10:00:00Z",
    }
    with pytest.raises(ValidationError):
        LiveRecommendRequest(**payload)
    request = LiveRecommendRequest(**payload, cpu_limit_m=1000, memory_limit_mb=512)
    assert request.cpu_limit_m == 1000


@pytest.mark.parametrize("fraction,limit,resource_request", [
    (-1, 1000, 100), (math.nan, 1000, 100), (0.1, math.inf, 100),
    (0.1, 1000, 0), (0.1, 100, 500), (True, 1000, 100),
])
def test_bad_conversion_inputs_are_rejected(fraction, limit, resource_request):
    with pytest.raises(ValueError):
        m1_client.to_pct_of_request(fraction, limit, resource_request)
