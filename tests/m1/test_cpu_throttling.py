"""CFS throttling: exported by the service from its cgroup, measured by M1, cited by M3."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.errors import TelemetryMissing
from app.telemetry import PrometheusTelemetryProvider
from tests.m1.support import make_settings
from tests.m1.test_payment_probe_concurrency import load_payment
from tests.m1.test_prometheus_telemetry import FakePrometheusClient


class CgroupReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.payment = load_payment()

    def stat(self, name, text):
        path = Path(self.directory.name) / name
        path.write_text(text, encoding="ascii")
        return str(path)

    def test_cgroup_v2_and_v1_report_periods_and_throttled_time(self):
        v2 = self.stat("v2", "usage_usec 9\nnr_periods 200\nnr_throttled 50\nthrottled_usec 2500000\n")
        v1 = self.stat("v1", "nr_periods 10\nnr_throttled 4\nthrottled_time 3000000000\n")
        self.assertEqual(self.payment.read_cpu_throttling([(v2, "throttled_usec", 1e-6)]), (200, 50, 2.5))
        self.assertEqual(self.payment.read_cpu_throttling([(v1, "throttled_time", 1e-9)]), (10, 4, 3.0))

    def test_missing_or_malformed_cgroup_exports_nothing(self):
        broken = self.stat("broken", "nr_periods x\n")
        self.assertIsNone(self.payment.read_cpu_throttling([(broken, "throttled_usec", 1e-6),
                                                            ("/no/such/cpu.stat", "throttled_usec", 1e-6)]))
        self.payment.CGROUP_CPU_STAT = (("/no/such/cpu.stat", "throttled_usec", 1e-6),)
        self.assertEqual(list(self.payment.CpuThrottlingCollector().collect()), [])

    def test_collector_exports_counters_with_the_service_labels(self):
        self.payment.CGROUP_CPU_STAT = ((self.stat("v2", "nr_periods 200\nnr_throttled 50\nthrottled_usec 2500000\n"),
                                         "throttled_usec", 1e-6),)
        families = {family.name: family for family in self.payment.CpuThrottlingCollector().collect()}
        self.assertEqual(set(families), {"nexus_cpu_cfs_periods", "nexus_cpu_cfs_throttled_periods",
                                         "nexus_cpu_cfs_throttled_seconds"})
        sample = families["nexus_cpu_cfs_throttled_periods"].samples[0]
        self.assertEqual(sample.value, 50)
        self.assertEqual(sample.labels["service_name"], "payment-probe-test")


class ThrottlingClient(FakePrometheusClient):
    def __init__(self, periods=10.0, throttled=4.0, seconds=0.3):
        super().__init__()
        self.rates = {"nexus_cpu_cfs_periods_total": periods, "nexus_cpu_cfs_throttled_periods_total": throttled,
                      "nexus_cpu_cfs_throttled_seconds_total": seconds}

    def _sample_value(self, query, evaluation_time):
        for name, value in self.rates.items():
            if name + "{" in query:
                return value
        return super()._sample_value(query, evaluation_time)


class M1ThrottlingTests(unittest.IsolatedAsyncioTestCase):
    def provider(self, client):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return PrometheusTelemetryProvider(client, make_settings(Path(directory.name)))

    async def test_ratio_of_throttled_periods(self):
        result = await self.provider(ThrottlingClient()).cpu_throttling("payment-service")
        self.assertAlmostEqual(result.throttled_period_ratio, 0.4)
        self.assertAlmostEqual(result.throttled_seconds_per_second, 0.3)

    async def test_no_cpu_limit_means_no_reading_rather_than_zero(self):
        with self.assertRaises(TelemetryMissing):
            await self.provider(ThrottlingClient(periods=0)).cpu_throttling("payment-service")
