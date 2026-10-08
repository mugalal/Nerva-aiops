import pytest
from pydantic import ValidationError

from app.models import RecommendRequest, ScaleOptionsRequest


def recommend_payload():
    return {
        "service": "payment-service",
        "current": {"replicas": 3, "cpu_request_m": 100, "memory_request_mb": 128},
        "observed": {"avg_cpu_pct": 40, "peak_cpu_pct": 55,
                     "avg_memory_pct": 50, "peak_memory_pct": 60},
        "window": {"start": "2026-10-07T08:00:00Z", "end": "2026-10-07T10:00:00Z"},
    }


def scale_payload():
    return {"service": "payment-service", "current_replicas": 3,
            "cpu_request_m": 100, "memory_request_mb": 128,
            "observed_cpu_pct": 72, "scale_duration_minutes": 30}


def test_valid_recommend_request():
    assert RecommendRequest(**recommend_payload()).service == "payment-service"


def test_peak_below_average_is_rejected():
    data = recommend_payload()
    data["observed"]["peak_cpu_pct"] = 10
    with pytest.raises(ValidationError):
        RecommendRequest(**data)


def test_window_end_before_start_is_rejected():
    data = recommend_payload()
    data["window"]["end"] = "2026-10-07T07:00:00Z"
    with pytest.raises(ValidationError):
        RecommendRequest(**data)


def test_valid_scale_request():
    assert ScaleOptionsRequest(**scale_payload()).current_replicas == 3


def test_zero_replicas_is_rejected():
    data = scale_payload()
    data["current_replicas"] = 0
    with pytest.raises(ValidationError):
        ScaleOptionsRequest(**data)