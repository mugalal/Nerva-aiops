from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from app.errors import ProviderInvalidResponse, TelemetryMissing, TelemetryStale
from app.providers.prometheus import PrometheusClient, PrometheusSample, PrometheusSeries
from app.telemetry import PrometheusTelemetryProvider
from tests.m1.support import make_settings


class FakePrometheusClient:
    def __init__(self):
        self.source_age = 5
        self.scrape_up = 1
        self.current_version = "v2"
        self.historical_versions = ["v1"]
        self.interval_versions = None
        self.missing_metric = None
        self.history_gap = False
        self.historical_stale_point = False
        self.unknown_version_point = False
        self.version_evaluation_times = []
        self.version_queries = []
        self.scalar_evaluation_times = []
        self.latency_changes_with_time = False

    async def ready(self):
        return True, "ready"

    @staticmethod
    def _value(query):
        if "histogram_quantile" in query:
            return 820
        if 'status_code=~"5.."' in query:
            return 0.14
        if "process_cpu_seconds_total" in query:
            return 0.65
        if "process_resident_memory_bytes" in query:
            return 0.42
        if "count(nexus_service_info" in query:
            return 2
        if "nexus_http_requests_total" in query:
            return 35
        raise AssertionError(f"Unexpected PromQL: {query}")

    def _sample_value(self, query, evaluation_time):
        if "timestamp(" in query:
            return evaluation_time - self.source_age
        if query.startswith("min(up{"):
            return self.scrape_up
        if self.latency_changes_with_time and "histogram_quantile" in query:
            return 10 + evaluation_time % 60
        if self.missing_metric and self.missing_metric in query:
            # Reproduce PromQL's zero fallback concealing an absent series.
            if "or vector(0)" in query:
                return 0
            raise TelemetryMissing("Required series is absent")
        return self._value(query)

    async def query_scalar(self, query, *, evaluation_time=None):
        self.scalar_evaluation_times.append(evaluation_time)
        return PrometheusSample(
            labels={}, timestamp=evaluation_time,
            value=self._sample_value(query, evaluation_time),
        )

    async def query_vector(self, query, *, evaluation_time=None):
        self.version_evaluation_times.append(evaluation_time)
        self.version_queries.append(query)
        versions = (
            (self.interval_versions or self.historical_versions)
            if "count_over_time" in query else [self.current_version]
        )
        return [
            PrometheusSample(labels={"version": version}, timestamp=evaluation_time, value=2)
            for version in versions
        ]

    @staticmethod
    def _times(start, end, step_seconds):
        return [start + index * step_seconds for index in range(math.floor((end - start) / step_seconds) + 1)]

    async def query_range_scalar(self, query, *, start, end, step_seconds):
        times = self._times(start, end, step_seconds)
        values = [(timestamp, self._sample_value(query, timestamp)) for timestamp in times]
        if self.history_gap and "histogram_quantile" in query:
            values.pop(1)
        if self.historical_stale_point and "timestamp(" in query:
            values[1] = (times[1], times[1] - 90)
        return values

    async def query_range(self, query, *, start, end, step_seconds):
        times = self._times(start, end, step_seconds)
        if self.unknown_version_point:
            times.pop(1)
        return [
            PrometheusSeries(labels={"version": version}, values=[(timestamp, 2) for timestamp in times])
            for version in self.historical_versions
        ]


class PrometheusTelemetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client = FakePrometheusClient()
        self.provider = PrometheusTelemetryProvider(self.client, make_settings(Path(self.directory.name)))
        self.end = datetime.now(timezone.utc)
        self.start = self.end - timedelta(minutes=1)

    async def window(self):
        return await self.provider.window("payment-service", self.start, self.end, 15)

    async def test_snapshot_reports_the_shared_metric_evaluation_time(self):
        snapshot = await self.provider.snapshot("payment-service")
        self.assertEqual(snapshot.version, "v2")
        self.assertEqual(snapshot.metrics.request_rate, 35)
        self.assertEqual(snapshot.metrics.latency_p95_ms, 820)
        self.assertEqual(snapshot.metrics.http_5xx_rate, 0.14)
        self.assertEqual(snapshot.metrics.replica_count, 2)
        self.assertEqual(snapshot.timestamp.timestamp(), self.client.version_evaluation_times[-1])
        self.assertEqual(set(self.client.scalar_evaluation_times), {snapshot.timestamp.timestamp()})

    async def test_snapshot_and_history_agree_at_same_timestamp_with_delayed_scrapes(self):
        self.client.source_age = 15
        self.client.latency_changes_with_time = True
        self.client.historical_versions = [self.client.current_version]
        snapshot = await self.provider.snapshot("payment-service")
        window = await self.provider.window("payment-service", snapshot.timestamp - timedelta(seconds=30),
                                            snapshot.timestamp, 15)
        self.assertEqual(snapshot.timestamp, window.points[-1].timestamp)
        self.assertEqual(snapshot.metrics, window.points[-1].metrics)
        self.assertEqual(snapshot.version, window.version)

    async def test_snapshot_rejects_stale_raw_scrapes_despite_fresh_query_results(self):
        self.client.source_age = 90
        with self.assertRaises(TelemetryStale):
            await self.provider.snapshot("payment-service")

    async def test_snapshot_rejects_future_source_scrapes(self):
        self.client.source_age = -30
        with self.assertRaises(TelemetryStale):
            await self.provider.snapshot("payment-service")

    async def test_snapshot_rejects_unknown_version(self):
        self.client.current_version = ""
        with self.assertRaisesRegex(TelemetryMissing, "version label"):
            await self.provider.snapshot("payment-service")

    async def test_snapshot_rejects_down_scrape_target_with_retained_metrics(self):
        self.client.scrape_up = 0
        with self.assertRaises(TelemetryMissing):
            await self.provider.snapshot("payment-service")

    async def test_missing_cpu_and_requests_are_not_silently_zero(self):
        for metric in ("process_cpu_seconds_total", "nexus_http_requests_total"):
            with self.subTest(metric=metric):
                self.client.missing_metric = metric
                with self.assertRaises(TelemetryMissing):
                    await self.provider.snapshot("payment-service")

    async def test_historical_window_uses_historical_version_after_a_deployment(self):
        window = await self.window()
        self.assertEqual(len(window.points), 5)
        self.assertEqual(window.version, "v1")
        self.assertEqual(window.points[0].metrics.cpu, 0.65)
        self.assertEqual(self.client.version_evaluation_times, [self.end.timestamp()])
        self.assertIn("[121s]", self.client.version_queries[-1])

    async def test_historical_window_rejects_concurrent_versions(self):
        self.client.historical_versions = ["v1", "v2"]
        with self.assertRaisesRegex(TelemetryMissing, "mixed versions"):
            await self.window()

    async def test_historical_window_rejects_brief_version_change_between_steps(self):
        self.client.interval_versions = ["v1", "v2"]
        with self.assertRaisesRegex(TelemetryMissing, "mixed versions"):
            await self.window()

    async def test_historical_window_rejects_unknown_version_at_a_point(self):
        self.client.unknown_version_point = True
        with self.assertRaisesRegex(TelemetryMissing, "version label"):
            await self.window()

    async def test_historical_window_rejects_partial_metric_coverage(self):
        self.client.history_gap = True
        with self.assertRaisesRegex(TelemetryMissing, "incomplete or misaligned"):
            await self.window()

    async def test_historical_freshness_is_relative_to_each_historical_point(self):
        self.start -= timedelta(days=1)
        self.end -= timedelta(days=1)
        window = await self.window()
        self.assertEqual(len(window.points), 5)
        self.client.historical_stale_point = True
        with self.assertRaises(TelemetryStale):
            await self.window()

    async def test_historical_window_rejects_down_scrapes(self):
        self.client.scrape_up = 0
        with self.assertRaises(TelemetryMissing):
            await self.window()


class StaticPrometheusClient(PrometheusClient):
    def __init__(self, payload):
        super().__init__("http://prometheus")
        self.payload = payload

    async def _get(self, path, params=None):
        return httpx.Response(200, json=self.payload)


class PrometheusResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_source_timestamp_is_a_provider_error(self):
        client = StaticPrometheusClient({
            "status": "success", "data": {"resultType": "vector", "result": [
                {"metric": {}, "value": ["NaN", "1"]}
            ]},
        })
        with self.assertRaises(ProviderInvalidResponse):
            await client.query_scalar("up")

    async def test_nonfinite_required_metric_is_missing_data(self):
        client = StaticPrometheusClient({
            "status": "success", "data": {"resultType": "vector", "result": [
                {"metric": {}, "value": [10, "NaN"]}
            ]},
        })
        with self.assertRaises(TelemetryMissing):
            await client.query_scalar("latency")

    async def test_empty_aggregate_range_is_missing_data(self):
        client = StaticPrometheusClient({
            "status": "success", "data": {"resultType": "matrix", "result": [
                {"metric": {}, "values": []}
            ]},
        })
        with self.assertRaises(TelemetryMissing):
            await client.query_range_scalar("latency", start=0, end=60, step_seconds=15)


if __name__ == "__main__":
    unittest.main()
