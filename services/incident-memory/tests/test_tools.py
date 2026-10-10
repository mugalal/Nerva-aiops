from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.actions import ActionDrafts
from app.chat import chat
from app.fixtures import demo_records, demo_bundle
from app.main import create_app
from app.models import ChatRequest, StoreRequest
from app.providers import Provider, ProviderUnavailable
from app.storage import Repository
from app.tools import CopilotTools, ActionArgs, MODELS, TOOL_EFFECTS, TOOL_DEFINITIONS


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.repo = Repository("sqlite:///" + str(Path(self.directory.name) / "tools.db"))
        self.repo.initialize()
        for item in demo_records():
            self.repo.save(StoreRequest.model_validate(item))
        self.drafts = ActionDrafts()
        self.tools = CopilotTools(self.repo, Provider("mock"), "mock", self.drafts)

    def tearDown(self):
        self.directory.cleanup()

    def test_listing_filter_pagination(self):
        result = self.tools.run("list_incidents", {"limit": 2})
        self.assertEqual(result["total_matches"], 4)
        self.assertEqual(result["next_offset"], 2)
        second = self.tools.run("list_incidents", {"offset": 2, "limit": 2})
        self.assertFalse(set(row["incident_id"] for row in result["incidents"]) & set(row["incident_id"] for row in second["incidents"]))
        self.assertEqual(self.tools.run("list_incidents", {"status": "UNRESOLVED"})["incidents"], [])
        filtered = self.tools.run("list_incidents", {"service": "catalog-service"})
        self.assertEqual(filtered["incidents"][0]["incident_id"], "INC-DEMO-OTHER")

    def test_named_incident_and_search_source(self):
        result = self.tools.run("get_incident", {"incident_id": "INC-DEMO-TRAFFIC"})
        self.assertEqual(result["current"]["memory"]["root_cause"], "traffic_spike")
        self.assertEqual(self.tools.run("get_incident", {"incident_id": "MISSING"})["status"], "not_found")
        found = self.tools.run("search_incident_memory", {"service": "payment-service", "source": "real"})
        self.assertEqual(found["source"], "mock")

    def test_invalid_arguments_and_unknown_tool(self):
        self.assertEqual(self.tools.run("list_incidents", {"limit": 500})["status"], "error")
        self.assertEqual(self.tools.run("execute_shell", {"command": "anything"})["status"], "error")
        self.assertEqual(self.tools.run("prepare_action_request", {"action": "DELETE"})["status"], "error")

    def test_mock_health_not_presented_as_live(self):
        self.assertEqual(self.tools.run("get_service_health", {})["status"], "unavailable")

    def test_live_list_and_partial_unresolved_coverage(self):
        def handler(request):
            return httpx.Response(200, json={"status": "ok", "incidents": [{"incident_id": "INC-LIVE", "status": "OPEN", "affected_services": ["orders"], "severity": "high"}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            tools = CopilotTools(self.repo, Provider("live", client), "real")
            result = tools.run("list_incidents", {"status": "UNRESOLVED"})
            self.assertEqual(result["incidents"][0]["incident_id"], "INC-LIVE")
            self.assertEqual(result["total_matches"], 1)
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            result = CopilotTools(self.repo, Provider("live", client), "real").run("list_incidents", {"status": "UNRESOLVED"})
            self.assertEqual(result["status"], "partial")
            self.assertIn("Unresolved coverage", result["notices"][0])
            self.assertEqual(result["incidents"], [])
        for status in [None, "UNKNOWN", "UNRECOGNIZED_STATE"]:
            with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "ok", "incidents": [{"incident_id": "INC-UNKNOWN", "status": status}]}))) as client:
                result = CopilotTools(self.repo, Provider("live", client), "real").run("list_incidents", {"status": "UNRESOLVED"})
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["incidents"], [])

    def test_live_health_requires_explicit_provenance(self):
        with patch.dict(os.environ, {"M5_SERVICE_HEALTH_PATH": "/api/health"}):
            for source in ["mock", "real"]:
                with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"source": source, "status": "ok", "timestamp": "2026-10-10T10:00:00Z", "services": [{"service": "orders", "status": "unhealthy"}]}))) as client:
                    result = CopilotTools(self.repo, Provider("live", client), "real").run("get_service_health", {"service": "orders"})
                    self.assertEqual(result["status"], "ok" if source == "real" else "unavailable")

    def test_mock_action_is_review_only_and_target_checked(self):
        args = {"incident_id": "INC-001", "service": "payment-service", "action": "RESTART", "reason": "User requested restart"}
        result = self.tools.run("prepare_action_request", args)
        self.assertEqual(result["status"], "review_required")
        self.assertFalse(result["submission_available"])
        with self.assertRaises(ProviderUnavailable):
            self.drafts.submit(result["draft_id"], Provider("mock"))
        self.assertEqual(self.tools.run("prepare_action_request", {**args, "service": "other"})["status"], "error")

    def test_confirmed_submission_is_pending_and_not_repeated(self):
        writes = []
        def handler(request):
            writes.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "pending_approval"})
        args = ActionArgs(incident_id="INC-001", service="payment-service", action="RESTART", reason="User requested")
        with patch.dict(os.environ, {"M5_ACTION_REQUEST_PATH": "/api/action-requests"}):
            draft = self.drafts.prepare(args, "real", "live")
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                provider = Provider("live", client)
                app = create_app(self.repo, provider)
                app.state.action_drafts.items = self.drafts.items
                with TestClient(app) as api:
                    url = "/internal/copilot/actions/" + draft["draft_id"] + "/submit"
                    self.assertEqual(api.post(url, json={"confirmed": False}).status_code, 422)
                    self.assertEqual(api.post(url, json={"confirmed": True}, headers={"Origin": "https://wrong.example"}).status_code, 403)
                    self.assertEqual(writes, [])
                    result = api.post(url, json={"confirmed": True}).json()
                    self.assertFalse(result["executed"])
                    self.assertEqual(result["m4_response"]["status"], "pending_approval")
                    self.assertEqual(api.post(url, json={"confirmed": True}).status_code, 503)
                self.assertEqual(len(writes), 1)
                self.assertTrue(writes[0]["approval_required"])

    def test_model_tool_loop_returns_actual_list_and_citations(self):
        seen = []
        def model(request):
            payload = json.loads(request.content)
            seen.append(payload)
            if "tools" in payload and not any(message["role"] == "tool" for message in payload["messages"]):
                return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "list_incidents", "arguments": "{}"}}]}}]})
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps({"answer": "There are four mock incidents.", "kind": "system_answer", "evidence_ids": ["T1"]})}}]})
        with patch.dict(os.environ, {"M5_LLM_API_KEY": "test-only", "M5_LLM_BASE_URL": "https://api.openai.com/v1"}):
            with httpx.Client(transport=httpx.MockTransport(model)) as client:
                result = chat(self.repo, ChatRequest(question="Show all incidents", source="mock"), client, Provider("mock"), self.drafts)
        self.assertEqual(result["mode"], "ai_chat")
        self.assertEqual(result["tool_results"][0]["result"]["total_matches"], 4)
        self.assertEqual(result["citations"][0]["field"], "tool.list_incidents")
        self.assertTrue(any(message["role"] == "tool" for message in seen[-1]["messages"]))

    def test_unavailable_health_cannot_be_inferred_from_resolved_incidents(self):
        def model(request):
            payload = json.loads(request.content)
            if not any(message["role"] == "tool" for message in payload["messages"]):
                return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "health1", "type": "function", "function": {"name": "get_service_health", "arguments": "{}"}}]}}]})
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps({"answer": "All incidents are resolved, so no service is unhealthy.", "kind": "system_answer", "evidence_ids": ["T1"]})}}]})
        with patch.dict(os.environ, {"M5_LLM_API_KEY": "test-only", "M5_LLM_BASE_URL": "https://api.openai.com/v1"}):
            with httpx.Client(transport=httpx.MockTransport(model)) as client:
                result = chat(self.repo, ChatRequest(question="Which services are unhealthy?", source="mock"), client, Provider("mock"))
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIn("cannot determine", result["answer"])
        self.assertNotIn("no service is unhealthy", result["answer"])

    def test_provider_quota_failure_preserves_retrieved_rows(self):
        def model(request):
            payload = json.loads(request.content)
            if any(message["role"] == "tool" for message in payload["messages"]):
                return httpx.Response(429, json={"error": "quota"})
            return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "list1", "type": "function", "function": {"name": "list_incidents", "arguments": "{}"}}]}}]})
        with patch.dict(os.environ, {"M5_LLM_API_KEY": "test-only", "M5_LLM_BASE_URL": "https://api.openai.com/v1"}):
            with httpx.Client(transport=httpx.MockTransport(model)) as client:
                result = chat(self.repo, ChatRequest(question="Show all incidents", source="mock"), client, Provider("mock"))
        self.assertEqual(result["mode"], "tool_results")
        self.assertEqual(len(result["tool_results"][0]["result"]["incidents"]), 4)
        self.assertIn("quota", result["warning"])
        self.assertEqual(result["citations"][0]["field"], "tool.list_incidents")

    def test_system_discovery_and_demo_reads_do_not_claim_live_access(self):
        overview = self.tools.run("get_system_overview", {})
        self.assertEqual(overview["capabilities"]["health"], "not_connected")
        self.assertNotIn("api_key", json.dumps(overview).lower())
        inventory = self.tools.run("list_services", {})
        self.assertEqual(inventory["status"], "partial")
        self.assertEqual({row["service"] for row in inventory["services"]}, {"payment-service", "catalog-service"})
        for name, field in [("get_telemetry", "snapshots"), ("get_deployments", "deployments"), ("get_finops", "records")]:
            result = self.tools.run(name, {"service": "payment-service", "limit": 1})
            self.assertEqual(result["source"], "mock")
            self.assertEqual(result["status"], "partial")
            self.assertEqual(len(result[field]), 1)
        self.assertEqual(self.tools.run("get_telemetry", {"service": "catalog-service"})["snapshots"], [])
        self.assertEqual(self.tools.run("get_logs", {"service": "payment-service"})["status"], "unavailable")

    def test_live_read_adapters_validate_source_target_and_bound_results(self):
        for name, setting, field in [("get_telemetry", "M5_TELEMETRY_PATH", "snapshots"), ("get_deployments", "M5_DEPLOYMENTS_PATH", "deployments"), ("get_finops", "M5_FINOPS_PATH", "records"), ("get_logs", "M5_LOGS_PATH", "records")]:
            seen = []
            def handler(request):
                seen.append(request)
                return httpx.Response(200, json={"status": "ok", "source": "real", field: [{"service": "orders", "value": 1}, {"service": "orders", "value": 2}], "next_cursor": "more"})
            with patch.dict(os.environ, {setting: "/api/read"}):
                with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                    result = CopilotTools(self.repo, Provider("live", client), "real").run(name, {"service": "orders", "limit": 1, "cursor": "next page"})
                self.assertEqual(seen[0].method, "GET")
                self.assertEqual(seen[0].url.params["service"], "orders")
                self.assertEqual(seen[0].url.params["cursor"], "next page")
                self.assertEqual(len(result[field]), 1)
                self.assertEqual(result["next_cursor"], "more")
                for source, service in [("mock", "orders"), ("real", "different")]:
                    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"source": source, field: [{"service": service}]}))) as client:
                        result = CopilotTools(self.repo, Provider("live", client), "real").run(name, {"service": "orders"})
                        self.assertEqual(result["status"], "unavailable")

    def test_service_only_action_requires_inventory_and_never_executes(self):
        args = {"service": "payment-service", "action": "RESTART", "reason": "Explicit user request"}
        draft = self.tools.run("prepare_action_request", args)
        self.assertEqual(draft["status"], "review_required")
        self.assertIsNone(draft["proposal"]["incident_id"])
        self.assertFalse(draft["submission_available"])
        self.assertEqual(self.tools.run("prepare_action_request", {**args, "service": "unknown"})["status"], "error")
        with patch.dict(os.environ, {"M5_SERVICES_PATH": "/api/services", "M5_ACTION_REQUEST_PATH": "/api/requests"}):
            seen = []
            def handler(request):
                seen.append(request.method)
                return httpx.Response(200, json={"source": "real", "status": "ok", "services": [{"service": "payment-service"}]})
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                draft = CopilotTools(self.repo, Provider("live", client), "real", self.drafts).run("prepare_action_request", args)
                self.assertTrue(draft["submission_available"])
                self.assertEqual(seen, ["GET"])

    def test_action_status_is_read_only_and_checks_requested_id(self):
        with patch.dict(os.environ, {"M5_ACTION_STATUS_PATH": "/api/requests/{request_id}"}):
            for response_id in ["REQ-1", "DIFFERENT"]:
                seen = []
                def handler(request):
                    seen.append(request.method)
                    return httpx.Response(200, json={"source": "real", "request_id": response_id, "status": "pending_approval"})
                with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                    result = CopilotTools(self.repo, Provider("live", client), "real").run("get_action_status", {"request_id": "REQ-1"})
                self.assertEqual(result["status"], "pending_approval" if response_id == "REQ-1" else "unavailable")
                self.assertEqual(seen, ["GET"])

    def test_multi_step_question_does_not_stop_after_incident_listing(self):
        def model(request):
            payload = json.loads(request.content)
            previous = [message for message in payload["messages"] if message["role"] == "tool"]
            if len(previous) < 2:
                name, arguments = ("list_incidents", "{}") if not previous else ("get_finops", '{"service":"payment-service"}')
                return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call" + str(len(previous)), "type": "function", "function": {"name": name, "arguments": arguments}}]}}]})
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps({"answer": "Four demo incidents and historical resource evidence; no live costs are available.", "kind": "system_answer", "evidence_ids": ["T1", "T2"]})}}]})
        with patch.dict(os.environ, {"M5_LLM_API_KEY": "test-only", "M5_LLM_BASE_URL": "https://api.openai.com/v1"}):
            with httpx.Client(transport=httpx.MockTransport(model)) as client:
                result = chat(self.repo, ChatRequest(question="List incidents and show payment-service cost evidence", source="mock"), client, Provider("mock"))
        self.assertEqual([item["tool"] for item in result["tool_results"]], ["list_incidents", "get_finops"])
        self.assertEqual(len(result["citations"]), 2)

    def test_decline_is_final_idempotent_and_never_submits(self):
        writes = []
        args = ActionArgs(service="payment-service", action="RESTART", reason="User requested")
        with patch.dict(os.environ, {"M5_ACTION_REQUEST_PATH": "/api/requests"}):
            draft = self.drafts.prepare(args, "real", "live")
            with httpx.Client(transport=httpx.MockTransport(lambda request: writes.append(request) or httpx.Response(200, json={"status": "pending_approval"}))) as client:
                app = create_app(self.repo, Provider("live", client))
                app.state.action_drafts.items = self.drafts.items
                with TestClient(app) as api:
                    url = "/internal/copilot/actions/" + draft["draft_id"]
                    self.assertEqual(api.post(url + "/decline", json={}, headers={"Origin": "https://wrong.example"}).status_code, 403)
                    self.assertEqual(self.drafts.items[draft["draft_id"]]["state"], "draft")
                    result = api.post(url + "/decline", json={}).json()
                    self.assertEqual(result["status"], "declined")
                    self.assertFalse(result["submitted"])
                    self.assertFalse(result["executed"])
                    self.assertEqual(api.post(url + "/decline", json={}).json(), result)
                    self.assertEqual(api.post(url + "/submit", json={"confirmed": True}).status_code, 503)
                self.assertEqual(writes, [])

    def test_expired_and_inflight_drafts_cannot_be_accepted_or_declined(self):
        args = ActionArgs(service="payment-service", action="RESTART", reason="User requested")
        with patch.dict(os.environ, {"M5_ACTION_REQUEST_PATH": "/api/requests"}):
            for state in ["submitting", "submitted", "expired"]:
                draft = self.drafts.prepare(args, "real", "live")
                item = self.drafts.items[draft["draft_id"]]
                if state == "expired":
                    item["expires"] = 0
                else:
                    item["state"] = state
                with self.assertRaises(ProviderUnavailable):
                    self.drafts.decline(draft["draft_id"])
                with self.assertRaises(ProviderUnavailable):
                    self.drafts.submit(draft["draft_id"], Provider("live"))

    def test_read_tools_are_automatic_and_unclassified_tools_fail_closed(self):
        self.assertEqual(set(TOOL_EFFECTS), set(MODELS))
        self.assertEqual(TOOL_EFFECTS["prepare_action_request"], "proposal")
        self.assertNotIn("submit_action", {item["function"]["name"] for item in TOOL_DEFINITIONS})
        self.assertEqual(self.drafts.items, {})
        self.assertEqual(self.tools.run("list_incidents", {})["total_matches"], 4)
        self.assertEqual(self.drafts.items, {})
        with patch.dict(MODELS, {"delete_service": ActionArgs}):
            result = self.tools.run("delete_service", {"service": "payment-service", "action": "RESTART", "reason": "Unclassified mutation"})
            self.assertEqual(result["status"], "error")
            self.assertEqual(self.drafts.items, {})

    def test_empty_severity_listing_is_answered_without_model_quota(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: self.fail("Simple listings must not call the model"))) as client:
            result = chat(self.repo, ChatRequest(question="show me all the incidents with low severity", source="mock"), client, Provider("mock"))
        self.assertEqual(result["status"], "ok")
        self.assertIn("There are no incidents with low severity", result["answer"])
        self.assertEqual(result["tool_results"][0]["result"]["incidents"], [])
        self.assertEqual(result["citations"][0]["field"], "tool.list_incidents")
        self.assertNotIn("warning", result)

    def test_severity_listing_returns_matches_and_does_not_hide_incomplete_data(self):
        high = chat(self.repo, ChatRequest(question="Show me all the incidents with HIGH severity.", source="mock"), provider=Provider("mock"))
        self.assertGreater(high["tool_results"][0]["result"]["total_matches"], 0)
        self.assertIn("Found", high["answer"])
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            partial = chat(self.repo, ChatRequest(question="Show me all the incidents with low severity", source="real"), provider=Provider("live", client))
        self.assertEqual(partial["status"], "partial")
        self.assertIn("cannot confirm there are none", partial["answer"])
        self.assertNotIn("There are no incidents", partial["answer"])
        filtered = chat(self.repo, ChatRequest(question="List resolved incidents with high severity for payment-service", source="mock"), provider=Provider("mock"))
        self.assertTrue(all("payment-service" in row["affected_services"] for row in filtered["tool_results"][0]["result"]["incidents"]))
