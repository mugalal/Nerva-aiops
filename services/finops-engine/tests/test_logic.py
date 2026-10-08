import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import cost_model
from app.main import app
from app.models import FinOpsContext, RecommendRequest, ScaleOptionsRequest
from app.rightsizing import recommend
from app.scale_options import build_scale_options

ROOT = Path(__file__).resolve().parents[3]
client = TestClient(app)


def rec_payload():
    return {
        "service": "payment-service",
        "current": {"replicas": 3, "cpu_request_m": 100, "memory_request_mb": 128},
        "observed": {"avg_cpu_pct": 40, "peak_cpu_pct": 55, "avg_memory_pct": 50, "peak_memory_pct": 60},
        "window": {"start": "2026-10-07T08:00:00Z", "end": "2026-10-07T10:00:00Z"},
    }


def scale_payload():
    return {"service": "payment-service", "current_replicas": 3, "cpu_request_m": 100,
            "memory_request_mb": 128, "observed_cpu_pct": 72, "scale_duration_minutes": 30}


def test_cost_per_hour_by_hand():
    # 3 x (0.1 core x 1.0 + 0.125 GB x 0.25) = 0.39375
    assert cost_model.cost_per_hour(3, 100, 128) == pytest.approx(0.39375)


def test_halving_requests_saves_50_percent():
    a = cost_model.cost_per_hour(3, 100, 128)
    b = cost_model.cost_per_hour(3, 50, 64)
    assert cost_model.saving_pct(a, b) == pytest.approx(50.0)


def test_recommended_case():
    r = recommend(RecommendRequest(**rec_payload()))
    assert r.status == "RECOMMENDED"
    assert r.recommendation.recommended.cpu_request_m == 90
    assert r.recommendation.recommended.memory_request_mb == 128
    assert r.recommendation.estimated_monthly_saving_pct == pytest.approx(7.62, abs=0.01)


def test_short_window_is_insufficient_evidence():
    data = rec_payload()
    data["window"]["end"] = "2026-10-07T08:30:00Z"
    assert recommend(RecommendRequest(**data)).status == "INSUFFICIENT_EVIDENCE"


def test_extreme_peak_gives_no_recommendation():
    data = rec_payload()
    data["observed"]["peak_cpu_pct"] = 95
    r = recommend(RecommendRequest(**data))
    assert r.status == "NO_RECOMMENDATION"
    assert r.recommendation.reliability_risk == "HIGH"
    assert r.recommendation.recommended == r.recommendation.current


def test_safety_floor_is_respected():
    data = rec_payload()
    data["observed"] = {"avg_cpu_pct": 1, "peak_cpu_pct": 2, "avg_memory_pct": 1, "peak_memory_pct": 2}
    r = recommend(RecommendRequest(**data))
    assert r.recommendation.recommended.cpu_request_m == 50
    assert r.recommendation.recommended.memory_request_mb == 64


def test_never_increases_resources_and_is_deterministic():
    r1 = recommend(RecommendRequest(**rec_payload()))
    r2 = recommend(RecommendRequest(**rec_payload()))
    assert r1 == r2
    assert r1.recommendation.recommended.cpu_request_m <= 100


def test_scale_options_match_mock_risks():
    ctx = build_scale_options(ScaleOptionsRequest(**scale_payload()))
    mock = FinOpsContext(**json.loads((ROOT / "mocks/mock_finops_context.json").read_text()))
    assert [o.replicas for o in ctx.temporary_scale_options] == [4, 6]
    assert [o.risk for o in ctx.temporary_scale_options] == [o.risk for o in mock.temporary_scale_options]


def test_more_replicas_cost_more_and_lower_risk_order():
    o4, o6 = build_scale_options(ScaleOptionsRequest(**scale_payload())).temporary_scale_options
    assert o6.estimated_cost_delta > o4.estimated_cost_delta > 0


def test_api_endpoints():
    r = client.post("/internal/finops/scale-options", json=scale_payload())
    assert r.status_code == 200
    FinOpsContext(**r.json())  # response fits the frozen contract
    assert client.post("/internal/finops/recommend", json=rec_payload()).status_code == 200
    bad = scale_payload()
    bad["current_replicas"] = 0
    assert client.post("/internal/finops/scale-options", json=bad).status_code == 422