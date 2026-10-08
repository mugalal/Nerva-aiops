"""Transparent cost model. Cost units (CU) are abstract, NOT real money."""

CPU_RATE_CU_PER_CORE_HOUR = 1.0
MEMORY_RATE_CU_PER_GB_HOUR = 0.25
HOURS_PER_MONTH = 730

ASSUMPTIONS = [
    "Cost is in abstract cost units (CU), not real currency; no provider pricing is used.",
    f"CPU rate: {CPU_RATE_CU_PER_CORE_HOUR} CU per core-hour of REQUESTED CPU.",
    f"Memory rate: {MEMORY_RATE_CU_PER_GB_HOUR} CU per GB-hour of REQUESTED memory (1 GB = 1024 MB).",
    "We charge for requested (reserved) resources, not for what is actually used.",
    f"Cost per hour = replicas x (cpu_m/1000 x cpu_rate + memory_mb/1024 x memory_rate); month = {HOURS_PER_MONTH} h.",
]


def cost_per_hour(replicas: int, cpu_request_m: int, memory_request_mb: int) -> float:
    per_pod = (cpu_request_m / 1000) * CPU_RATE_CU_PER_CORE_HOUR \
        + (memory_request_mb / 1024) * MEMORY_RATE_CU_PER_GB_HOUR
    return replicas * per_pod


def saving_pct(current_cost: float, new_cost: float) -> float:
    if current_cost <= 0:
        return 0.0
    return round((current_cost - new_cost) / current_cost * 100, 2)


def scale_cost_delta(current_cost: float, new_cost: float, duration_minutes: int) -> float:
    return round((new_cost - current_cost) * duration_minutes / 60, 4)