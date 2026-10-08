import asyncio
from datetime import datetime, timezone
import json
from typing import Protocol

from .config import M1Settings
from .errors import TelemetryMissing, TelemetryStale
from .models import TelemetryMetrics, TelemetrySnapshot, TelemetryWindow, TelemetryWindowPoint
from .providers.prometheus import PrometheusClient, PrometheusSample


class TelemetryProvider(Protocol):
    mode: str

    async def ready(self) -> tuple[bool, str]: ...

    async def snapshot(self, service: str) -> TelemetrySnapshot: ...

    async def window(
        self, service: str, start: datetime, end: datetime, step_seconds: int
    ) -> TelemetryWindow: ...


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class PrometheusTelemetryProvider:
    mode = "real"

    def __init__(self, client: PrometheusClient, settings: M1Settings):
        self.client = client
        self.settings = settings

    async def ready(self) -> tuple[bool, str]:
        return await self.client.ready()

    def queries(self, service: str) -> dict[str, str]:
        selector = f'service_name="{_escape_label(service)}"'
        rate_window = self.settings.request_rate_window
        latency_window = self.settings.latency_window
        return {
            "request_rate": (
                f'(sum(rate(nexus_http_requests_total{{{selector},route="/pay"}}'
                f'[{rate_window}])) or vector(0))'
            ),
            "latency_p95_ms": (
                "histogram_quantile(0.95, sum by (le) "
                f'(rate(nexus_http_request_duration_seconds_bucket{{{selector},route="/pay"}}'
                f'[{latency_window}]))) * 1000'
            ),
            "http_5xx_rate": (
                f'((sum(rate(nexus_http_requests_total{{{selector},route="/pay",'
                f'status_code=~"5.."}}[{latency_window}])) or vector(0)) / '
                f'clamp_min(sum(rate(nexus_http_requests_total{{{selector},route="/pay"}}'
                f'[{latency_window}])), 0.000001))'
            ),
            "cpu": (
                f'(sum(rate(process_cpu_seconds_total{{{selector}}}[{rate_window}])) '
                "or vector(0)) / "
                f'clamp_min(sum(nexus_resource_limit_cpu_cores{{{selector}}}), 0.001)'
            ),
            "memory": (
                f'sum(process_resident_memory_bytes{{{selector}}}) / '
                f'clamp_min(sum(nexus_resource_limit_memory_bytes{{{selector}}}), 1)'
            ),
            "replica_count": f'count(nexus_service_info{{{selector}}}) or vector(0)',
            "version": f'count by (version) (nexus_service_info{{{selector}}})',
        }

    def _ensure_fresh(self, samples: list[PrometheusSample]) -> None:
        now = datetime.now(timezone.utc).timestamp()
        oldest = min(sample.timestamp for sample in samples)
        age = now - oldest
        if age > self.settings.stale_after_seconds:
            raise TelemetryStale(
                f"Prometheus data is {age:.1f}s old; limit is "
                f"{self.settings.stale_after_seconds:.1f}s"
            )

    async def snapshot(self, service: str) -> TelemetrySnapshot:
        queries = self.queries(service)
        metric_names = (
            "cpu",
            "memory",
            "request_rate",
            "latency_p95_ms",
            "http_5xx_rate",
            "replica_count",
        )
        metric_results = await asyncio.gather(
            *(self.client.query_scalar(queries[name]) for name in metric_names)
        )
        version_samples = await self.client.query_vector(queries["version"])
        if not version_samples:
            raise TelemetryMissing(f"No running version was reported for {service}")

        samples = [*metric_results, *version_samples]
        self._ensure_fresh(samples)
        values = {name: sample.value for name, sample in zip(metric_names, metric_results)}
        version_sample = max(version_samples, key=lambda sample: sample.value)
        version = version_sample.labels.get("version")
        if not version:
            raise TelemetryMissing(f"Prometheus version label is missing for {service}")

        timestamp = datetime.fromtimestamp(
            min(sample.timestamp for sample in samples), tz=timezone.utc
        )
        return TelemetrySnapshot(
            timestamp=timestamp,
            service=service,
            version=version,
            metrics=TelemetryMetrics(
                cpu=max(0.0, values["cpu"]),
                memory=max(0.0, values["memory"]),
                request_rate=max(0.0, values["request_rate"]),
                latency_p95_ms=max(0.0, values["latency_p95_ms"]),
                http_5xx_rate=min(max(0.0, values["http_5xx_rate"]), 1.0),
                replica_count=max(0, int(round(values["replica_count"]))),
            ),
        )

    async def window(
        self, service: str, start: datetime, end: datetime, step_seconds: int
    ) -> TelemetryWindow:
        queries = self.queries(service)
        metric_names = (
            "cpu",
            "memory",
            "request_rate",
            "latency_p95_ms",
            "http_5xx_rate",
            "replica_count",
        )
        range_results = await asyncio.gather(
            *(
                self.client.query_range_scalar(
                    queries[name],
                    start=start.timestamp(),
                    end=end.timestamp(),
                    step_seconds=step_seconds,
                )
                for name in metric_names
            )
        )
        values_by_metric = {
            name: {round(timestamp, 3): value for timestamp, value in values}
            for name, values in zip(metric_names, range_results)
        }
        common_timestamps = set.intersection(
            *(set(values.keys()) for values in values_by_metric.values())
        )
        if not common_timestamps:
            raise TelemetryMissing("Prometheus returned no aligned historical samples")

        version_samples = await self.client.query_vector(queries["version"])
        if not version_samples:
            raise TelemetryMissing(f"No running version was reported for {service}")
        version = max(version_samples, key=lambda sample: sample.value).labels.get("version")
        if not version:
            raise TelemetryMissing(f"Prometheus version label is missing for {service}")

        points = []
        for timestamp in sorted(common_timestamps):
            values = {name: values_by_metric[name][timestamp] for name in metric_names}
            points.append(
                TelemetryWindowPoint(
                    timestamp=datetime.fromtimestamp(timestamp, tz=timezone.utc),
                    metrics=TelemetryMetrics(
                        cpu=max(0.0, values["cpu"]),
                        memory=max(0.0, values["memory"]),
                        request_rate=max(0.0, values["request_rate"]),
                        latency_p95_ms=max(0.0, values["latency_p95_ms"]),
                        http_5xx_rate=min(max(0.0, values["http_5xx_rate"]), 1.0),
                        replica_count=max(0, int(round(values["replica_count"]))),
                    ),
                )
            )
        return TelemetryWindow(
            service=service,
            version=version,
            start=start,
            end=end,
            step_seconds=step_seconds,
            points=points,
        )


class MockTelemetryProvider:
    mode = "mock"

    def __init__(self, mock_path):
        self.mock_path = mock_path

    async def ready(self) -> tuple[bool, str]:
        return self.mock_path.exists(), "canonical mock provider is active"

    def _snapshot(self, service: str, timestamp: datetime) -> TelemetrySnapshot:
        if not self.mock_path.exists():
            raise TelemetryMissing(f"Canonical mock does not exist: {self.mock_path}")
        payload = json.loads(self.mock_path.read_text(encoding="utf-8"))
        payload["service"] = service
        payload["timestamp"] = timestamp.isoformat()
        return TelemetrySnapshot.model_validate(payload)

    async def snapshot(self, service: str) -> TelemetrySnapshot:
        return self._snapshot(service, datetime.now(timezone.utc))

    async def window(
        self, service: str, start: datetime, end: datetime, step_seconds: int
    ) -> TelemetryWindow:
        snapshot = self._snapshot(service, end)
        return TelemetryWindow(
            service=service,
            version=snapshot.version,
            start=start,
            end=end,
            step_seconds=step_seconds,
            points=[TelemetryWindowPoint(timestamp=end, metrics=snapshot.metrics)],
        )
