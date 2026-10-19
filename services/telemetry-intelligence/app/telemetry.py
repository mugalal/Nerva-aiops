import asyncio
from datetime import datetime, timezone
import json
import math
from typing import Protocol

from .config import M1Settings, duration_seconds
from .errors import TelemetryMissing, TelemetryStale
from .models import CpuThrottling, TelemetryMetrics, TelemetrySnapshot, TelemetryWindow, TelemetryWindowPoint
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
    metric_names = (
        "cpu", "memory", "request_rate", "latency_p95_ms", "http_5xx_rate", "replica_count"
    )

    def __init__(self, client: PrometheusClient, settings: M1Settings):
        self.client = client
        self.settings = settings

    async def ready(self) -> tuple[bool, str]:
        return await self.client.ready()

    def route(self, service: str) -> str:
        route = self.settings.service_routes.get(service)
        if not route:
            raise TelemetryMissing(f"No business route is configured for {service}; set M1_SERVICE_ROUTES")
        return _escape_label(route)

    def queries(self, service: str) -> dict[str, str]:
        selector = f'service_name="{_escape_label(service)}"'
        route = self.route(service)
        rate_window = self.settings.request_rate_window
        latency_window = self.settings.latency_window
        return {
            "request_rate": (
                f'sum(rate(nexus_http_requests_total{{{selector},route="{route}"}}'
                f'[{rate_window}]))'
            ),
            "latency_p95_ms": (
                "histogram_quantile(0.95, sum by (le) "
                f'(rate(nexus_http_request_duration_seconds_bucket{{{selector},route="{route}"}}'
                f'[{latency_window}]))) * 1000'
            ),
            "http_5xx_rate": (
                f'((sum(rate(nexus_http_requests_total{{{selector},route="{route}",'
                f'status_code=~"5.."}}[{latency_window}])) or vector(0)) / '
                f'clamp_min(sum(rate(nexus_http_requests_total{{{selector},route="{route}"}}'
                f'[{latency_window}])), 0.000001))'
            ),
            "cpu": (
                f'sum(rate(process_cpu_seconds_total{{{selector}}}[{rate_window}])) / '
                f'clamp_min(sum(nexus_resource_limit_cpu_cores{{{selector}}}), 0.001)'
            ),
            "memory": (
                f'sum(process_resident_memory_bytes{{{selector}}}) / '
                f'clamp_min(sum(nexus_resource_limit_memory_bytes{{{selector}}}), 1)'
            ),
            "replica_count": f'count(nexus_service_info{{{selector}}})',
            "version": f'count by (version) (nexus_service_info{{{selector}}})',
        }

    async def cpu_throttling(self, service: str) -> CpuThrottling:
        """Throttled share of CFS periods over the rate window; TelemetryMissing without a CPU limit."""
        selector = f'service_name="{_escape_label(service)}"'
        window = self.settings.request_rate_window
        periods = await self.client.query_scalar(
            f'sum(rate(nexus_cpu_cfs_periods_total{{{selector}}}[{window}]))')
        if periods.value <= 0:
            raise TelemetryMissing(f"{service} has no enforced CFS periods (no CPU limit)")
        throttled = await self.client.query_scalar(
            f'sum(rate(nexus_cpu_cfs_throttled_periods_total{{{selector}}}[{window}]))')
        seconds = await self.client.query_scalar(
            f'sum(rate(nexus_cpu_cfs_throttled_seconds_total{{{selector}}}[{window}]))')
        return CpuThrottling(throttled_period_ratio=min(1.0, max(0.0, throttled.value / periods.value)),
                             throttled_seconds_per_second=max(0.0, seconds.value))

    def source_queries(self, service: str) -> dict[str, str]:
        selector = f'service_name="{_escape_label(service)}"'
        route = self.route(service)
        # Aggregate-result timestamps are evaluation times. timestamp(raw_selector)
        # exposes the actual scrape time as the sample VALUE, even in range queries.
        sources = {
            "requests": f'nexus_http_requests_total{{{selector},route="{route}"}}',
            "latency": f'nexus_http_request_duration_seconds_bucket{{{selector},route="{route}"}}',
            "cpu": f'process_cpu_seconds_total{{{selector}}}',
            "memory": f'process_resident_memory_bytes{{{selector}}}',
            "cpu_limit": f'nexus_resource_limit_cpu_cores{{{selector}}}',
            "memory_limit": f'nexus_resource_limit_memory_bytes{{{selector}}}',
            "identity": f'nexus_service_info{{{selector}}}',
            "up": f'up{{{selector}}}',
        }
        return {
            **{f"source_{name}": f"min(timestamp({query}))" for name, query in sources.items()},
            "scrape_up": f'min(up{{{selector}}})',
        }

    def _ensure_sources(self, values: dict[str, float], evaluation_time: float) -> float:
        if values["scrape_up"] != 1:
            raise TelemetryMissing("One or more Prometheus service scrape targets are down")
        oldest = min(value for name, value in values.items() if name.startswith("source_"))
        newest = max(value for name, value in values.items() if name.startswith("source_"))
        age = evaluation_time - oldest
        if age > self.settings.stale_after_seconds or newest > evaluation_time + 0.001:
            raise TelemetryStale(
                f"Prometheus source scrape age is {age:.1f}s; limit is "
                f"{self.settings.stale_after_seconds:.1f}s"
            )
        return oldest

    @staticmethod
    def _version(samples: list[PrometheusSample], service: str) -> str:
        active = [sample for sample in samples if sample.value > 0]
        if not active or any(not sample.labels.get("version") for sample in active):
            raise TelemetryMissing(f"Prometheus version label is missing for {service}")
        versions = {sample.labels["version"] for sample in active}
        if len(versions) != 1:
            raise TelemetryMissing(f"Prometheus telemetry contains mixed versions for {service}")
        return versions.pop()

    @staticmethod
    def _metrics(values: dict[str, float]) -> TelemetryMetrics:
        if values["replica_count"] < 1:
            raise TelemetryMissing("Prometheus reported no running service replicas")
        return TelemetryMetrics(
            cpu=max(0.0, values["cpu"]),
            memory=max(0.0, values["memory"]),
            request_rate=max(0.0, values["request_rate"]),
            latency_p95_ms=max(0.0, values["latency_p95_ms"]),
            http_5xx_rate=min(max(0.0, values["http_5xx_rate"]), 1.0),
            replica_count=int(round(values["replica_count"])),
        )

    async def snapshot(self, service: str) -> TelemetrySnapshot:
        queries = self.queries(service)
        scalar_queries = {**{name: queries[name] for name in self.metric_names}, **self.source_queries(service)}
        # Prometheus evaluates these expressions at one millisecond-resolution
        # instant; raw scrape timestamps are only the independent freshness gate.
        evaluation_time = math.floor(datetime.now(timezone.utc).timestamp() * 1000) / 1000
        results = await asyncio.gather(
            *(self.client.query_scalar(query, evaluation_time=evaluation_time) for query in scalar_queries.values()),
            self.client.query_vector(queries["version"], evaluation_time=evaluation_time),
        )
        values = {name: sample.value for name, sample in zip(scalar_queries, results[:-1])}
        self._ensure_sources(values, evaluation_time)
        version = self._version(results[-1], service)
        return TelemetrySnapshot(
            timestamp=datetime.fromtimestamp(evaluation_time, tz=timezone.utc),
            service=service,
            version=version,
            metrics=self._metrics(values),
        )

    async def window(
        self, service: str, start: datetime, end: datetime, step_seconds: int
    ) -> TelemetryWindow:
        queries = self.queries(service)
        scalar_queries = {**{name: queries[name] for name in self.metric_names}, **self.source_queries(service)}
        range_params = {"start": start.timestamp(), "end": end.timestamp(), "step_seconds": step_seconds}
        range_results = await asyncio.gather(
            *(self.client.query_range_scalar(query, **range_params) for query in scalar_queries.values()),
            self.client.query_range(queries["version"], **range_params),
        )
        values_by_metric = {
            name: {round(timestamp, 3): value for timestamp, value in values}
            for name, values in zip(scalar_queries, range_results[:-1])
        }
        expected = {
            round(start.timestamp() + index * step_seconds, 3)
            for index in range(math.floor((end - start).total_seconds() / step_seconds) + 1)
        }
        if not expected or any(set(values) != expected for values in values_by_metric.values()):
            raise TelemetryMissing("Prometheus returned incomplete or misaligned historical samples")

        version_series = range_results[-1]
        versions_by_timestamp = {timestamp: [] for timestamp in expected}
        for series in version_series:
            for timestamp, value in series.values:
                rounded = round(timestamp, 3)
                if rounded in versions_by_timestamp:
                    versions_by_timestamp[rounded].append(PrometheusSample(series.labels, timestamp, value))
        versions = {self._version(samples, service) for samples in versions_by_timestamp.values()}
        if len(versions) != 1:
            raise TelemetryMissing(f"Historical window contains mixed versions for {service}")
        version = versions.pop()
        # Include metric lookback as rate/histogram queries at the first point can
        # still contain a prior release. Also catch rollouts between query steps.
        selector = f'service_name="{_escape_label(service)}"'
        lookback_seconds = max(
            duration_seconds(self.settings.request_rate_window),
            duration_seconds(self.settings.latency_window),
        )
        span_seconds = max(1, math.ceil((end - start).total_seconds() + lookback_seconds) + 1)
        interval_versions = await self.client.query_vector(
            f'count by (version) (count_over_time(nexus_service_info{{{selector}}}[{span_seconds}s]))',
            evaluation_time=end.timestamp(),
        )
        if self._version(interval_versions, service) != version:
            raise TelemetryMissing(f"Historical window version is inconsistent for {service}")

        points = []
        for timestamp in sorted(expected):
            values = {name: values_by_metric[name][timestamp] for name in scalar_queries}
            self._ensure_sources(values, timestamp)
            points.append(
                TelemetryWindowPoint(
                    timestamp=datetime.fromtimestamp(timestamp, tz=timezone.utc),
                    metrics=self._metrics(values),
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
