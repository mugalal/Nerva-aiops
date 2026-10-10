"""Fetch utilization from M1 and convert units. No fabricated data, ever."""
from datetime import datetime, timedelta, timezone
import math
import re
from statistics import mean

import httpx

from .models import WindowUsage
from .resource_config import finite_number


class M1Unavailable(Exception):
    """M1 could not be reached or returned something unusable."""


SERVICE_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}"


def validate_service(service: str) -> str:
    if not isinstance(service, str) or not re.fullmatch(SERVICE_PATTERN, service):
        raise ValueError("service must be a nonempty service name without whitespace or path characters")
    return service


def parse_timestamp(value) -> datetime:
    try:
        timestamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("M1 timestamp must be an ISO 8601 date with a timezone") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("M1 timestamp must include a timezone")
    return timestamp.astimezone(timezone.utc)


def validate_snapshot(snapshot: dict, service: str, *, max_age_seconds: float = 60,
                      now: datetime | None = None) -> dict:
    if not isinstance(snapshot, dict) or snapshot.get("service") != service:
        raise ValueError("M1 telemetry service does not match the requested service")
    if not isinstance(snapshot.get("version"), str) or not snapshot["version"].strip():
        raise ValueError("M1 telemetry version is missing")
    now = now or datetime.now(timezone.utc)
    age = (now - parse_timestamp(snapshot.get("timestamp"))).total_seconds()
    if age > max_age_seconds or age < -5:
        raise ValueError("M1 telemetry is stale or dated in the future")
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("M1 telemetry metrics are missing")
    validated = {
        name: finite_number(metrics.get(name), f"M1 {name}")
        for name in ("cpu", "memory", "request_rate", "latency_p95_ms", "http_5xx_rate")
    }
    if validated["http_5xx_rate"] > 1:
        raise ValueError("M1 http_5xx_rate must be a fraction between zero and one")
    replicas = metrics.get("replica_count")
    if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas < 1:
        raise ValueError("M1 replica_count must be a positive integer")
    validated["replica_count"] = replicas
    return validated


def _get_object(base_url: str, path: str, *, params: dict, timeout: float) -> dict:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}{path}", params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("M1 response must be a JSON object")
        return payload
    except (httpx.HTTPError, ValueError) as exc:
        raise M1Unavailable(str(exc)) from exc


def fetch_window(base_url, service, start, end, step_seconds=15, timeout=5.0) -> dict:
    validate_service(service)
    payload = _get_object(base_url, "/internal/telemetry/window", params={
        "service": service, "start": start.isoformat(), "end": end.isoformat(),
        "step_seconds": step_seconds,
    }, timeout=timeout)
    validate_window(payload, expected_service=service, expected_start=start,
                    expected_end=end, expected_step=step_seconds)
    return payload


def validate_window(window: dict, *, expected_service: str | None = None,
                    expected_start: datetime | None = None, expected_end: datetime | None = None,
                    expected_step: int | None = None) -> list[datetime]:
    """Require M1's complete single-release query grid, not a claimed duration.

    M1 returns an inclusive Prometheus range evaluated every step. Comparing
    every timestamp with that grid catches missing readings, replayed readings,
    and histories belonging to another request. M1 rounds evaluations to 1 ms.
    """
    if not isinstance(window, dict):
        raise ValueError("M1 window must be a JSON object")
    service = validate_service(window.get("service"))
    if expected_service is not None and service != expected_service:
        raise ValueError("M1 window service does not match the requested service")
    version = window.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("M1 window must identify one release version")
    start, end = parse_timestamp(window.get("start")), parse_timestamp(window.get("end"))
    if end <= start:
        raise ValueError("M1 window end must be after its start")
    if end > datetime.now(timezone.utc) + timedelta(seconds=5):
        raise ValueError("M1 window cannot contain future measurements")
    if ((expected_start is not None and start != parse_timestamp(expected_start))
            or (expected_end is not None and end != parse_timestamp(expected_end))):
        raise ValueError("M1 window boundaries do not match the requested window")
    step = window.get("step_seconds")
    if not isinstance(step, int) or isinstance(step, bool) or not 5 <= step <= 300:
        raise ValueError("M1 window step_seconds must be an integer between 5 and 300")
    if expected_step is not None and step != expected_step:
        raise ValueError("M1 window step does not match the requested step")
    points = window.get("points")
    if not isinstance(points, list) or not points:
        raise ValueError("M1 returned no data points for this window")
    expected_count = math.floor((end - start).total_seconds() / step) + 1
    if len(points) != expected_count or len(points) < 2:
        raise ValueError("M1 window has incomplete sample coverage")
    if "sample_count" in window:
        count = window["sample_count"]
        if not isinstance(count, int) or isinstance(count, bool) or count != len(points):
            raise ValueError("M1 window sample_count must equal its positive number of readings")
    timestamps = []
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            raise ValueError("M1 window has a malformed reading")
        if point.get("service", service) != service or point.get("version", version) != version:
            raise ValueError("M1 window contains mixed service or release identities")
        timestamp = parse_timestamp(point.get("timestamp"))
        if timestamps and timestamp <= timestamps[-1]:
            raise ValueError("M1 window timestamps must be unique and strictly increasing")
        offset = (timestamp - start).total_seconds()
        if offset < -0.001 or timestamp > end + timedelta(milliseconds=1):
            raise ValueError("M1 window contains a reading outside its requested range")
        if abs(offset - index * step) > 0.001:
            raise ValueError("M1 window has sparse or misaligned sample coverage")
        timestamps.append(timestamp)
    return timestamps


def summarize_window(window: dict, **expected) -> dict:
    """Average/peak only from a validated grid; expose actual measured coverage."""
    timestamps = validate_window(window, **expected)
    points = window["points"]
    try:
        cpu = [finite_number(p["metrics"]["cpu"], "M1 window cpu") for p in points]
        memory = [finite_number(p["metrics"]["memory"], "M1 window memory") for p in points]
    except (KeyError, TypeError) as exc:
        raise ValueError("M1 window has missing or malformed metrics") from exc
    return {
        "avg_cpu_frac": mean(cpu),
        "peak_cpu_frac": max(cpu),
        "avg_memory_frac": mean(memory),
        "peak_memory_frac": max(memory),
        "sample_count": len(points),
        "observed_start": timestamps[0],
        "observed_end": timestamps[-1],
        "version": window["version"],
    }


def to_pct_of_request(fraction: float, limit: float, request: float) -> float:
    """M1 reports usage / LIMIT. Convert to percent of the REQUEST."""
    fraction = finite_number(fraction, "utilization fraction")
    limit = finite_number(limit, "resource limit", positive=True)
    request = finite_number(request, "resource request", positive=True)
    if request > limit:
        raise ValueError("Resource request must not exceed its limit")
    return finite_number(fraction * (limit / request) * 100, "utilization percent")


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

def fetch_snapshot(base_url, service, timeout=5.0) -> dict:
    validate_service(service)
    return _get_object(base_url, "/internal/telemetry/snapshot", params={"service": service}, timeout=timeout)


def fetch_preview(base_url, service, timeout=5.0) -> dict:
    validate_service(service)
    return _get_object(base_url, "/internal/evidence/preview", params={"service": service}, timeout=timeout)
