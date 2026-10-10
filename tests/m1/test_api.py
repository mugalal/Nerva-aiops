from pathlib import Path
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory
import unittest

import httpx

from app.main import create_app

from tests.m1.support import make_service, make_settings, make_snapshot


class M1ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        root = Path(self.directory.name)
        self.service, _, _ = make_service(root, make_snapshot())
        self.app = create_app(make_settings(root), self.service)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def test_snapshot_endpoint_returns_frozen_contract(self):
        response = await self.client.get(
            "/internal/telemetry/snapshot",
            params={"service": "payment-service"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["service"], "payment-service")
        self.assertEqual(payload["version"], "v1")
        self.assertEqual(payload["metrics"]["latency_p95_ms"], 80)

    async def test_m3_compatibility_routes_use_stored_incident_evidence(self):
        capture = await self.client.post(
            "/internal/evidence/capture",
            json={
                "incident_id": "INC-API",
                "service": "payment-service",
                "scenario": "bad_deployment",
            },
        )
        telemetry = await self.client.get(
            "/internal/telemetry",
            params={"incident_id": "INC-API"},
        )
        deployment = await self.client.get(
            "/internal/deployments",
            params={"incident_id": "INC-API"},
        )
        logs = await self.client.get(
            "/internal/logs",
            params={"incident_id": "INC-API"},
        )

        self.assertEqual(capture.status_code, 200)
        self.assertEqual(telemetry.status_code, 200)
        self.assertEqual(deployment.status_code, 200)
        self.assertEqual(deployment.json()["new_version"], "v2")
        self.assertEqual(logs.status_code, 200)
        self.assertEqual(len(logs.json()["entries"]), 1)

    async def test_missing_evidence_is_explicit_instead_of_fabricated(self):
        response = await self.client.get(
            "/internal/telemetry",
            params={"incident_id": "INC-MISSING"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"]["category"], "evidence_not_found")
        self.assertFalse(response.json()["detail"]["retryable"])

    async def test_bad_time_windows_return_422_instead_of_500(self):
        now = datetime.now(timezone.utc)
        for start, end in [(now, now - timedelta(minutes=1)),
                           (now.replace(tzinfo=None), now),
                           (now, now + timedelta(minutes=1))]:
            with self.subTest(start=start, end=end):
                response = await self.client.get("/internal/telemetry/window", params={
                    "service": "payment-service", "start": start.isoformat(), "end": end.isoformat()
                })
                self.assertEqual(response.status_code, 422)

    async def test_unknown_scenarios_and_naive_action_times_are_rejected(self):
        response = await self.client.post("/internal/evidence/capture", json={
            "incident_id": "INC-TYPO", "service": "payment-service", "scenario": "bad_release"
        })
        self.assertEqual(response.status_code, 422)
        response = await self.client.post("/internal/recovery/validate", json={
            "incident_id": "INC-TYPO", "service": "payment-service", "scenario": "bad_deployment",
            "action_completed_at": "2026-10-08T10:00:00"
        })
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
