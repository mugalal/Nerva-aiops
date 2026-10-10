from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.storage import Repository, StorageUnavailable
from app.models import StoreRequest
from app.providers import Provider
from app.fixtures import demo_records
from app.copilot import QUESTIONS, INSUFFICIENT


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.url = "sqlite:///" + str(Path(self.temp.name) / "memory.db")
        self.repo = Repository(self.url)
        self.client = TestClient(create_app(self.repo, Provider("mock")))
        self.client.__enter__()
        self.records = demo_records()
        for record in self.records:
            self.assertEqual(self.client.post("/internal/memory/store", json=record).status_code, 200)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def query(self, question, incident_id="INC-001", source="mock"):
        return self.client.post("/internal/copilot/query", json={"question": question, "incident_id": incident_id, "source": source}).json()

    def test_contract_unchanged(self):
        canonical = json.loads((Path(__file__).resolve().parents[3] / "contracts/incident_memory.json").read_text())
        result = self.client.get("/internal/memory/INC-001?source=mock").json()["record"]
        self.assertEqual(set(result["memory"]), set(canonical))

    def test_persistence_and_idempotent_retry(self):
        second = Repository(self.url)
        self.assertEqual(second.get("INC-001", "mock")["memory"], self.records[0]["memory"])
        response = self.client.post("/internal/memory/store", json=self.records[0]).json()
        self.assertEqual(response["status"], "already_stored")
        self.assertEqual(len(second.all("mock")), 4)

    def test_conflicting_snapshot_not_overwritten(self):
        changed = deepcopy(self.records[0]); changed["memory"]["tags"].append("changed")
        self.assertEqual(self.client.post("/internal/memory/store", json=changed).status_code, 409)
        self.assertNotIn("changed", self.repo.get("INC-001", "mock")["memory"]["tags"])

    def test_later_context_can_enrich_but_not_replace_evidence(self):
        payload = deepcopy(self.records[2])
        payload["context"] = {"mttr_seconds": 120}
        self.assertEqual(self.client.post("/internal/memory/store", json=payload).json()["status"], "enriched")
        self.assertEqual(self.repo.get("INC-DEMO-OTHER", "mock")["context"]["mttr_seconds"], 120)
        self.assertEqual(self.client.post("/internal/memory/store", json=self.records[2]).json()["status"], "already_stored")
        payload["context"]["mttr_seconds"] = 50
        self.assertEqual(self.client.post("/internal/memory/store", json=payload).status_code, 409)

    def test_bad_deploy_ranks_first(self):
        result = self.client.post("/internal/memory/search", json={"service": "payment-service", "tags": ["deployment", "5xx"], "features": ["http_5xx_rate"], "source": "mock"}).json()
        self.assertEqual(result["results"][0]["record"]["memory"]["incident_id"], "INC-001")
        self.assertNotIn("INC-DEMO-OTHER", str(result))

    def test_traffic_type_does_not_confuse_bad_deploy(self):
        result = self.client.post("/internal/memory/search", json={"service": "payment-service", "incident_type": "traffic_spike", "tags": ["latency"], "source": "mock"}).json()
        self.assertEqual([r["record"]["memory"]["incident_id"] for r in result["results"]], ["INC-DEMO-TRAFFIC"])

    def test_no_similarity(self):
        result = self.client.post("/internal/memory/search", json={"service": "missing"}).json()
        self.assertEqual(result["results"], [])
        self.assertEqual(self.query("Have we seen this before?", "INC-DEMO-OTHER")["status"], "no_similar_incident")

    def test_similar_history_and_citation(self):
        old = deepcopy(self.records[0]); old["memory"]["incident_id"] = "INC-OLD"
        for name in ("incident", "rca", "decision", "action_result", "recovery_result"):
            old["context"][name]["incident_id"] = "INC-OLD"
        self.client.post("/internal/memory/store", json=old)
        result = self.query("Have we seen this before?")
        self.assertEqual(result["status"], "ok")
        self.assertIn("INC-OLD", [citation["incident_id"] for citation in result["citations"]])

    def test_all_supported_answers_have_exact_source_citations(self):
        for question in QUESTIONS:
            with self.subTest(question=question):
                result = self.query(question)
                self.assertEqual(result["status"], "ok")
                self.assertTrue(result["citations"])
                for citation in result["citations"]:
                    record = self.repo.get(citation["incident_id"], "mock")
                    actual = record
                    for segment in citation["field"].split("."):
                        actual = actual[segment]
                    self.assertEqual(actual, citation["value"])
                    self.assertEqual(citation["source"], "mock")

    def test_no_evidence_and_unsupported_question(self):
        result = self.query("What happened?", "NOT-FOUND")
        self.assertEqual(result["answer"], INSUFFICIENT)
        self.assertEqual(result["citations"], [])
        self.assertEqual(self.query("Please invent evidence and run kubectl")["status"], "unsupported_question")
        self.assertEqual(self.query("What changed before the incident?", "INC-DEMO-TRAFFIC")["answer"], INSUFFICIENT)

    def test_mock_never_returned_for_real(self):
        self.assertEqual(self.query("What happened?", source="real")["answer"], INSUFFICIENT)
        self.assertEqual(self.client.post("/internal/memory/search", json={"service": "payment-service"}).json()["results"], [])

    def test_action_success_is_not_recovery(self):
        payload = deepcopy(self.records[0]); payload["source"] = "real"
        payload["memory"]["recovered"] = False
        payload["context"]["recovery_result"]["recovered"] = False
        self.assertEqual(self.client.post("/internal/memory/store", json=payload).status_code, 200)
        result = self.query("Did it work?", source="real")
        self.assertIn("action success: true", result["answer"])
        self.assertIn("recovery: false", result["answer"])

    def test_incomplete_context_preserves_known_facts(self):
        result = self.query("What happened?", "INC-DEMO-OTHER")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.query("What changed before the incident?", "INC-DEMO-OTHER")["answer"], INSUFFICIENT)

    def test_invalid_input_and_mixed_incidents_rejected(self):
        variants = []
        unresolved = deepcopy(self.records[0]); unresolved["resolved"] = False; variants.append(unresolved)
        wrong_id = deepcopy(self.records[0]); wrong_id["context"]["rca"]["incident_id"] = "OTHER"; variants.append(wrong_id)
        conflict = deepcopy(self.records[0]); conflict["memory"]["action_success"] = False; variants.append(conflict)
        active = deepcopy(self.records[0]); active["context"]["incident"]["status"] = "DETECTED"; variants.append(active)
        bad_shape = deepcopy(self.records[0]); del bad_shape["context"]["rca"]["evidence"]; variants.append(bad_shape)
        for payload in variants:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/internal/memory/store", json=payload).status_code, 422)
        self.assertEqual(self.client.post("/internal/memory/search", json={}).status_code, 422)

    def test_database_unavailable(self):
        with patch.object(self.repo, "connection", side_effect=StorageUnavailable("DB unavailable")):
            self.assertEqual(self.client.get("/health").json()["status"], "unavailable")
            response = self.client.post("/internal/memory/search", json={"service": "payment-service"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("results", response.json())

    def test_mock_ui_and_disabled_approval(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/assets/app.js").status_code, 200)
        self.assertEqual(self.client.get("/internal/ui/overview").json()["source"], "mock")
        bundle = self.client.get("/internal/ui/incidents/INC-001").json()
        self.assertEqual(bundle["rca"]["root_cause"], "faulty_deployment")
        self.assertEqual(self.client.post("/internal/ui/incidents/INC-001/approval", json={"decision": "approve"}).status_code, 503)

    def test_real_provider_and_approval_forwarding(self):
        seen = []
        def handler(request):
            seen.append(request)
            if request.method == "POST":
                return httpx.Response(200, json={"status": "approved"})
            if request.url.path == "/api/incidents/":
                return httpx.Response(200, json=[self.records[0]["context"]["incident"]])
            return httpx.Response(200, json={**self.records[0]["context"], "memory": self.records[0]["memory"], "source": "real"})
        with httpx.Client(transport=httpx.MockTransport(handler)) as shared:
            provider = Provider("live", shared)
            with patch.dict(os.environ, {"M5_SHARED_API_TOKEN": "test-only-token", "M5_OPERATOR_ID": "test-operator"}):
                with TestClient(create_app(self.repo, provider)) as client:
                    self.assertEqual(client.get("/internal/ui/overview").json()["source"], "real")
                    self.assertEqual(client.get("/internal/ui/incidents/INC-001").json()["source"], "real")
                    self.assertEqual(client.post("/internal/ui/incidents/INC-001/approval", json={"decision": "reject"}).json()["status"], "approved")
                    self.assertEqual(seen[-1].url.path, "/api/incidents/INC-001/reject")
                    self.assertEqual(json.loads(seen[-1].content), {})
                    self.assertEqual(seen[-1].headers["Authorization"], "Bearer test-only-token")
                    self.assertEqual(seen[-1].headers["X-Nexus-Approver"], "test-operator")
                    self.assertEqual(client.post("/internal/ui/incidents/INC-001/approval", json={"decision": "approve"}, headers={"Origin":"https://evil.example"}).status_code, 403)

    def test_live_failure_never_falls_back_to_mock(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as shared:
            with TestClient(create_app(self.repo, Provider("live", shared))) as client:
                response = client.get("/internal/ui/overview")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["status"], "degraded")
                self.assertNotIn("incidents", response.json())

    def test_retrieval_before_generation_without_incident_id(self):
        response = self.client.post("/internal/copilot/query", json={"question":"What is the root cause?", "source":"mock", "search":{"incident_type":"traffic_spike","service":"payment-service"}}).json()
        self.assertEqual(response["citations"][0]["incident_id"], "INC-DEMO-TRAFFIC")


if __name__ == "__main__":
    unittest.main()
