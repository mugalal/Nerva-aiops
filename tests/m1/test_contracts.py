from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from app.models import RecoveryResult, TelemetrySnapshot


class FrozenContractTests(unittest.TestCase):
    def test_telemetry_snapshot_accepts_only_the_frozen_shape(self):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": "payment-service",
            "version": "v1",
            "metrics": {
                "cpu": 0.4,
                "memory": 0.6,
                "request_rate": 21.5,
                "latency_p95_ms": 82,
                "http_5xx_rate": 0.01,
                "replica_count": 2,
            },
        }

        snapshot = TelemetrySnapshot.model_validate(payload)

        self.assertEqual(set(snapshot.model_dump()), {"timestamp", "service", "version", "metrics"})
        self.assertEqual(
            set(snapshot.metrics.model_dump()),
            {
                "cpu",
                "memory",
                "request_rate",
                "latency_p95_ms",
                "http_5xx_rate",
                "replica_count",
            },
        )

    def test_telemetry_snapshot_rejects_unknown_fields(self):
        with self.assertRaises(ValidationError):
            TelemetrySnapshot.model_validate(
                {
                    "timestamp": datetime.now(timezone.utc),
                    "service": "payment-service",
                    "version": "v1",
                    "metrics": {
                        "cpu": 0.4,
                        "memory": 0.6,
                        "request_rate": 21.5,
                        "latency_p95_ms": 82,
                        "http_5xx_rate": 0.01,
                        "replica_count": 2,
                        "made_up_metric": 99,
                    },
                }
            )

    def test_recovery_contract_has_no_fake_unknown_status(self):
        result = RecoveryResult.model_validate(
            {
                "incident_id": "INC-001",
                "recovered": True,
                "before": {"latency_p95_ms": 820, "http_5xx_rate": 0.14},
                "after": {"latency_p95_ms": 80, "http_5xx_rate": 0},
                "recovery_time_seconds": 12.5,
                "slo_restored": True,
            }
        )

        self.assertNotIn("status", result.model_dump())
        self.assertNotIn("reason", result.model_dump())


if __name__ == "__main__":
    unittest.main()
