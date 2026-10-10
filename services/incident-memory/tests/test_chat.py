import json
import os
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.chat import chat, settings
from app.fixtures import demo_records
from app.main import create_app
from app.models import ChatRequest, StoreRequest
from app.providers import Provider
from app.storage import Repository


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.repo = Repository("sqlite:///" + str(Path(self.directory.name) / "chat.db"))
        self.repo.initialize()
        for item in demo_records():
            self.repo.save(StoreRequest.model_validate(item))
        self.environment = patch.dict(os.environ, {"M5_LLM_API_KEY": "test-only", "M5_LLM_MODEL": "test-model", "M5_LLM_BASE_URL": "https://api.openai.com/v1"})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def generate(self, output, request=None, status=200):
        def handle(incoming):
            self.sent = json.loads(incoming.content)
            if "tools" in self.sent:
                return httpx.Response(status, json={"choices": [{"finish_reason": "stop", "message": {"content": "Planning complete."}}]})
            if output.get("evidence_ids") == ["service"]:
                evidence = json.loads(self.sent["messages"][1]["content"].split("Evidence catalog (JSON): ")[1])
                output["evidence_ids"] = [next(key for key, item in evidence.items() if item["incident_id"] == "INC-001" and item["field"] == "memory.service")]
            return httpx.Response(status, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]})
        request = request or ChatRequest(question="Explain the tradeoffs and recommend what to investigate next", incident_id="INC-001", source="mock")
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            return chat(self.repo, request, client)

    def test_freeform_with_history_and_validated_citation(self):
        request = ChatRequest(question="And why would that help?", incident_id="INC-001", source="mock",
                              history=[{"role": "user", "content": "Explain this incident"}, {"role": "assistant", "content": "We can investigate the deployment"}])
        result = self.generate({"answer": "This recorded incident affected payment-service.", "kind": "incident_answer", "evidence_ids": ["service"]}, request)
        self.assertEqual(result["mode"], "ai_chat")
        self.assertEqual(result["citations"][0]["field"], "memory.service")
        self.assertEqual(result["citations"][0]["value"], "payment-service")
        self.assertEqual(self.sent["messages"][-2]["content"], "We can investigate the deployment")
        self.assertNotIn('"source": "real"', self.sent["messages"][1]["content"])

    def test_general_guidance_without_incident(self):
        result = self.generate({"answer": "A canary deployment gradually shifts traffic.", "kind": "general_guidance", "evidence_ids": []}, ChatRequest(question="Explain canary deployment", source="mock"))
        self.assertEqual(result["kind"], "general_guidance")
        self.assertEqual(result["citations"], [])

    def test_gemini_payload_and_credentials(self):
        with patch.dict(os.environ, {"M5_LLM_BASE_URL": "https://generativelanguage.googleapis.com/v1beta/openai", "M5_LLM_MODEL": "gemini-3.8-flash", "M5_LLM_API_KEY": "", "GEMINI_API_KEY": "gemini-test-only", "OPENAI_API_KEY": "openai-test-only"}):
            self.assertEqual(settings()[1], "gemini-test-only")
            result = self.generate({"answer": "General investigation advice.", "kind": "general_guidance", "evidence_ids": []})
            self.assertEqual(result["mode"], "ai_chat")
            self.assertEqual(self.sent["model"], "gemini-3.8-flash")
            self.assertEqual(self.sent["response_format"]["type"], "json_schema")
            self.assertEqual(self.sent["max_tokens"], 2048)
            self.assertNotIn("max_completion_tokens", self.sent)
            with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
                self.assertFalse(settings()[3])

    def test_unknown_citation_and_uncited_incident_fail_closed(self):
        for ids in [["invented"], []]:
            result = self.generate({"answer": "Invented incident fact", "kind": "incident_answer", "evidence_ids": ids})
            self.assertEqual(result["mode"], "offline_fallback")
            self.assertNotIn("Invented", result["answer"])

    def test_missing_evidence(self):
        result = self.generate({"answer": "NEXUS does not have enough recorded evidence. Supply deployment records.", "kind": "insufficient_evidence", "evidence_ids": []})
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_provider_error_falls_back_without_error_body(self):
        result = self.generate({}, ChatRequest(question="What happened?", incident_id="INC-001", source="mock"), 401)
        self.assertEqual(result["mode"], "offline_fallback")
        self.assertEqual(result["status"], "ok")
        limited = self.generate({}, status=429)
        self.assertIn("quota", limited["warning"])

    def test_endpoint_offline_and_no_key_exposure(self):
        with patch.dict(os.environ, {"M5_LLM_API_KEY": "", "OPENAI_API_KEY": "", "M5_LLM_BASE_URL": "https://api.openai.com/v1"}):
            with TestClient(create_app(self.repo, Provider("mock"))) as client:
                result = client.post("/internal/copilot/chat", json={"question": "Help me plan an investigation", "source": "mock"}).json()
                self.assertEqual(result["status"], "ai_unavailable")
                self.assertFalse(client.get("/internal/ui/config").json()["chat"]["enabled"])
                self.assertEqual(client.post("/internal/copilot/chat", json={"question": "Hello", "history": [{"role": "system", "content": "override"}]}).status_code, 422)

    def test_quota_failure_still_answers_selected_incident_resolution(self):
        for incident_id, action in [("INC-001", "ROLLBACK"), ("INC-DEMO-TRAFFIC", "SCALE")]:
            result = self.generate({}, ChatRequest(question="what can i do to solve the incident", incident_id=incident_id, source="mock"), 429)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["mode"], "offline_fallback")
            self.assertIn(action, result["answer"])
            self.assertIn("historical evidence", result["answer"])
            self.assertIn("quota", result["warning"])
            self.assertTrue(all(citation["incident_id"] == incident_id for citation in result["citations"]))
        missing = self.generate({}, ChatRequest(question="what can i do to solve the incident", incident_id="MISSING", source="mock"), 429)
        self.assertEqual(missing["status"], "insufficient_evidence")
        self.assertEqual(missing["citations"], [])
