from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.errors import BaselineNotFound, EvidenceNotFound
from app.models import (
    BaselineThresholds, HealthyBaseline, IncidentEvidence, MetricStatistics,
    RecoveryEvidenceRecord, RecoveryMetrics, RecoveryResult,
    TelemetryMetrics, TelemetrySnapshot,
)
from app.storage import EvidenceStore, _safe_name


def snapshot(service="payment-service", version="v1"):
    return TelemetrySnapshot(
        timestamp=datetime.now(timezone.utc), service=service, version=version,
        metrics=TelemetryMetrics(
            cpu=0.2, memory=0.3, request_rate=20, latency_p95_ms=80,
            http_5xx_rate=0, replica_count=1,
        ),
    )


def evidence(incident_id, version="v1"):
    before = snapshot(version=version)
    return IncidentEvidence(
        incident_id=incident_id, service=before.service, scenario="bad_deployment",
        captured_at=before.timestamp, before=before, selected_logs=[],
        kubernetes=None, deployment_event=None, provider_errors=[],
    )


def baseline(service, version="v1"):
    now = datetime.now(timezone.utc)
    stats = MetricStatistics(minimum=1, maximum=1, average=1, p95=1)
    return HealthyBaseline(
        service=service, version=version, measured_at=now, window_start=now,
        window_end=now, sample_count=10, request_rate=stats, latency_p95_ms=stats,
        http_5xx_rate=stats, cpu=stats, memory=stats, replica_count=stats,
        thresholds=BaselineThresholds(latency_p95_ms_max=100, http_5xx_rate_max=0.01),
    )


def recovery(incident_id, version="v1"):
    measured = snapshot(version=version)
    metrics = RecoveryMetrics(latency_p95_ms=80, http_5xx_rate=0)
    return RecoveryEvidenceRecord(
        incident_id=incident_id, service=measured.service, scenario="bad_deployment",
        action_completed_at=measured.timestamp, validated_at=measured.timestamp,
        before=measured, after=measured,
        result=RecoveryResult(
            incident_id=incident_id, recovered=True, before=metrics, after=metrics,
            recovery_time_seconds=0, slo_restored=True,
        ),
    )


class EvidenceStorageTests(unittest.TestCase):
    def test_distinct_punctuation_unicode_and_path_keys_round_trip_independently(self):
        keys = ["INC/a", "INC_a", "INC:a", "../INC", "C:\\incident", "حادثة", "CON"]
        with TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            for index, key in enumerate(keys):
                store.save_evidence(evidence(key, str(index)))
                store.save_baseline(baseline(key, str(index)))
                store.save_recovery(recovery(key, str(index)))
            for index, key in enumerate(keys):
                self.assertEqual(store.get_evidence(key).before.version, str(index))
                self.assertEqual(store.get_baseline(key).version, str(index))
                self.assertEqual(store.get_recovery(key).after.version, str(index))
            for data_dir in (store.baseline_dir, store.incident_dir, store.recovery_dir):
                paths = list(data_dir.glob("*.json"))
                self.assertEqual(len(paths), len(keys))
                self.assertTrue(all(path.name.startswith("key-") for path in paths))

    def test_existing_valid_legacy_files_remain_readable_and_new_files_take_precedence(self):
        with TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            for data_dir, filename, payload in (
                (store.baseline_dir, "payment-service.json", baseline("payment-service")),
                (store.incident_dir, "INC-legacy.json", evidence("INC-legacy")),
                (store.recovery_dir, "INC-legacy.json", recovery("INC-legacy")),
            ):
                (data_dir / filename).write_text(payload.model_dump_json(), encoding="utf-8")
            self.assertEqual(store.get_baseline("payment-service").version, "v1")
            self.assertEqual(store.get_evidence("INC-legacy").incident_id, "INC-legacy")
            self.assertEqual(store.get_recovery("INC-legacy").incident_id, "INC-legacy")
            store.save_evidence(evidence("INC-legacy", "v2"))
            self.assertEqual(store.get_evidence("INC-legacy").before.version, "v2")

    def test_legacy_sanitization_alias_never_returns_another_incident(self):
        with TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            (store.incident_dir / "INC_a.json").write_text(
                evidence("INC/a").model_dump_json(), encoding="utf-8",
            )
            for requested in ("INC_a", "INC/a"):
                with self.subTest(requested=requested), self.assertRaises(EvidenceNotFound):
                    store.get_evidence(requested)

    def test_stored_keys_are_validated_in_legacy_and_hashed_paths(self):
        with TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            for name in ("expected.json", f"{_safe_name('expected')}.json"):
                for data_dir, payload, getter, error in (
                    (store.baseline_dir, baseline("wrong"), store.get_baseline, BaselineNotFound),
                    (store.incident_dir, evidence("wrong"), store.get_evidence, EvidenceNotFound),
                    (store.recovery_dir, recovery("wrong"), store.get_recovery, EvidenceNotFound),
                ):
                    with self.subTest(name=name, directory=data_dir.name):
                        (data_dir / name).write_text(payload.model_dump_json(), encoding="utf-8")
                        with self.assertRaises(error):
                            getter("expected")

    def test_recovery_result_cannot_reference_another_incident(self):
        with TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            record = recovery("expected")
            record.result.incident_id = "wrong"
            store.save_recovery(record)
            with self.assertRaises(EvidenceNotFound):
                store.get_recovery("expected")

    def test_concurrent_writers_publish_complete_records_and_use_unique_temporaries(self):
        with TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            store.save_evidence(evidence("shared"))

            def write_and_read(index):
                store.save_evidence(evidence("shared", str(index)))
                record = store.get_evidence("shared")
                self.assertEqual(record.incident_id, "shared")
                self.assertIn(record.before.version, {"v1", *map(str, range(100))})

            with ThreadPoolExecutor(max_workers=12) as executor:
                list(executor.map(write_and_read, range(100)))
            self.assertEqual(len(list(store.incident_dir.glob("*.json"))), 1)
            self.assertEqual(list(store.incident_dir.glob("*.tmp")), [])

    def test_failed_atomic_replace_leaves_no_temporary_files(self):
        with TemporaryDirectory() as directory:
            store = EvidenceStore(Path(directory))
            with patch("app.storage.Path.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    store.save_evidence(evidence("test"))
            self.assertEqual(list(store.incident_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
