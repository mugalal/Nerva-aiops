import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from app.providers.kubernetes import KubectlKubernetesClient
from app.providers.kubernetes_api import InClusterKubernetesClient


class FixtureKubernetesClient(InClusterKubernetesClient):
    def __init__(self):
        super().__init__(
            "https://kubernetes.test",
            Path("unused-token"),
            Path("unused-ca"),
        )
        self.overrides = {}

    async def _get_json(self, path: str, params=None):
        for resource, override in self.overrides.items():
            if resource in path:
                return deepcopy(override)
        if path == "/version":
            return {"gitVersion": "v1.34.1"}
        if "/deployments/" in path:
            return {
                "metadata": {
                    "uid": "deployment-uid",
                    "generation": 2,
                    "annotations": {
                        "nexus.io/previous-version": "v1",
                        "nexus.io/version": "v2",
                        "nexus.io/commit-sha": "faulty-v2",
                        "nexus.io/pipeline-id": "scenario-a",
                        "deployment.kubernetes.io/revision": "2",
                    }
                },
                "spec": {
                    "replicas": 2,
                    "template": {
                        "metadata": {"labels": {"version": "v2"}},
                        "spec": {
                            "containers": [
                                {
                                    "image": "nexus/payment-service:v2",
                                    "env": [{"name": "VERSION", "value": "v2"}],
                                }
                            ]
                        }
                    },
                },
                "status": {
                    "observedGeneration": 2,
                    "replicas": 2,
                    "updatedReplicas": 2,
                    "readyReplicas": 2,
                    "availableReplicas": 2,
                    "conditions": [
                        {
                            "lastUpdateTime": "2026-10-06T12:00:00Z",
                        }
                    ],
                },
            }
        if path.endswith("/replicasets"):
            return {"items": [{
                "metadata": {
                    "ownerReferences": [{
                        "kind": "Deployment", "uid": "deployment-uid", "controller": True,
                    }],
                    "annotations": {"deployment.kubernetes.io/revision": "2"},
                    "creationTimestamp": "2026-10-06T11:55:00Z",
                },
                "spec": {"template": {"metadata": {"labels": {"version": "v2"}}}},
            }]}
        if path.endswith("/pods"):
            return {
                "items": [
                    {
                        "status": {
                            "containerStatuses": [
                                {"restartCount": 1},
                            ]
                        }
                    },
                    {
                        "status": {
                            "containerStatuses": [
                                {"restartCount": 2},
                            ]
                        }
                    },
                ]
            }
        if path.endswith("/events"):
            return {
                "items": [
                    {
                        "involvedObject": {"name": "payment-service-abc"},
                        "reason": "ScalingReplicaSet",
                        "message": "Scaled up replica set payment-service-v2",
                    },
                    {
                        "involvedObject": {"name": "unrelated"},
                        "reason": "Pulled",
                        "message": "Unrelated image",
                    },
                ]
            }
        raise AssertionError(f"Unexpected Kubernetes path: {path}")


class FixtureKubectlClient(KubectlKubernetesClient):
    def __init__(self):
        super().__init__()
        self.source = FixtureKubernetesClient()
        self.overrides = self.source.overrides

    def _run_json(self, *args):
        resource = args[1]
        if resource == "namespace":
            return {"items": []}
        path = {
            "deployment": "/deployments/payment-service",
            "replicasets": "/replicasets",
            "pods": "/pods",
            "events": "/events",
        }[resource]
        return asyncio.run(self.source._get_json(path))


class InClusterKubernetesTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_context_and_deployment_evidence(self):
        client = FixtureKubernetesClient()

        ready, _ = await client.ready()
        context = await client.context("payment-service", "nexus-demo")
        deployment = await client.deployment_event("payment-service", "nexus-demo")

        self.assertTrue(ready)
        self.assertEqual(context.desired_replicas, 2)
        self.assertEqual(context.ready_replicas, 2)
        self.assertEqual(context.pod_restarts, 3)
        self.assertEqual(context.version, "v2")
        self.assertEqual(context.service_health, "ok")
        self.assertEqual(len(context.events), 1)
        self.assertEqual(deployment.event_id, "DEP-2")
        self.assertEqual(deployment.old_version, "v1")
        self.assertEqual(deployment.new_version, "v2")
        self.assertEqual(deployment.status, "SUCCESS")
        self.assertEqual(deployment.timestamp, datetime(2026, 10, 6, 11, 55, tzinfo=timezone.utc))

    async def test_both_providers_require_observed_updated_available_rollout(self):
        deployment = await FixtureKubernetesClient()._get_json("/deployments/payment-service")
        cases = [
            {"observedGeneration": 1},
            {"updatedReplicas": 1},
            {"availableReplicas": 1},
            {"replicas": 3},
            {"observedGeneration": 0, "updatedReplicas": 0},
        ]
        for factory in (FixtureKubernetesClient, FixtureKubectlClient):
            for changed in cases:
                with self.subTest(provider=factory.__name__, changed=changed):
                    current = deepcopy(deployment)
                    current["status"].update(changed)
                    client = factory()
                    client.overrides["deployments"] = current
                    context = await client.context("payment-service", "nexus-demo")
                    event = await client.deployment_event("payment-service", "nexus-demo")
                    self.assertEqual(context.service_health, "degraded")
                    self.assertEqual(event.status, "IN_PROGRESS")

    async def test_rollback_version_comes_from_template_and_stale_event_is_absent(self):
        deployment = await FixtureKubernetesClient()._get_json("/deployments/payment-service")
        deployment["spec"]["template"]["metadata"]["labels"]["version"] = "v1"
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        container["env"][0]["value"] = "v1"
        container["image"] = "nexus/payment-service:v1"
        for factory in (FixtureKubernetesClient, FixtureKubectlClient):
            with self.subTest(provider=factory.__name__):
                client = factory()
                client.overrides["deployments"] = deployment
                context = await client.context("payment-service", "nexus-demo")
                event = await client.deployment_event("payment-service", "nexus-demo")
                self.assertEqual(context.version, "v1")
                self.assertEqual(context.service_health, "ok")
                self.assertIsNone(event)

    async def test_running_pods_take_precedence_and_mixed_versions_are_degraded(self):
        for factory in (FixtureKubernetesClient, FixtureKubectlClient):
            for versions, expected in ((["v1"], "v1"), (["v1", "v2"], "unknown")):
                with self.subTest(provider=factory.__name__, versions=versions):
                    client = factory()
                    client.overrides["pods"] = {"items": [{
                        "metadata": {"labels": {"version": version}},
                        "status": {
                            "phase": "Running",
                            "conditions": [{"type": "Ready", "status": "True"}],
                        },
                    } for version in versions]}
                    context = await client.context("payment-service", "nexus-demo")
                    self.assertEqual(context.version, expected)
                    self.assertEqual(context.service_health, "degraded")

    async def test_rollout_timestamp_is_not_refreshed_by_conditions_or_missing_evidence(self):
        deployment = await FixtureKubernetesClient()._get_json("/deployments/payment-service")
        deployment["status"]["conditions"][0]["lastUpdateTime"] = datetime.now(timezone.utc).isoformat()
        for factory in (FixtureKubernetesClient, FixtureKubectlClient):
            with self.subTest(provider=factory.__name__):
                client = factory()
                client.overrides["deployments"] = deployment
                event = await client.deployment_event("payment-service", "nexus-demo")
                self.assertEqual(event.timestamp, datetime(2026, 10, 6, 11, 55, tzinfo=timezone.utc))
                client.overrides["replicasets"] = {"items": []}
                self.assertIsNone(await client.deployment_event("payment-service", "nexus-demo"))

    async def test_replicaset_requires_owner_revision_template_and_valid_timestamp(self):
        replica_sets = await FixtureKubernetesClient()._get_json("/replicasets")
        invalid = []
        wrong_owner = deepcopy(replica_sets)
        wrong_owner["items"][0]["metadata"]["ownerReferences"][0]["uid"] = "another-deployment"
        invalid.append(wrong_owner)
        wrong_revision = deepcopy(replica_sets)
        wrong_revision["items"][0]["metadata"]["annotations"]["deployment.kubernetes.io/revision"] = "1"
        invalid.append(wrong_revision)
        wrong_version = deepcopy(replica_sets)
        wrong_version["items"][0]["spec"]["template"]["metadata"]["labels"]["version"] = "v1"
        invalid.append(wrong_version)
        for timestamp in (None, "invalid", (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()):
            wrong_time = deepcopy(replica_sets)
            wrong_time["items"][0]["metadata"]["creationTimestamp"] = timestamp
            invalid.append(wrong_time)
        for factory in (FixtureKubernetesClient, FixtureKubectlClient):
            for index, replica_sets in enumerate(invalid):
                with self.subTest(provider=factory.__name__, invalid=index):
                    client = factory()
                    client.overrides["replicasets"] = replica_sets
                    self.assertIsNone(await client.deployment_event("payment-service", "nexus-demo"))

    async def test_template_release_provenance_overrides_stale_deployment_annotations(self):
        deployment = await FixtureKubernetesClient()._get_json("/deployments/payment-service")
        deployment["spec"]["template"]["metadata"]["annotations"] = {
            "nexus.io/previous-version": "v0", "nexus.io/version": "v2",
            "nexus.io/commit-sha": "template-commit", "nexus.io/pipeline-id": "template-build",
        }
        for factory in (FixtureKubernetesClient, FixtureKubectlClient):
            with self.subTest(provider=factory.__name__):
                client = factory()
                client.overrides["deployments"] = deployment
                event = await client.deployment_event("payment-service", "nexus-demo")
                self.assertEqual(event.old_version, "v0")
                self.assertEqual(event.commit_sha, "template-commit")
                self.assertEqual(event.pipeline_id, "template-build")

    async def test_partial_template_provenance_is_not_filled_with_stale_release_metadata(self):
        deployment = await FixtureKubernetesClient()._get_json("/deployments/payment-service")
        deployment["spec"]["template"]["metadata"]["annotations"] = {"nexus.io/version": "v2"}
        for factory in (FixtureKubernetesClient, FixtureKubectlClient):
            with self.subTest(provider=factory.__name__):
                client = factory()
                client.overrides["deployments"] = deployment
                self.assertIsNone(await client.deployment_event("payment-service", "nexus-demo"))


if __name__ == "__main__":
    unittest.main()
