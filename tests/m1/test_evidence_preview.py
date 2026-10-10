from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import httpx

from app.errors import ProviderInvalidResponse, ProviderUnavailable, TelemetryMissing
from app.main import create_app
from app.providers.kubernetes import KubectlKubernetesClient
from app.providers.resources import application_resources
from tests.m1.support import make_service, make_settings, make_snapshot


def deployment(cpu="100m", memory="128Mi", containers=None):
    application = {"name": "payment-service", "resources": {
        "requests": {"cpu": cpu, "memory": memory},
        "limits": {"cpu": "0.5", "memory": "512Mi"},
    }}
    return {
        "metadata": {"generation": 1},
        "spec": {"replicas": 1, "template": {"spec": {"containers": containers or [application]}}},
        "status": {"observedGeneration": 1, "replicas": 1, "updatedReplicas": 1,
                   "readyReplicas": 1, "availableReplicas": 1},
    }


class ResourceConfigurationTests(unittest.TestCase):
    def test_cpu_cores_and_binary_memory_use_correct_units(self):
        config = application_resources(deployment(cpu="0.1"), "payment-service")
        self.assertEqual(config.cpu_request_m, 100)
        self.assertEqual(config.cpu_limit_m, 500)
        self.assertEqual(config.memory_request_mb, 128)
        self.assertEqual(config.memory_limit_mb, 512)
        self.assertEqual(config.memory_unit, "MiB")

    def test_decimal_and_scientific_memory_are_not_treated_as_mib(self):
        for value in ("128M", "128e6", "128000000"):
            with self.subTest(value=value):
                config = application_resources(deployment(memory=value), "payment-service")
                self.assertAlmostEqual(config.memory_request_mb, 128000000 / 1048576)

    def test_sidecar_resources_are_not_added_to_application_limits(self):
        application = deployment()["spec"]["template"]["spec"]["containers"][0]
        sidecar = {"name": "alloy", "resources": {"requests": {"cpu": "2", "memory": "1Gi"}}}
        config = application_resources(deployment(containers=[sidecar, application]), "payment-service")
        self.assertEqual(config.cpu_limit_m, 500)
        with self.assertRaises(ProviderInvalidResponse):
            application_resources(deployment(containers=[sidecar, sidecar]), "payment-service")

    def test_missing_nonfinite_or_inverted_resources_fail_explicitly(self):
        for value in ("0", "NaN", "0.0005", "600m", "1e999999999"):
            with self.subTest(value=value), self.assertRaises(ProviderInvalidResponse):
                application_resources(deployment(cpu=value), "payment-service")
        missing = deployment()
        del missing["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]["cpu"]
        with self.assertRaises(ProviderInvalidResponse):
            application_resources(missing, "payment-service")


class EvidencePreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.service, self.telemetry, _ = make_service(self.root, make_snapshot())
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(make_settings(self.root), self.service)),
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def test_live_preview_needs_no_incident_id_or_scenario_and_does_not_store_evidence(self):
        response = await self.client.get("/internal/evidence/preview", params={"service": "payment-service"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["provider_mode"], "real")
        self.assertEqual(data["telemetry"]["service"], "payment-service")
        self.assertEqual(data["telemetry"]["metrics"]["request_rate"], 20)
        self.assertEqual(data["deployment_event"]["new_version"], "v2")
        self.assertEqual(len(data["selected_logs"]), 1)
        self.assertIsNone(data["baseline"])
        self.assertEqual(list(self.root.rglob("*.json")), [])

    async def test_resource_configuration_is_observed_from_provider(self):
        async def observed_resources(*args):
            return application_resources(deployment(), "payment-service")
        self.service.kubernetes.resource_config = observed_resources
        response = await self.client.get("/internal/evidence/preview", params={"service": "payment-service"})
        self.assertEqual(response.json()["resource_config"]["cpu_limit_m"], 500)

    async def test_unavailable_optional_sources_are_reported_without_fake_values(self):
        async def unavailable(*args, **kwargs):
            raise ProviderUnavailable("kubernetes", "No configured cluster")
        self.service.kubernetes.context = unavailable
        self.service.kubernetes.deployment_event = unavailable
        response = await self.client.get("/internal/evidence/preview", params={"service": "payment-service"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["kubernetes"])
        self.assertIsNone(response.json()["deployment_event"])
        self.assertTrue(response.json()["provider_errors"])

    async def test_missing_mandatory_telemetry_fails(self):
        async def missing(*args, **kwargs):
            raise TelemetryMissing("No payment telemetry")
        self.service.snapshot = missing
        response = await self.client.get("/internal/evidence/preview", params={"service": "payment-service"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["category"], "telemetry_missing")

    async def test_mock_mode_is_never_labelled_real(self):
        self.telemetry.mode = "mock"
        response = await self.client.get("/internal/evidence/preview", params={"service": "payment-service"})
        self.assertEqual(response.json()["provider_mode"], "mock")

    async def test_unready_rollout_cannot_supply_resource_configuration(self):
        client = KubectlKubernetesClient()
        pending = deployment()
        pending["status"]["observedGeneration"] = 0
        client._run_json = lambda *args: pending
        with self.assertRaises(ProviderUnavailable):
            await client.resource_config("payment-service", "nexus-demo")
