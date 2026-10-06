from datetime import datetime, timedelta, timezone
import time
import unittest

from app.providers.prometheus import PrometheusSample
from app.telemetry import PrometheusTelemetryProvider

from tests.m1.support import make_settings


class FakePrometheusClient:
    async def ready(self) -> tuple[bool, str]:
        return True, "ready"

    @staticmethod
    def _value(query: str) -> float:
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

    async def query_scalar(self, query: str) -> PrometheusSample:
        return PrometheusSample(labels={}, timestamp=time.time(), value=self._value(query))

    async def query_vector(self, query: str) -> list[PrometheusSample]:
        return [
            PrometheusSample(
                labels={"version": "v2"},
                timestamp=time.time(),
                value=2,
            )
        ]

    async def query_range_scalar(
        self,
        query: str,
        *,
        start: float,
        end: float,
        step_seconds: int,
    ) -> list[tuple[float, float]]:
        value = self._value(query)
        return [(start, value), (end, value)]


class PrometheusTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_normalizes_real_prometheus_values(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            provider = PrometheusTelemetryProvider(
                FakePrometheusClient(),
                make_settings(Path(directory)),
            )
            snapshot = await provider.snapshot("payment-service")

        self.assertEqual(snapshot.version, "v2")
        self.assertEqual(snapshot.metrics.request_rate, 35)
        self.assertEqual(snapshot.metrics.latency_p95_ms, 820)
        self.assertEqual(snapshot.metrics.http_5xx_rate, 0.14)
        self.assertEqual(snapshot.metrics.replica_count, 2)

    async def test_historical_window_aligns_metric_timestamps(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        end = datetime.now(timezone.utc)
        with TemporaryDirectory() as directory:
            provider = PrometheusTelemetryProvider(
                FakePrometheusClient(),
                make_settings(Path(directory)),
            )
            window = await provider.window("payment-service", start, end, 15)

        self.assertEqual(len(window.points), 2)
        self.assertEqual(window.version, "v2")
        self.assertEqual(window.points[0].metrics.cpu, 0.65)


if __name__ == "__main__":
    unittest.main()
