from pathlib import Path
import unittest

from app.providers.kubernetes_api import InClusterKubernetesClient


class FixtureKubernetesClient(InClusterKubernetesClient):
    def __init__(self):
        super().__init__(
            "https://kubernetes.test",
            Path("unused-token"),
            Path("unused-ca"),
        )

    async def _get_json(self, path: str, params=None):
        if path == "/version":
            return {"gitVersion": "v1.34.1"}
        if "/deployments/" in path:
            return {
                "metadata": {
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
                    "readyReplicas": 2,
                    "conditions": [
                        {
                            "lastUpdateTime": "2026-10-06T12:00:00Z",
                        }
                    ],
                },
            }
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


if __name__ == "__main__":
    unittest.main()
