import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

import app.main as main_module
from app.context_provider import ContextUnavailable, build_context
from app.m1_client import M1Unavailable
from app.models import FinOpsContext


def snapshot(cpu=0.144, replicas=3):
    return {
        "service": "payment-service", "provider_mode": "real",
        "telemetry": {
            "service": "payment-service", "version": "v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": {"cpu": cpu, "memory": 0.2, "replica_count": replicas,
                        "request_rate": 20, "latency_p95_ms": 10, "http_5xx_rate": 0},
        },
        "kubernetes": {"service": "payment-service", "version": "v1", "service_health": "ok"},
        "resource_config": {"cpu_request_m": 100, "cpu_limit_m": 500,
                            "memory_request_mb": 128, "memory_limit_mb": 512, "memory_unit": "MiB"},
    }


def fake(data):
    return lambda base_url, service: data


def test_context_from_snapshot_matches_frozen_shape():
    # 0.144 of a 500m limit = 72m = 72% of a 100m request
    context, request = build_context("http://m1", fetch=fake(snapshot()))
    FinOpsContext(**context.model_dump())
    assert context.current_replicas == 3
    assert context.observed_cpu_pct == pytest.approx(72.0)
    assert [o.replicas for o in context.temporary_scale_options] == [4, 6]
    assert [o.risk for o in context.temporary_scale_options] == ["MEDIUM", "LOW"]


def test_m1_down_raises_instead_of_inventing_data():
    def broken(base_url, service):
        raise M1Unavailable("connection refused")

    with pytest.raises(ContextUnavailable):
        build_context("http://m1", fetch=broken)


def test_zero_replicas_is_not_valid_data():
    with pytest.raises(ContextUnavailable):
        build_context("http://m1", fetch=fake(snapshot(replicas=0)))


def test_route_answers_get_and_post_without_a_body(monkeypatch):
    monkeypatch.setattr(main_module, "build_context",
                        lambda base_url, service: build_context(base_url, service, fetch=fake(snapshot())))
    client = TestClient(main_module.app)
    for method in (client.get, client.post):
        response = method("/internal/finops/context")
        assert response.status_code == 200
        assert "temporary_scale_options" in response.json()


def test_route_returns_503_when_no_data(monkeypatch):
    def unavailable(base_url, service):
        raise ContextUnavailable("M1 data unavailable: down")

    monkeypatch.setattr(main_module, "build_context", unavailable)
    client = TestClient(main_module.app)
    assert client.get("/internal/finops/context").status_code == 503
