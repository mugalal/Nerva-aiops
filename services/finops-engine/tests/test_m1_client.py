from datetime import datetime, timedelta, timezone

import pytest
import httpx
from fastapi.testclient import TestClient

from app.live_recommend import recommend_live
from app.m1_client import M1Unavailable, summarize_window, to_pct_of_request
from app.models import LiveRecommendRequest
from app import main, m1_client


def points(cpu, memory, n=481, last_cpu=None, last_memory=None, step=15, end=None):
    start = datetime(2026, 10, 7, 8, tzinfo=timezone.utc)
    pts = [{"timestamp": (start + timedelta(seconds=index * step)).isoformat(),
            "metrics": {"cpu": cpu, "memory": memory}} for index in range(n)]
    if last_cpu is not None:
        pts[-1]["metrics"]["cpu"] = last_cpu
        pts[-1]["metrics"]["memory"] = last_memory
    return {"service": "payment-service", "version": "v1", "start": start.isoformat(),
            "end": (end or start + timedelta(seconds=(n - 1) * step)).isoformat(),
            "step_seconds": step, "points": pts}


def live_request():
    return LiveRecommendRequest(
        service="payment-service",
        current={"replicas": 3, "cpu_request_m": 100, "memory_request_mb": 128},
        cpu_limit_m=500,
        memory_limit_mb=512,
        window_start=datetime(2026, 10, 7, 8, tzinfo=timezone.utc),
        window_end=datetime(2026, 10, 7, 10, tzinfo=timezone.utc),
    )


def fake(window):
    return lambda *args, **kwargs: window


def current_preview():
    return {
        "provider_mode": "real", "service": "payment-service",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "telemetry": {
            "service": "payment-service", "version": "v1",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": {"cpu": 0.05, "memory": 0.1, "request_rate": 20,
                        "latency_p95_ms": 10, "http_5xx_rate": 0, "replica_count": 3},
        },
        "kubernetes": {"service": "payment-service", "service_health": "ok", "version": "v1",
                       "desired_replicas": 3, "ready_replicas": 3},
        "resource_config": {"cpu_request_m": 100, "cpu_limit_m": 500,
                            "memory_request_mb": 128, "memory_limit_mb": 512, "memory_unit": "MiB"},
    }


def test_conversion_by_hand():
    # 0.18 of a 500m limit = 90m, which is 90% of a 100m request
    assert to_pct_of_request(0.18, 500, 100) == pytest.approx(90.0)
    # 0.25 of a 512 MB limit = 128 MB = 100% of a 128 MB request
    assert to_pct_of_request(0.25, 512, 128) == pytest.approx(100.0)


def test_summarize_average_and_peak():
    s = summarize_window(points(0.05, 0.10, n=4, last_cpu=0.09, last_memory=0.14))
    assert s["peak_cpu_frac"] == 0.09
    assert s["sample_count"] == 4
    assert s["avg_cpu_frac"] == pytest.approx((0.05 * 3 + 0.09) / 4)


def test_empty_window_raises():
    with pytest.raises(ValueError):
        summarize_window({"points": []})


def test_low_load_gives_a_recommendation():
    window = points(0.05, 0.10, last_cpu=0.06, last_memory=0.12)
    r = recommend_live(live_request(), "http://m1", fetch=fake(window), fetch_current=fake(current_preview()))
    assert r.status == "RECOMMENDED"
    assert r.recommendation.observed.avg_cpu_pct == pytest.approx(25.0, abs=0.1)
    assert r.recommendation.recommended.cpu_request_m <= 100


def test_demo_load_is_not_oversized():
    # 0.18 of the limit is 90% of the request, so shrinking would be unsafe
    r = recommend_live(live_request(), "http://m1", fetch=fake(points(0.18, 0.10)),
                       fetch_current=fake(current_preview()))
    assert r.status == "NO_RECOMMENDATION"
    assert r.recommendation.recommended == r.recommendation.current


def test_m1_down_gives_insufficient_evidence_not_fake_numbers():
    def broken(*args, **kwargs):
        raise M1Unavailable("connection refused")

    r = recommend_live(live_request(), "http://m1", fetch=broken)
    assert r.status == "INSUFFICIENT_EVIDENCE"
    assert "M1 data unavailable" in r.reason
    assert r.recommendation.estimated_monthly_saving_pct == 0.0


@pytest.mark.parametrize("mutation", [
    lambda w: w.update(service="other-service"),
    lambda w: w.update(version=""),
    lambda w: w.pop("version"),
    lambda w: w.update(start="2026-10-07T07:00:00Z"),
    lambda w: w.update(end="2026-10-07T11:00:00Z"),
    lambda w: w.update(step_seconds=30),
    lambda w: w.update(step_seconds=True),
    lambda w: w.update(start="2026-10-07T08:00:00"),
    lambda w: w["points"][1].update(timestamp=w["points"][0]["timestamp"]),
    lambda w: w["points"].reverse(),
    lambda w: w["points"].pop(200),
    lambda w: w["points"][200].update(timestamp="2026-10-07T08:50:05Z"),
    lambda w: w["points"][-1].update(timestamp="2026-10-07T10:00:15Z"),
    lambda w: w["points"][0].update(timestamp="2026-10-07T08:00:00"),
    lambda w: w["points"][1].update(service="other-service"),
    lambda w: w["points"][1].update(version="v2"),
    lambda w: w.update(sample_count=0),
    lambda w: w.update(sample_count=True),
    lambda w: w.update(sample_count=1000),
    lambda w: w["points"][1].update(metrics={"cpu": float("nan"), "memory": 0.1}),
    lambda w: w["points"][1].update(metrics={"cpu": 0.1, "memory": -1}),
])
def test_malformed_or_mismatched_history_cannot_authorize_downsizing(mutation):
    window = points(0.05, 0.10)
    mutation(window)
    response = recommend_live(live_request(), "http://m1", fetch=fake(window))
    assert response.status == "INSUFFICIENT_EVIDENCE"
    assert response.recommendation.recommended == response.recommendation.current
    assert response.recommendation.estimated_monthly_saving_pct == 0


def test_replayed_single_reading_cannot_masquerade_as_two_hours():
    window = points(0.05, 0.10)
    for point in window["points"]:
        point["timestamp"] = window["points"][0]["timestamp"]
    response = recommend_live(live_request(), "http://m1", fetch=fake(window))
    assert response.status == "INSUFFICIENT_EVIDENCE"
    assert "unique" in response.reason


def test_actual_coverage_is_used_instead_of_requested_duration():
    request = live_request().model_copy(update={
        "window_end": datetime(2026, 10, 7, 9, tzinfo=timezone.utc), "step_seconds": 299,
    })
    # The query spans 60 min, but its 13 actual readings span only 59 min 48 sec.
    window = points(0.05, 0.10, n=13, step=299, end=request.window_end)
    summary = summarize_window(window)
    assert (summary["observed_end"] - summary["observed_start"]).total_seconds() == 3588
    response = recommend_live(request, "http://m1", fetch=fake(window), fetch_current=fake(current_preview()))
    assert response.status == "INSUFFICIENT_EVIDENCE"
    assert response.recommendation.estimated_monthly_saving_pct == 0


def test_offset_timezones_identify_the_same_measured_instants():
    window = points(0.05, 0.10)
    offset = timezone(timedelta(hours=2))
    for field in ("start", "end"):
        window[field] = datetime.fromisoformat(window[field]).astimezone(offset).isoformat()
    for point in window["points"]:
        point["timestamp"] = datetime.fromisoformat(point["timestamp"]).astimezone(offset).isoformat()
    response = recommend_live(live_request(), "http://m1", fetch=fake(window), fetch_current=fake(current_preview()))
    assert response.status == "RECOMMENDED"
    assert any("481 unique readings" in assumption for assumption in response.assumptions)


def test_live_http_route_rejects_wrong_service_history_without_fake_savings(monkeypatch):
    window = points(0.05, 0.10)
    window["service"] = "other-service"
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=window))) as upstream:
        monkeypatch.setattr(m1_client.httpx, "get", upstream.get)
        response = TestClient(main.app).post("/internal/finops/recommend-live",
                                             json=live_request().model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["status"] == "INSUFFICIENT_EVIDENCE"
    assert response.json()["recommendation"]["estimated_monthly_saving_pct"] == 0


@pytest.mark.parametrize("field", ["window_start", "window_end"])
def test_live_route_rejects_naive_timezones_as_422_not_server_error(field):
    payload = live_request().model_dump(mode="json")
    payload[field] = "2026-10-07T09:00:00"
    assert TestClient(main.app).post("/internal/finops/recommend-live", json=payload).status_code == 422


@pytest.mark.parametrize("mutation", [
    lambda p: p["telemetry"].update(version="v2"),
    lambda p: p["resource_config"].update(cpu_limit_m=1000),
    lambda p: p["resource_config"].update(cpu_request_m=90),
    lambda p: p["resource_config"].update(memory_limit_mb=1024),
    lambda p: p["resource_config"].update(memory_request_mb=120),
    lambda p: p["telemetry"]["metrics"].update(replica_count=4),
    lambda p: p.update(provider_mode="mock"),
    lambda p: p.update(service="other-service"),
    lambda p: p.update(collected_at="2026-10-07T08:00:00Z"),
    lambda p: p["kubernetes"].update(service_health="degraded"),
])
def test_complete_history_cannot_be_applied_to_mismatching_current_deployment(mutation):
    preview = current_preview()
    mutation(preview)
    if preview["telemetry"]["version"] == "v2":
        preview["kubernetes"]["version"] = "v2"
    response = recommend_live(live_request(), "http://m1", fetch=fake(points(0.05, 0.10)),
                              fetch_current=fake(preview))
    assert response.status == "INSUFFICIENT_EVIDENCE"
    assert response.recommendation.recommended == response.recommendation.current
    assert response.recommendation.estimated_monthly_saving_pct == 0


def test_live_http_route_validates_history_and_current_resources_before_recommending(monkeypatch):
    calls = []
    def handler(req):
        calls.append(req.url.path)
        payload = current_preview() if req.url.path == "/internal/evidence/preview" else points(0.05, 0.10)
        return httpx.Response(200, json=payload)
    with httpx.Client(transport=httpx.MockTransport(handler)) as upstream:
        monkeypatch.setattr(m1_client.httpx, "get", upstream.get)
        response = TestClient(main.app).post("/internal/finops/recommend-live",
                                             json=live_request().model_dump(mode="json"))
    assert calls == ["/internal/telemetry/window", "/internal/evidence/preview"]
    assert response.status_code == 200
    assert response.json()["status"] == "RECOMMENDED"
