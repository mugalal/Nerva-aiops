from . import cost_model
from .models import FinOpsContext, ScaleOption, ScaleOptionsRequest


def risk_for(projected_cpu_pct: float) -> str:
    if projected_cpu_pct <= 50:
        return "LOW"
    if projected_cpu_pct <= 75:
        return "MEDIUM"
    return "HIGH"


def build_scale_options(req: ScaleOptionsRequest) -> FinOpsContext:
    current = req.current_replicas
    candidates = req.candidate_replicas or [current + 1, current * 2]
    candidates = sorted({c for c in candidates if c > current})
    base = cost_model.cost_per_hour(current, req.cpu_request_m, req.memory_request_mb)

    options = []
    for n in candidates:
        projected = req.observed_cpu_pct * current / n  # assumes load spreads evenly
        new_cost = cost_model.cost_per_hour(n, req.cpu_request_m, req.memory_request_mb)
        options.append(ScaleOption(
            replicas=n,
            estimated_cost_delta=cost_model.scale_cost_delta(base, new_cost, req.scale_duration_minutes),
            risk=risk_for(projected),
        ))

    return FinOpsContext(
        service=req.service,
        current_replicas=current,
        current_cpu_request_m=req.cpu_request_m,
        observed_cpu_pct=req.observed_cpu_pct,
        temporary_scale_options=options,
    )