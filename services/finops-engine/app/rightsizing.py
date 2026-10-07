import math

from . import cost_model
from .models import FinOpsRecommendation, ObservedUsage, RecommendRequest, RecommendResponse, ResourceSpec

MIN_WINDOW_MINUTES = 60
MIN_SAMPLES = 10
EXTREME_PEAK_PCT = 90
TARGET_PEAK_PCT = 65
MIN_CPU_M = 50
MIN_MEMORY_MB = 64
CPU_STEP_M = 10
MEMORY_STEP_MB = 16
MIN_SAVING_PCT = 5.0
SHORT_WINDOW_MINUTES = 360

RULE_ASSUMPTIONS = [
    "Utilization inputs are percent of the REQUEST (not the limit).",
    f"Sizing uses the PEAK, aiming for peak <= {TARGET_PEAK_PCT}% of the new request.",
    f"Safety floors: CPU >= {MIN_CPU_M}m, memory >= {MIN_MEMORY_MB} MB; replicas are never changed; resources never increase.",
    f"Insufficient evidence if window < {MIN_WINDOW_MINUTES} min or fewer than {MIN_SAMPLES} samples.",
    f"No recommendation if any peak >= {EXTREME_PEAK_PCT}% or the saving is below {MIN_SAVING_PCT}%.",
    f"Risk is raised when the window is shorter than {SHORT_WINDOW_MINUTES} min.",
    "Nothing is applied automatically; M4 owns any action.",
]


def _round_up(value: float, step: int) -> int:
    return int(math.ceil(value / step) * step)


def _result(req, status, reason, recommended, saving, risk) -> RecommendResponse:
    return RecommendResponse(
        status=status,
        reason=reason,
        assumptions=cost_model.ASSUMPTIONS + RULE_ASSUMPTIONS,
        recommendation=FinOpsRecommendation(
            service=req.service,
            current=req.current,
            observed=ObservedUsage(
                avg_cpu_pct=req.observed.avg_cpu_pct,
                avg_memory_pct=req.observed.avg_memory_pct,
            ),
            recommended=recommended,
            estimated_monthly_saving_pct=saving,
            reliability_risk=risk,
        ),
    )


def recommend(req: RecommendRequest) -> RecommendResponse:
    cur = req.current
    obs = req.observed
    minutes = (req.window.end - req.window.start).total_seconds() / 60

    if minutes < MIN_WINDOW_MINUTES:
        return _result(req, "INSUFFICIENT_EVIDENCE",
                       f"Measurement window is {minutes:.0f} min; at least {MIN_WINDOW_MINUTES} required.",
                       cur, 0.0, "MEDIUM")
    if req.window.sample_count is not None and req.window.sample_count < MIN_SAMPLES:
        return _result(req, "INSUFFICIENT_EVIDENCE",
                       f"Only {req.window.sample_count} samples; at least {MIN_SAMPLES} required.",
                       cur, 0.0, "MEDIUM")
    if obs.peak_cpu_pct >= EXTREME_PEAK_PCT or obs.peak_memory_pct >= EXTREME_PEAK_PCT:
        return _result(req, "NO_RECOMMENDATION",
                       "Peak utilization is extreme; the current size is already tight, not oversized.",
                       cur, 0.0, "HIGH")

    new_cpu = max(MIN_CPU_M, _round_up(cur.cpu_request_m * obs.peak_cpu_pct / TARGET_PEAK_PCT, CPU_STEP_M))
    new_mem = max(MIN_MEMORY_MB, _round_up(cur.memory_request_mb * obs.peak_memory_pct / TARGET_PEAK_PCT, MEMORY_STEP_MB))
    new_cpu = min(new_cpu, cur.cpu_request_m)
    new_mem = min(new_mem, cur.memory_request_mb)
    proposed = ResourceSpec(replicas=cur.replicas, cpu_request_m=new_cpu, memory_request_mb=new_mem)

    saving = cost_model.saving_pct(
        cost_model.cost_per_hour(cur.replicas, cur.cpu_request_m, cur.memory_request_mb),
        cost_model.cost_per_hour(proposed.replicas, new_cpu, new_mem),
    )
    if saving < MIN_SAVING_PCT:
        return _result(req, "NO_RECOMMENDATION",
                       f"Possible saving is {saving}%, below the {MIN_SAVING_PCT}% threshold.",
                       cur, 0.0, "LOW")

    projected = max(obs.peak_cpu_pct * cur.cpu_request_m / new_cpu,
                    obs.peak_memory_pct * cur.memory_request_mb / new_mem)
    if projected > 80:
        risk = "HIGH"
    elif projected > TARGET_PEAK_PCT or minutes < SHORT_WINDOW_MINUTES:
        risk = "MEDIUM"
    else:
        risk = "LOW"

    return _result(req, "RECOMMENDED",
                   f"Peak CPU {obs.peak_cpu_pct}% and memory {obs.peak_memory_pct}% of request over "
                   f"{minutes:.0f} min. Projected peak after change: {projected:.0f}%.",
                   proposed, saving, risk)