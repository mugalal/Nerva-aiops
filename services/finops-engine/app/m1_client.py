"""Fetch utilization from M1 and convert units. No fabricated data, ever."""
from statistics import mean

import httpx

from .models import WindowUsage


class M1Unavailable(Exception):
    """M1 could not be reached or returned something unusable."""


def fetch_window(base_url, service, start, end, step_seconds=15, timeout=5.0) -> dict:
    try:
        response = httpx.get(
            f"{base_url}/internal/telemetry/window",
            params={
                "service": service,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "step_seconds": step_seconds,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise M1Unavailable(str(exc)) from exc


def summarize_window(window: dict) -> dict:
    """Average and peak of M1's cpu/memory fractions over the window's points."""
    points = window.get("points") or []
    if not points:
        raise ValueError("M1 returned no data points for this window")
    cpu = [p["metrics"]["cpu"] for p in points]
    memory = [p["metrics"]["memory"] for p in points]
    return {
        "avg_cpu_frac": mean(cpu),
        "peak_cpu_frac": max(cpu),
        "avg_memory_frac": mean(memory),
        "peak_memory_frac": max(memory),
        "sample_count": len(points),
    }


def to_pct_of_request(fraction: float, limit: float, request: float) -> float:
    """M1 reports usage / LIMIT. Convert to percent of the REQUEST."""
    return fraction * limit / request * 100


def build_window_usage(summary, cpu_limit_m, memory_limit_mb,
                       cpu_request_m, memory_request_mb) -> WindowUsage:
    def pct(frac, limit, request):
        # Clamped to 100: anything >= 90% gives the same outcome (NO_RECOMMENDATION).
        return round(min(100.0, to_pct_of_request(frac, limit, request)), 2)

    return WindowUsage(
        avg_cpu_pct=pct(summary["avg_cpu_frac"], cpu_limit_m, cpu_request_m),
        peak_cpu_pct=pct(summary["peak_cpu_frac"], cpu_limit_m, cpu_request_m),
        avg_memory_pct=pct(summary["avg_memory_frac"], memory_limit_mb, memory_request_mb),
        peak_memory_pct=pct(summary["peak_memory_frac"], memory_limit_mb, memory_request_mb),
    )