from datetime import datetime, timezone
from statistics import fmean

from .config import M1Settings
from .errors import TelemetryMissing
from .models import (
    BaselineThresholds,
    HealthyBaseline,
    MetricStatistics,
    TelemetryWindow,
)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise TelemetryMissing("Cannot calculate a baseline from an empty telemetry window")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _statistics(values: list[float]) -> MetricStatistics:
    return MetricStatistics(
        minimum=min(values),
        maximum=max(values),
        average=fmean(values),
        p95=_percentile(values, 0.95),
    )


def build_healthy_baseline(window: TelemetryWindow, settings: M1Settings) -> HealthyBaseline:
    if not window.points:
        raise TelemetryMissing("The healthy measurement window contains no aligned samples")

    fields = {
        name: [float(getattr(point.metrics, name)) for point in window.points]
        for name in (
            "request_rate",
            "latency_p95_ms",
            "http_5xx_rate",
            "cpu",
            "memory",
            "replica_count",
        )
    }
    stats = {name: _statistics(values) for name, values in fields.items()}
    latency_limit = stats["latency_p95_ms"].p95 * settings.baseline_latency_multiplier
    error_limit = max(
        settings.baseline_error_floor,
        stats["http_5xx_rate"].p95 * settings.baseline_error_multiplier,
    )

    return HealthyBaseline(
        service=window.service,
        version=window.version,
        measured_at=datetime.now(timezone.utc),
        window_start=window.start,
        window_end=window.end,
        sample_count=len(window.points),
        request_rate=stats["request_rate"],
        latency_p95_ms=stats["latency_p95_ms"],
        http_5xx_rate=stats["http_5xx_rate"],
        cpu=stats["cpu"],
        memory=stats["memory"],
        replica_count=stats["replica_count"],
        thresholds=BaselineThresholds(
            latency_p95_ms_max=latency_limit,
            http_5xx_rate_max=min(error_limit, 1.0),
        ),
    )
