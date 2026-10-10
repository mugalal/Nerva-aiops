"""Actual M4 envelope compatibility and operator decisions through M5's API."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.fixtures import demo_records
from app.main import create_app
from app.models import StoreRequest
from app.providers import Provider
from app.storage import Repository, StorageUnavailable
from app.tools import CopilotTools


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.repo = Repository("sqlite:///" + str(Path(self.directory.name) / "gateway.db"))
        self.repo.initialize()
        self.record = deepcopy(demo_records()[0])
        self.incident = self.record["context"]["incident"]
        self.incident["status"] = "AWAITING_APPROVAL"
        self.writes = []

    def tearDown(self):
        self.directory.cleanup()

    def upstream(self, request):
        if request.method == "POST":
            self.writes.append(request)
            return httpx.Response(200, json={"incident_id": "INC-001", "status": "ESCALATED", "approved": False})
        if request.url.path == "/api/incidents/":
            return httpx.Response(200, json=[self.incident])
        return httpx.Response(200, json={"incident": self.incident, "decision": self.record["context"]["decision"],
            "rca": self.record["context"]["rca"], "source": "mixed", "provider_modes": {"rca": "real"},
            "remediation_backend": "mock", "action_result": None, "recovery_result": None,
            "finops_context": None, "workflow": {"archive_status": "not_eligible"}, "audit": []})

    def test_m4_array_listing_and_aggregate_preserve_unknown_outcomes(self):
        with httpx.Client(transport=httpx.MockTransport(self.upstream)) as transport:
            with TestClient(create_app(self.repo, Provider("live", transport))) as client:
                overview = client.get("/internal/ui/overview")
                self.assertEqual(overview.status_code, 200)
                self.assertEqual(overview.json()["incidents"], [self.incident])
                bundle = client.get("/internal/ui/incidents/INC-001").json()
                self.assertEqual(bundle["source"], "mixed")
                self.assertIsNone(bundle["recovery_result"])
                self.assertIsNone(bundle["finops_context"])
                self.assertNotIn("memory", bundle)
                self.assertNotIn("cost_impact", bundle)
                result = CopilotTools(self.repo, Provider("live", transport), "real").run("list_incidents", {"status": "UNRESOLVED"})
                self.assertEqual(result["incidents"][0]["incident_id"], "INC-001")

    def test_reject_and_approve_use_separate_routes_and_backend_credentials(self):
        with patch.dict(os.environ, {"M5_SHARED_API_TOKEN": "test-only-private", "M5_OPERATOR_ID": "reviewer"}, clear=True):
            with httpx.Client(transport=httpx.MockTransport(self.upstream)) as transport:
                with TestClient(create_app(self.repo, Provider("live", transport))) as client:
                    config = client.get("/internal/ui/config").json()
                    self.assertTrue(config["approval_enabled"])
                    self.assertNotIn("test-only-private", json.dumps(config))
                    for choice in ("reject", "approve"):
                        response = client.post("/internal/ui/incidents/INC-001/approval", json={"decision": choice})
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(self.writes[-1].url.path, f"/api/incidents/INC-001/{choice}")
                        self.assertEqual(self.writes[-1].headers["X-Nexus-Approver"], "reviewer")
                    self.assertEqual(client.post("/internal/ui/incidents/INC-001/approval", json={"decision": "reject"},
                        headers={"Origin": "https://other.example"}).status_code, 403)
                    self.assertEqual(len(self.writes), 2)

    def test_missing_operator_configuration_disables_both_decisions(self):
        with patch.dict(os.environ, {}, clear=True):
            with httpx.Client(transport=httpx.MockTransport(self.upstream)) as transport:
                with TestClient(create_app(self.repo, Provider("live", transport))) as client:
                    self.assertFalse(client.get("/internal/ui/config").json()["approval_enabled"])
                    for choice in ("reject", "approve"):
                        self.assertEqual(client.post("/internal/ui/incidents/INC-001/approval", json={"decision": choice}).status_code, 503)
                    self.assertEqual(client.post("/internal/memory/store", json=deepcopy(demo_records()[0])).status_code, 503)
                    self.assertEqual(self.writes, [])

    def test_live_memory_store_requires_private_backend_token(self):
        with patch.dict(os.environ, {"M5_SHARED_API_TOKEN": "test-only-archive-token", "M5_OPERATOR_ID": "reviewer"}, clear=True):
            with httpx.Client(transport=httpx.MockTransport(self.upstream)) as transport:
                with TestClient(create_app(self.repo, Provider("live", transport))) as client:
                    payload = deepcopy(demo_records()[0])
                    for headers in ({}, {"Authorization": "Bearer incorrect"}):
                        self.assertEqual(client.post("/internal/memory/store", json=payload, headers=headers).status_code, 401)
                    self.assertIsNone(self.repo.get("INC-001", "mock"))
                    response = client.post("/internal/memory/store", json=payload,
                        headers={"Authorization": "Bearer test-only-archive-token"})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["record"]["memory"]["incident_id"], "INC-001")

    def test_dependency_failures_are_readiness_failures(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as transport:
            with TestClient(create_app(self.repo, Provider("live", transport))) as client:
                self.assertEqual(client.get("/ready").status_code, 503)
                self.assertNotIn("incidents", client.get("/internal/ui/overview").json())
                with patch.object(self.repo, "initialize", side_effect=StorageUnavailable("offline")):
                    self.assertEqual(client.get("/ready").json()["status"], "unavailable")

    def test_wrong_incident_and_action_conflict_are_explicit(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"incident": {"incident_id": "OTHER"}}))) as transport:
            with TestClient(create_app(self.repo, Provider("live", transport))) as client:
                self.assertEqual(client.get("/internal/ui/incidents/INC-001").status_code, 503)
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"incident": self.incident}))) as transport:
            with TestClient(create_app(self.repo, Provider("live", transport))) as client:
                self.assertEqual(client.get("/internal/ui/incidents/INC-001").status_code, 503)
        with patch.dict(os.environ, {"M5_SHARED_API_TOKEN": "test-only", "M5_OPERATOR_ID": "reviewer"}, clear=True):
            with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(409, json={"detail": "changed"}))) as transport:
                with TestClient(create_app(self.repo, Provider("live", transport))) as client:
                    self.assertEqual(client.post("/internal/ui/incidents/INC-001/approval", json={"decision": "approve"}).status_code, 409)

    def test_archive_join_and_escalated_history_keep_recorded_status(self):
        record = deepcopy(self.record)
        record["source"] = "real"
        record["resolved"] = False
        record["context"]["incident"]["status"] = "ESCALATED"
        record["memory"]["recovered"] = False
        record["context"]["recovery_result"]["recovered"] = False
        self.repo.save(StoreRequest.model_validate(record))
        def real_detail(request):
            response = self.upstream(request)
            payload = response.json()
            if isinstance(payload, dict):
                payload["source"] = "real"
            return httpx.Response(200, json=payload)
        with httpx.Client(transport=httpx.MockTransport(real_detail)) as transport:
            with TestClient(create_app(self.repo, Provider("live", transport))) as client:
                bundle = client.get("/internal/ui/incidents/INC-001").json()
                self.assertFalse(bundle["memory"]["recovered"])
                self.assertFalse(bundle["archive"]["resolved"])
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as transport:
            rows = CopilotTools(self.repo, Provider("live", transport), "real").run("list_incidents", {"status": "UNRESOLVED"})
            self.assertEqual(rows["incidents"][0]["status"], "ESCALATED")
