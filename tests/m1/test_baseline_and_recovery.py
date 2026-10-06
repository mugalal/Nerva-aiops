from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.baseline import build_healthy_baseline
from app.models import (
    CaptureEvidenceRequest,
    RecoveryValidationRequest,
    TelemetryMetrics,
    TelemetryWindow,
    TelemetryWindowPoint,
)
from app.providers.loki import LogEntry
from app.service import _select_incident_logs

from tests.m1.support import make_service, make_settings, make_snapshot


class BaselineTests(unittest.TestCase):
    def test_baseline_is_measured_and_threshold_formula_is_visible(self):
        now = datetime.now(timezone.utc)
        latency_values = [80, 90, 100, 110]
        points = [
            TelemetryWindowPoint(
                timestamp=now + timedelta(seconds=index * 15),
                metrics=TelemetryMetrics(
                    cpu=0.2,
                    memory=0.3,
                    request_rate=20,
                    latency_p95_ms=latency,
                    http_5xx_rate=0,
                    replica_count=1,
                ),
            )
            for index, latency in enumerate(latency_values)
        ]
        window = TelemetryWindow(
            service="payment-service",
            version="v1",
            start=points[0].timestamp,
            end=points[-1].timestamp,
            step_seconds=15,
            points=points,
        )
        with TemporaryDirectory() as directory:
            baseline = build_healthy_baseline(window, make_settings(Path(directory)))

        self.assertEqual(baseline.sample_count, 4)
        self.assertAlmostEqual(
            baseline.thresholds.latency_p95_ms_max,
            baseline.latency_p95_ms.p95 * 1.25,
        )
        self.assertEqual(baseline.thresholds.http_5xx_rate_max, 0.01)

    def test_incident_log_selection_keeps_errors_and_stays_compact(self):
        now = datetime.now(timezone.utc)
        entries = [
            LogEntry(timestamp=now, labels={}, line='{"level":"INFO","status_code":200}')
            for _ in range(30)
        ]
        entries.extend(
            [
                LogEntry(
                    timestamp=now,
                    labels={},
                    line='{"level":"ERROR","message":"payment failed"}',
                )
                for _ in range(25)
            ]
        )

        selected = _select_incident_logs(entries)

        self.assertEqual(len(selected), 20)
        self.assertTrue(all('"level":"ERROR"' in line for line in selected))


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_deployment_is_false_before_rollback_and_true_after(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            bad = make_snapshot(version="v2", latency=820, error_rate=0.14)
            service, telemetry, _ = make_service(root, bad)
            service.store.save_baseline(
                build_healthy_baseline(
                    telemetry.history.model_copy(
                        update={
                            "version": "v1",
                            "points": [
                                point.model_copy(
                                    update={
                                        "metrics": point.metrics.model_copy(
                                            update={
                                                "latency_p95_ms": 80,
                                                "http_5xx_rate": 0,
                                            }
                                        )
                                    }
                                )
                                for point in telemetry.history.points
                            ],
                        }
                    ),
                    service.settings,
                )
            )
            await service.capture_evidence(
                CaptureEvidenceRequest(
                    incident_id="INC-DEPLOY",
                    service="payment-service",
                    scenario="bad_deployment",
                )
            )
            request = RecoveryValidationRequest(
                incident_id="INC-DEPLOY",
                service="payment-service",
                action_completed_at=datetime.now(timezone.utc) - timedelta(seconds=5),
                scenario="bad_deployment",
            )

            before_rollback = await service.validate_recovery(request)
            telemetry.current = make_snapshot(version="v2", latency=80, error_rate=0)
            healthy_but_not_rolled_back = await service.validate_recovery(request)
            telemetry.current = make_snapshot(version="v1", latency=80, error_rate=0)
            after_rollback = await service.validate_recovery(request)
            saved = service.store.get_recovery("INC-DEPLOY")

        self.assertFalse(before_rollback.recovered)
        self.assertFalse(before_rollback.slo_restored)
        self.assertFalse(healthy_but_not_rolled_back.recovered)
        self.assertTrue(healthy_but_not_rolled_back.slo_restored)
        self.assertTrue(after_rollback.recovered)
        self.assertTrue(after_rollback.slo_restored)
        self.assertEqual(after_rollback.before.latency_p95_ms, 820)
        self.assertEqual(after_rollback.after.latency_p95_ms, 80)
        self.assertEqual(saved.after.version, "v1")
        self.assertTrue(saved.result.recovered)

    async def test_traffic_recovery_requires_more_replicas_and_live_traffic(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            overloaded = make_snapshot(
                request_rate=100,
                latency=500,
                error_rate=0.05,
                replicas=1,
            )
            service, telemetry, _ = make_service(root, overloaded)
            healthy = make_snapshot(request_rate=60, latency=80, error_rate=0, replicas=1)
            telemetry.history = telemetry.history.model_copy(
                update={
                    "points": [
                        point.model_copy(update={"metrics": healthy.metrics})
                        for point in telemetry.history.points
                    ]
                }
            )
            service.store.save_baseline(
                build_healthy_baseline(telemetry.history, service.settings)
            )
            await service.capture_evidence(
                CaptureEvidenceRequest(
                    incident_id="INC-TRAFFIC",
                    service="payment-service",
                    scenario="traffic_spike",
                )
            )
            request = RecoveryValidationRequest(
                incident_id="INC-TRAFFIC",
                service="payment-service",
                action_completed_at=datetime.now(timezone.utc),
                scenario="traffic_spike",
            )

            telemetry.current = healthy
            without_scale = await service.validate_recovery(request)
            telemetry.current = make_snapshot(
                request_rate=60,
                latency=80,
                error_rate=0,
                replicas=2,
            )
            with_scale = await service.validate_recovery(request)

        self.assertFalse(without_scale.recovered)
        self.assertTrue(without_scale.slo_restored)
        self.assertTrue(with_scale.recovered)


if __name__ == "__main__":
    unittest.main()
