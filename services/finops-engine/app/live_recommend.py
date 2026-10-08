from .m1_client import M1Unavailable, build_window_usage, fetch_window, summarize_window
from . import cost_model
from .models import (
    FinOpsRecommendation, LiveRecommendRequest, MeasurementWindow,
    ObservedUsage, RecommendRequest, RecommendResponse,
)
from .rightsizing import recommend

LIVE_ASSUMPTIONS = [
    "Utilization comes from M1 as fractions of the LIMIT, converted to percent of the REQUEST.",
    "M1 values are fleet averages across pods; a single hot pod is not visible.",
    "CPU limit and memory limit are supplied by the caller (defaults: demo 500m / 512 MB; 1 Mi treated as 1 MB).",
    "Percent of request is capped at 100; any peak >= 90% leads to NO_RECOMMENDATION anyway.",
]


def _no_data_response(req: LiveRecommendRequest, reason: str) -> RecommendResponse:
    return RecommendResponse(
        status="INSUFFICIENT_EVIDENCE",
        reason=f"{reason} (observed values are 0 placeholders: no data was measured)",
        assumptions=cost_model.ASSUMPTIONS + LIVE_ASSUMPTIONS,
        recommendation=FinOpsRecommendation(
            service=req.service,
            current=req.current,
            observed=ObservedUsage(avg_cpu_pct=0.0, avg_memory_pct=0.0),
            recommended=req.current,
            estimated_monthly_saving_pct=0.0,
            reliability_risk="MEDIUM",
        ),
    )


def recommend_live(req: LiveRecommendRequest, base_url: str,
                   fetch=fetch_window) -> RecommendResponse:
    # `fetch` is a parameter so tests can replace M1 with a fake.
    try:
        window = fetch(base_url, req.service, req.window_start,
                       req.window_end, req.step_seconds)
        summary = summarize_window(window)
    except (M1Unavailable, ValueError) as exc:
        return _no_data_response(req, f"M1 data unavailable: {exc}")

    usage = build_window_usage(summary, req.cpu_limit_m, req.memory_limit_mb,
                               req.current.cpu_request_m, req.current.memory_request_mb)
    rec_request = RecommendRequest(
        service=req.service,
        current=req.current,
        observed=usage,
        window=MeasurementWindow(start=req.window_start, end=req.window_end,
                                 sample_count=summary["sample_count"]),
        traffic_pattern=req.traffic_pattern,
    )
    response = recommend(rec_request)
    return response.model_copy(update={"assumptions": response.assumptions + LIVE_ASSUMPTIONS})