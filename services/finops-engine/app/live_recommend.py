from .m1_client import M1Unavailable, build_window_usage, fetch_preview, fetch_window, summarize_window
from .context_provider import validate_evidence_preview
from . import cost_model
from .models import (
    FinOpsRecommendation, LiveRecommendRequest, MeasurementWindow,
    ObservedUsage, RecommendRequest, RecommendResponse,
)
from .rightsizing import recommend

LIVE_ASSUMPTIONS = [
    "Utilization comes from M1 as fractions of the LIMIT, converted to percent of the REQUEST.",
    "M1 values are fleet averages across pods; a single hot pod is not visible.",
    "Explicit caller requests, limits and replicas must match current M1 deployment evidence; CPU is millicores and memory is MiB.",
    "Deployment resource configuration must remain unchanged throughout the measured release; M1's window does not expose historical resource configurations.",
    "M1 must supply one identified release and a complete, ordered grid of unique timezone-aware readings; sizing uses the actual first-to-last reading duration.",
    "Percent of request is capped at 100; any peak >= 90% leads to NO_RECOMMENDATION anyway.",
]


def _no_data_response(req: LiveRecommendRequest, reason: str) -> RecommendResponse:
    return RecommendResponse(
        status="INSUFFICIENT_EVIDENCE",
        reason=f"{reason} (observed values are 0 placeholders: no usable measurement was accepted)",
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
                   fetch=fetch_window, fetch_current=fetch_preview) -> RecommendResponse:
    # Both upstream operations are injectable so tests can exercise real guards.
    try:
        window = fetch(base_url, req.service, req.window_start,
                       req.window_end, req.step_seconds)
        summary = summarize_window(window, expected_service=req.service,
                                   expected_start=req.window_start, expected_end=req.window_end,
                                   expected_step=req.step_seconds)
        metrics, resources, version = validate_evidence_preview(fetch_current(base_url, req.service), req.service)
        if summary["version"] != version:
            raise ValueError("M1 historical release does not match the current deployed release")
        if (req.current.replicas != metrics["replica_count"]
                or req.current.cpu_request_m != resources.cpu_request_m
                or req.current.memory_request_mb != resources.memory_request_mb
                or req.cpu_limit_m != resources.cpu_limit_m
                or req.memory_limit_mb != resources.memory_limit_mb):
            raise ValueError("Caller resources do not match current M1 deployment evidence")
        usage = build_window_usage(summary, req.cpu_limit_m, req.memory_limit_mb,
                                   req.current.cpu_request_m, req.current.memory_request_mb)
    except (M1Unavailable, KeyError, ValueError, TypeError, OverflowError) as exc:
        return _no_data_response(req, f"M1 data unavailable: {exc}")

    rec_request = RecommendRequest(
        service=req.service,
        current=req.current,
        observed=usage,
        window=MeasurementWindow(start=summary["observed_start"], end=summary["observed_end"],
                                 sample_count=summary["sample_count"]),
        traffic_pattern=req.traffic_pattern,
    )
    response = recommend(rec_request)
    evidence = (f"Measured release {summary['version']}: {summary['sample_count']} unique readings "
                f"from {summary['observed_start'].isoformat()} to {summary['observed_end'].isoformat()}; "
                f"current resource source: {resources.source}.")
    return response.model_copy(update={"assumptions": response.assumptions + LIVE_ASSUMPTIONS + [evidence]})
