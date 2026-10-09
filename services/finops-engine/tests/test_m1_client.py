from datetime import datetime, timezone

import pytest

from app.live_recommend import recommend_live
from app.m1_client import M1Unavailable, summarize_window, to_pct_of_request
from app.models import LiveRecommendRequest


def points(cpu, memory, n=240, last_cpu=None, last_memory=None):
    pts = [{"timestamp": "2026-10-07T08:00:00Z",
            "metrics": {"cpu": cpu, "memory": memory}} for _ in range(n)]
    if last_cpu is not None:
        pts[-1]["metrics"]["cpu"] = last_cpu
        pts[-1]["metrics"]["memory"] = last_memory
    return {"points": pts}


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
    r = recommend_live(live_request(), "http://m1", fetch=fake(window))
    assert r.status == "RECOMMENDED"
    assert r.recommendation.observed.avg_cpu_pct == pytest.approx(25.0, abs=0.1)
    assert r.recommendation.recommended.cpu_request_m <= 100


def test_demo_load_is_not_oversized():
    # 0.18 of the limit is 90% of the request, so shrinking would be unsafe
    r = recommend_live(live_request(), "http://m1", fetch=fake(points(0.18, 0.10)))
    assert r.status == "NO_RECOMMENDATION"
    assert r.recommendation.recommended == r.recommendation.current


def test_m1_down_gives_insufficient_evidence_not_fake_numbers():
    def broken(*args, **kwargs):
        raise M1Unavailable("connection refused")

    r = recommend_live(live_request(), "http://m1", fetch=broken)
    assert r.status == "INSUFFICIENT_EVIDENCE"
    assert "M1 data unavailable" in r.reason
    assert r.recommendation.estimated_monthly_saving_pct == 0.0
