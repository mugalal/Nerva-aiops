from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from dataclasses import replace

from app.baseline import build_healthy_baseline
from app.errors import M1Error
from app.models import (
    CaptureEvidenceRequest,
    RecoveryValidationRequest,
    TelemetryMetrics,
    TelemetryWindow,
    TelemetryWindowPoint,
)
from app.providers.loki import LogEntry
from app.service import _select_incident_logs

from tests.m1.support import make_service, make_settings, make_snapshot, make_window


class BaselineTests(unittest.TestCase):
    def test_invalid_settings_cannot_disable_measurement_guards(self):
        with TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            for change in ({"stale_after_seconds": float("nan")},
                           {"baseline_latency_multiplier": float("inf")},
                           {"recovery_hold_seconds": 1},
                           {"traffic_continuity_ratio": 0}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    replace(settings, **change)

    def test_sparse_idle_or_faulty_windows_cannot_replace_a_healthy_baseline(self):
        with TemporaryDirectory() as directory:
            settings = make_settings(Path(directory))
            healthy = make_window(make_snapshot())
            cases = {
                "too_few_samples": healthy.model_copy(update={"points": healthy.points[:1]}),
                "gap": healthy.model_copy(update={"points": healthy.points[:2] + healthy.points[3:]}),
                "idle": make_window(make_snapshot(request_rate=0)),
                "faulty": make_window(make_snapshot(error_rate=0.14)),
                "slow": make_window(make_snapshot(latency=820)),
                "no_replicas": make_window(make_snapshot(replicas=0)),
            }
            for name, window in cases.items():
                with self.subTest(name=name), self.assertRaises(M1Error) as caught:
                    build_healthy_baseline(window, settings)
                self.assertEqual(caught.exception.category, "baseline_quality_failed")

    def test_baseline_is_measured_and_threshold_formula_is_visible(self):
        now = datetime.now(timezone.utc)
        latency_values = [80, 90, 100, 110, 120]
        points = [
            TelemetryWindowPoint(
                    timestamp=now - timedelta(seconds=60 - index * 15),
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

        self.assertEqual(baseline.sample_count, 5)
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
    @staticmethod
    def mark_capture_in_past(service, incident_id):
        evidence = service.store.get_evidence(incident_id)
        capture_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        baseline = evidence.baseline
        if baseline is not None:
            baseline = baseline.model_copy(update={
                "measured_at": capture_time - timedelta(seconds=1),
                "window_start": capture_time - timedelta(seconds=61),
                "window_end": capture_time - timedelta(seconds=1),
            })
        service.store.save_evidence(evidence.model_copy(update={
            "captured_at": capture_time, "baseline": baseline,
        }))

    async def prepare_case(self, root):
        service, telemetry, _ = make_service(root, make_snapshot(latency=80))
        service.store.save_baseline(build_healthy_baseline(telemetry.history, service.settings))
        telemetry.current = make_snapshot(version="v2", latency=820, error_rate=0.14)
        await service.capture_evidence(CaptureEvidenceRequest(
            incident_id="INC-GUARDS", service="payment-service", scenario="bad_deployment"
        ))
        self.mark_capture_in_past(service, "INC-GUARDS")
        telemetry.current = make_snapshot(version="v1", latency=80, error_rate=0)
        request = RecoveryValidationRequest(
            incident_id="INC-GUARDS", service="payment-service", scenario="bad_deployment",
            action_completed_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
        return service, telemetry, request

    async def test_future_completion_and_completion_before_capture_are_rejected(self):
        with TemporaryDirectory() as directory:
            service, _, request = await self.prepare_case(Path(directory))
            for completion in (datetime.now(timezone.utc) + timedelta(hours=1),
                               datetime.now(timezone.utc) - timedelta(hours=1)):
                with self.subTest(completion=completion), self.assertRaises(M1Error) as caught:
                    await service.validate_recovery(request.model_copy(update={"action_completed_at": completion}))
                self.assertEqual(caught.exception.category, "invalid_action_time")

    async def test_single_good_snapshot_cannot_bypass_post_action_hold(self):
        with TemporaryDirectory() as directory:
            service, _, request = await self.prepare_case(Path(directory))
            with self.assertRaises(M1Error) as caught:
                await service.validate_recovery(request.model_copy(update={
                    "action_completed_at": datetime.now(timezone.utc) - timedelta(seconds=75)
                }))
            self.assertEqual(caught.exception.category, "recovery_pending")
            self.assertTrue(caught.exception.retryable)

    async def test_scenario_mismatch_is_rejected(self):
        with TemporaryDirectory() as directory:
            service, _, request = await self.prepare_case(Path(directory))
            with self.assertRaises(M1Error) as caught:
                await service.validate_recovery(request.model_copy(update={"scenario": "traffic_spike"}))
            self.assertEqual(caught.exception.category, "evidence_scenario_mismatch")

    async def test_recovery_requires_every_sample_to_be_healthy(self):
        with TemporaryDirectory() as directory:
            service, telemetry, request = await self.prepare_case(Path(directory))
            telemetry.history.points[1].metrics.http_5xx_rate = 0.14
            result = await service.validate_recovery(request)
            self.assertFalse(result.recovered)
            self.assertFalse(result.slo_restored)
            saved = service.store.get_recovery(request.incident_id)
            self.assertIsNotNone(saved.measurement_window)
            self.assertEqual(len(saved.measurement_window.points), 3)
            self.assertGreaterEqual(saved.measurement_window.start, request.action_completed_at + timedelta(seconds=60))

    async def test_incomplete_or_pre_action_history_cannot_validate_recovery(self):
        with TemporaryDirectory() as directory:
            service, telemetry, request = await self.prepare_case(Path(directory))
            telemetry.history.points = telemetry.history.points[:1]
            with self.assertRaises(M1Error) as caught:
                await service.validate_recovery(request)
            self.assertEqual(caught.exception.category, "recovery_data_incomplete")

    async def test_failed_measurement_preserves_existing_baseline(self):
        from app.models import TimeWindowRequest
        with TemporaryDirectory() as directory:
            service, telemetry, _ = await self.prepare_case(Path(directory))
            saved = service.store.get_baseline("payment-service")
            telemetry.history = make_window(make_snapshot(error_rate=0.14))
            window = telemetry.history
            with self.assertRaises(M1Error):
                await service.measure_baseline(TimeWindowRequest(service="payment-service",
                    start=window.start, end=window.end))
            self.assertEqual(service.store.get_baseline("payment-service"), saved)

    async def test_later_baseline_cannot_redefine_incident_recovery(self):
        with TemporaryDirectory() as directory:
            service, telemetry, request = await self.prepare_case(Path(directory))
            later = build_healthy_baseline(make_window(make_snapshot(version="v2", latency=400)), service.settings)
            service.store.save_baseline(later)
            telemetry.current = make_snapshot(version="v2", latency=80)
            result = await service.validate_recovery(request)
            self.assertFalse(result.recovered)
            telemetry.current = make_snapshot(version="v1", latency=200)
            result = await service.validate_recovery(request)
            self.assertFalse(result.slo_restored)
            telemetry.current = make_snapshot(version="v1", latency=80)
            self.assertTrue((await service.validate_recovery(request)).recovered)

    async def test_legacy_evidence_cannot_use_a_post_capture_baseline(self):
        with TemporaryDirectory() as directory:
            service, _, request = await self.prepare_case(Path(directory))
            evidence = service.store.get_evidence(request.incident_id)
            service.store.save_evidence(evidence.model_copy(update={"baseline": None}))
            with self.assertRaises(M1Error) as caught:
                await service.validate_recovery(request)
            self.assertEqual(caught.exception.category, "incident_baseline_missing")

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
            self.mark_capture_in_past(service, "INC-DEPLOY")
            request = RecoveryValidationRequest(
                incident_id="INC-DEPLOY",
                service="payment-service",
                action_completed_at=datetime.now(timezone.utc) - timedelta(minutes=2),
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
            self.mark_capture_in_past(service, "INC-TRAFFIC")
            request = RecoveryValidationRequest(
                incident_id="INC-TRAFFIC",
                service="payment-service",
                action_completed_at=datetime.now(timezone.utc) - timedelta(minutes=2),
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

            # Healthy metrics after an unrelated release do not prove scaling fixed v1.
            telemetry.current = telemetry.current.model_copy(update={"version": "v3"})
            with_different_release = await service.validate_recovery(request)
            telemetry.current = telemetry.current.model_copy(update={"version": "v1"})
            telemetry.history = telemetry.history.model_copy(update={"version": "mixed"})
            with_mixed_window = await service.validate_recovery(request)

        self.assertFalse(without_scale.recovered)
        self.assertTrue(without_scale.slo_restored)
        self.assertTrue(with_scale.recovered)
        self.assertFalse(with_different_release.recovered)
        self.assertTrue(with_different_release.slo_restored)
        self.assertFalse(with_mixed_window.recovered)


if __name__ == "__main__":
    unittest.main()
