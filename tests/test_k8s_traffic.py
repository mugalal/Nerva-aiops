"""Stdlib tests for the traffic generator's completion reporting."""

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "k8s-traffic.py"
SPEC = importlib.util.spec_from_file_location("k8s_traffic", SCRIPT)
TRAFFIC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRAFFIC)


class CompletionMetricsTests(unittest.TestCase):
    def test_http_failures_and_transport_errors_are_accounted_separately(self):
        metrics = TRAFFIC.CompletionMetrics()
        for completion in ((200, 1.2, None), (201, 4, None), (503, 20.1, None),
                           (0, 9, "TimeoutError")):
            metrics.record(*completion)
        fields = metrics.fields()
        self.assertEqual(fields["completed"], 4)
        self.assertEqual(fields["successful"], 2)
        self.assertEqual(fields["failed"], 2)
        self.assertEqual(fields["statuses"], {"200": 1, "201": 1, "503": 1, "0": 1})
        self.assertEqual(fields["errors"], {"TimeoutError": 1})
        self.assertEqual(fields["error_count"], 1)
        self.assertEqual(fields["latency_p95_ms"], 21)

    def test_interval_reset_excludes_outage_but_preserves_cumulative_history(self):
        cumulative = TRAFFIC.CompletionMetrics()
        interval = TRAFFIC.CompletionMetrics(latency_bucket_limit=120_000)
        for metrics in (cumulative, interval):
            metrics.record(0, 10_000, "TimeoutError")
        outage_report = interval.fields()
        interval.reset()
        for metrics in (cumulative, interval):
            metrics.record(200, 5.1, None)
        recovered_report = interval.fields()
        self.assertEqual(outage_report["failed"], 1)
        self.assertEqual(outage_report["latency_p95_ms"], 10_000)
        self.assertEqual(recovered_report["completed"], 1)
        self.assertEqual(recovered_report["failed"], 0)
        self.assertEqual(recovered_report["errors"], {})
        self.assertEqual(recovered_report["latency_p95_ms"], 6)
        self.assertEqual(cumulative.fields()["latency_p95_ms"], 10_000)
        self.assertEqual(cumulative.fields()["failed"], 1)
        self.assertEqual(outage_report["errors"], {"TimeoutError": 1})

    def test_bounded_histogram_keeps_outliers_in_percentile_rank(self):
        metrics = TRAFFIC.CompletionMetrics(latency_bucket_limit=100)
        for _ in range(19):
            metrics.record(200, 5, None)
        metrics.record(0, 120_001, "TimeoutError")
        self.assertEqual(metrics.fields()["latency_p95_ms"], 5)
        self.assertEqual(metrics.fields()["latency_overflow_count"], 1)
        metrics.record(0, 130_000, "TimeoutError")
        fields = metrics.fields()
        self.assertIsNone(fields["latency_p95_ms"])
        self.assertTrue(fields["latency_p95_overflow"])
        self.assertEqual(fields["latency_p95_lower_bound_ms"], 101)
        self.assertEqual(len(metrics.latency_buckets), 1)
        self.assertEqual(fields["completed"], 21)
        metrics.reset()
        self.assertEqual(metrics.fields()["completed"], 0)
        self.assertIsNone(metrics.fields()["latency_p95_ms"])
        self.assertFalse(metrics.fields()["latency_p95_overflow"])
        self.assertEqual(metrics.fields()["latency_overflow_count"], 0)


if __name__ == "__main__":
    unittest.main()
