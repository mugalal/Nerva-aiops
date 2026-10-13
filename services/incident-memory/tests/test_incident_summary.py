"""A long scale-out incident must reach the Copilot as facts, not as 'too_large'."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from app.chat import chat
from app.models import ChatRequest
from app.providers import Provider
from app.storage import Repository
from app.tools import CopilotTools, bounded_result, tool_definitions

INCIDENT = "INC-20261013-044821-payment-service"
OTHER = "INC-20261013-041101-payment-service"
OPTIONS = [{"replicas": 2, "estimated_cost_delta": 0.0656, "risk": "HIGH"},
           {"replicas": 8, "estimated_cost_delta": 0.4594, "risk": "MEDIUM"}]


def spike_context(incident_id=INCIDENT, polls=50):
    """Shape of M4's /api/incidents/{id}/context for a scale-out whose recovery is polled for minutes."""
    recovery = {"incident_id": incident_id, "recovered": False, "slo_restored": False,
                "before": {"latency_p95_ms": 5000.0, "http_5xx_rate": 0.0},
                "after": {"latency_p95_ms": 14.69, "http_5xx_rate": 0.0}, "recovery_time_seconds": 355.8}
    audit = [{"event_id": "AUD-0", "timestamp": "2026-10-13T04:49:23Z", "incident_id": incident_id,
              "event_type": "DECISION_PROPOSED", "details": {"recommended_action": "SCALE"}}]
    audit += [{"event_id": f"AUD-{n}", "timestamp": f"2026-10-13T04:5{n % 6}:{n % 60:02d}Z", "incident_id": incident_id,
               "event_type": "RECOVERY_STABILIZING",
               "details": {"incident_id": incident_id, "success": False, "source": "m1", "details": recovery}}
              for n in range(1, polls + 1)]
    audit.append({"event_id": "AUD-T", "timestamp": "2026-10-13T04:56:01Z", "incident_id": incident_id,
                  "event_type": "RECOVERY_TIMEOUT", "details": {"timed_out": True, "details": recovery}})
    log_line = '2026-10-13T04:49:21Z {"level":"INFO","service":"payment-service","route":"/pay","status":200,"latency_ms":4999.9}'
    return {
        "incident": {"incident_id": incident_id, "status": "ESCALATED", "severity": "high",
                     "affected_services": ["payment-service"], "started_at": "2026-10-13T04:48:21Z"},
        "source": "real", "provider_modes": {"rca": "real", "finops": "real", "recovery": "real"},
        "anomaly": {"anomaly_id": "ANO-1", "timestamp": "2026-10-13T04:48:20Z", "score": 0.99,
                    "model": "z_score_per_service", "features": {"cpu": 1.0, "request_rate": 70.1}},
        "anomalies": [{"anomaly_id": f"ANO-{n}"} for n in range(12)],
        "rca": {"root_cause": "traffic_spike", "confidence": 1.0, "affected_component": "payment-service",
                "evidence": ["Request rate increased significantly"]},
        "decision": {"recommended_action": "SCALE", "parameters": {"replicas": 8}, "risk": "MEDIUM",
                     "reason": "Traffic spike detected and a safe scale option is available",
                     "confidence": 1.0, "approval_required": True},
        "finops_context": {"service": "payment-service", "current_replicas": 1, "current_cpu_request_m": 100,
                           "observed_cpu_pct": 100.0, "temporary_scale_options": OPTIONS},
        "evidence": {"scenario": "traffic_spike", "captured_at": "2026-10-13T04:49:22Z",
                     "before": {"version": "v1", "metrics": {"cpu": 0.998, "request_rate": 70.1,
                                                             "latency_p95_ms": 5000.0, "replica_count": 1}},
                     "selected_logs": [log_line] * 60, "baseline": {"version": "v1", "measured_at": "2026-10-13T04:15:17Z",
                     "thresholds": {"latency_p95_ms_max": 11.89, "http_5xx_rate_max": 0.01}}},
        "action_result": {"action": "SCALE", "status": "SUCCESS", "completed_at": "2026-10-13T04:49:59Z"},
        "recovery_result": recovery,
        "recovery_validation": {"source": "m1", "success": False, "timed_out": True,
                                "deadline_at": "2026-10-13T04:55:59Z", "details": recovery},
        "workflow": {"revision": 120, "recovery_deadline_at": "2026-10-13T04:55:59Z", "diagnosis_error": None,
                     "archive_status": "DELIVERED"},
        "missing": [], "audit": audit,
    }


def shared_api(contexts):
    def handler(request):
        path = request.url.path
        if path == "/api/incidents/":
            return httpx.Response(200, json=[{"incident_id": key, "status": value["incident"]["status"],
                                              "affected_services": ["payment-service"]} for key, value in contexts.items()])
        for incident_id, context in contexts.items():
            if path == f"/api/incidents/{incident_id}/context":
                return httpx.Response(200, json=context)
        return httpx.Response(404, json={"detail": "not found"})
    return httpx.Client(transport=httpx.MockTransport(handler))


class IncidentSummaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.repo = Repository("sqlite:///" + str(Path(self.directory.name) / "summary.db"))
        self.repo.initialize()
        self.env = patch.dict(os.environ)
        self.env.start()
        for name in ("M5_FINOPS_PATH", "M5_TELEMETRY_PATH", "M5_LOGS_PATH", "M5_DEPLOYMENTS_PATH"):
            os.environ.pop(name, None)

    def tearDown(self):
        self.env.stop()
        self.directory.cleanup()

    def test_long_scale_out_incident_fits_and_keeps_every_recorded_fact(self):
        context = spike_context()
        self.assertGreater(len(json.dumps(context)), 20000, "fixture must reproduce the oversized record")
        with shared_api({INCIDENT: context}) as client:
            result = bounded_result(CopilotTools(self.repo, Provider("live", client), "real").run(
                "get_incident", {"incident_id": INCIDENT}))
        self.assertEqual(result["status"], "ok")
        current = result["current"]
        self.assertEqual(current["recovery_result"]["after"]["latency_p95_ms"], 14.69)
        self.assertEqual(current["recovery_result"]["recovery_time_seconds"], 355.8)
        self.assertFalse(current["recovery_result"]["slo_restored"])
        self.assertTrue(current["recovery_validation"]["timed_out"])
        self.assertEqual(current["finops"]["chosen_option"], OPTIONS[1])
        self.assertIn("not money", current["finops"]["cost_note"])
        self.assertEqual(current["audit"]["total_events"], 52)
        self.assertEqual(current["audit"]["event_counts"]["RECOVERY_STABILIZING"], 50)
        self.assertEqual(current["evidence"]["selected_log_lines"], 60)
        self.assertEqual(current["evidence"]["baseline"]["thresholds"]["latency_p95_ms_max"], 11.89)
        self.assertNotIn("latency_ms", json.dumps(result), "raw log lines must not be forwarded")

    def test_cost_question_uses_recorded_m6_estimates_without_finops_api(self):
        contexts = {INCIDENT: spike_context(), OTHER: {**spike_context(OTHER), "finops_context": None}}
        with shared_api(contexts) as client:
            tools = CopilotTools(self.repo, Provider("live", client), "real")
            listing = bounded_result(tools.run("get_finops", {"service": "payment-service"}))
            single = bounded_result(tools.run("get_finops", {"incident_id": INCIDENT}))
            absent = tools.run("get_finops", {"incident_id": OTHER})
        self.assertEqual(listing["origin"], "recorded_incidents")
        self.assertEqual([row["incident_id"] for row in listing["records"]], [INCIDENT])
        self.assertEqual(listing["records"][0]["chosen_option"]["estimated_cost_delta"], 0.4594)
        self.assertEqual(single["status"], "ok")
        self.assertEqual(single["records"][0]["options"], OPTIONS)
        self.assertIn("abstract cost units", single["cost_note"])
        self.assertEqual(absent["status"], "not_recorded")
        self.assertEqual(absent["records"], [])

    def test_connected_finops_api_is_still_used_when_configured(self):
        seen = []
        def handler(request):
            seen.append(request.url.path)
            return httpx.Response(200, json={"source": "real", "records": [{"service": "payment-service", "cost": 1}]})
        with patch.dict(os.environ, {"M5_FINOPS_PATH": "/api/finops"}):
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result = CopilotTools(self.repo, Provider("live", client), "real").run("get_finops", {"service": "payment-service"})
        self.assertEqual(seen, ["/api/finops"])
        self.assertEqual(result["records"], [{"service": "payment-service", "cost": 1}])

    def test_unconnected_generic_reads_are_not_offered_to_the_live_model(self):
        names = {item["function"]["name"] for item in tool_definitions("real", "live")}
        self.assertTrue({"get_incident", "get_finops", "get_system_overview"} <= names)
        self.assertFalse({"get_telemetry", "get_logs", "get_deployments"} & names)
        with patch.dict(os.environ, {"M5_LOGS_PATH": "/api/logs"}):
            self.assertIn("get_logs", {item["function"]["name"] for item in tool_definitions("real", "live")})
        self.assertIn("get_telemetry", {item["function"]["name"] for item in tool_definitions("mock", "mock")})

    def test_chat_answers_a_long_incident_from_the_summary(self):
        forwarded = []
        def model(request):
            payload = json.loads(request.content)
            tool_messages = [message for message in payload["messages"] if message["role"] == "tool"]
            if not tool_messages:
                offered = {item["function"]["name"] for item in payload["tools"]}
                self.assertNotIn("get_telemetry", offered)
                return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                        "name": "get_incident", "arguments": json.dumps({"incident_id": INCIDENT})}}]}}]})
            forwarded.append(tool_messages[0]["content"])
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(
                {"answer": "Scaled 1 to 8; p95 fell from 5000 to 14.69 ms but stayed above 11.89 ms, so the SLO was not restored.",
                 "kind": "incident_answer", "evidence_ids": ["T1"]})}}]})
        with patch.dict(os.environ, {"M5_LLM_API_KEY": "test-only", "M5_LLM_BASE_URL": "https://api.openai.com/v1"}):
            with shared_api({INCIDENT: spike_context()}) as shared, httpx.Client(transport=httpx.MockTransport(model)) as llm:
                result = chat(self.repo, ChatRequest(question=f"Explain incident {INCIDENT}", source="real"),
                              llm, Provider("live", shared))
        self.assertEqual(result["mode"], "ai_chat")
        self.assertEqual(result["kind"], "incident_answer")
        self.assertNotIn("too_large", forwarded[0])
        self.assertIn("recovery_time_seconds", forwarded[0])
        self.assertIn("chosen_option", forwarded[0])


if __name__ == "__main__":
    unittest.main()
