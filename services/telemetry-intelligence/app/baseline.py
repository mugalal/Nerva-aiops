from datetime import datetime, timezone
from statistics import fmean

from .config import M1Settings
from .errors import M1Error, TelemetryMissing
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

    def reject(reason: str) -> None:
        raise M1Error("baseline_quality_failed", reason, status_code=409, retryable=False)

    duration = (window.end - window.start).total_seconds()
    if duration < settings.baseline_min_duration_seconds:
        reject(f"Healthy baseline requires at least {settings.baseline_min_duration_seconds:g}s")
    if len(window.points) < settings.baseline_min_samples:
        reject(f"Healthy baseline requires at least {settings.baseline_min_samples} samples")
    timestamps = [point.timestamp for point in window.points]
    if timestamps != sorted(set(timestamps)) or any(
        (timestamp - window.start).total_seconds() < -0.001
        or (timestamp - window.end).total_seconds() > 0.001 for timestamp in timestamps
    ):
        reject("Baseline samples must be ordered, unique, and inside the requested window")
    expected = int(duration // window.step_seconds) + 1
    coverage = len(timestamps) / expected
    if (coverage < settings.baseline_min_coverage_ratio
            or (timestamps[-1] - timestamps[0]).total_seconds()
            < duration * settings.baseline_min_coverage_ratio
            or any((right - left).total_seconds() > window.step_seconds * 1.5
                   for left, right in zip(timestamps, timestamps[1:]))):
        reject("Healthy baseline has insufficient coverage or gaps")
    if any(point.metrics.request_rate < settings.baseline_min_request_rate
           or point.metrics.replica_count < 1 for point in window.points):
        reject("Healthy baseline requires running replicas and live request traffic throughout")
    if any(point.metrics.http_5xx_rate > settings.baseline_max_error_rate
           for point in window.points):
        reject("The proposed healthy baseline exceeds the allowed error rate")
    if any(point.metrics.latency_p95_ms > settings.baseline_max_latency_ms
           or point.metrics.cpu > settings.baseline_max_utilization_ratio
           or point.metrics.memory > settings.baseline_max_utilization_ratio
           for point in window.points):
        reject("The proposed healthy baseline exceeds configured latency or utilization ceilings")

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
