from . import cost_model
import math
from .models import FinOpsContext, ScaleOption, ScaleOptionsRequest
from .resource_config import finite_number


def risk_for(projected_cpu_pct: float) -> str:
    if projected_cpu_pct <= 50:
        return "LOW"
    if projected_cpu_pct <= 75:
        return "MEDIUM"
    return "HIGH"


def build_scale_options(req: ScaleOptionsRequest, *, observed_cpu_pct: float | None = None) -> FinOpsContext:
    current = req.current_replicas
    observed = finite_number(req.observed_cpu_pct if observed_cpu_pct is None else observed_cpu_pct,
                             "observed CPU percent")
    candidates = req.candidate_replicas or [current + 1, current * 2]
    if req.candidate_replicas is None and not any(observed * (current / n) <= 50 for n in candidates):
        # Add enough capacity to offer real headroom when a fixed doubling is
        # insufficient. M4 still enforces its replica cap and risk policy.
        candidates += [math.ceil((observed / target) * current) for target in (70, 50)]
    candidates = sorted({c for c in candidates if c > current})
    base = cost_model.cost_per_hour(current, req.cpu_request_m, req.memory_request_mb)

    options = []
    for n in candidates:
        # The public contract caps observed_cpu_pct at 100. Keep actual overload
        # for risk calculation so a heavily overloaded service cannot look safe.
        projected = observed * (current / n)  # assumes load spreads evenly
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
