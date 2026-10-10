"""Explicit M5 demo fixtures, separate from frozen shared mocks."""
from copy import deepcopy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def mock(name):
    return json.loads((ROOT / "mocks" / f"mock_{name}.json").read_text(encoding="utf-8"))


def demo_records():
    context = {"incident": mock("incident"), "anomaly": mock("anomaly_event"),
               "rca": mock("rca_response"), "decision": mock("decision"),
               "action_result": mock("action_result"), "recovery_result": mock("recovery"),
               "finops_context": mock("finops_context"), "deployment_event": mock("deployment_event")}
    context["incident"]["status"] = "RESOLVED"
    base = {"memory": mock("incident_memory"), "context": context, "source": "mock", "resolved": True}
    traffic = deepcopy(base)
    traffic["memory"].update(incident_id="INC-DEMO-TRAFFIC", incident_type="traffic_spike", root_cause="traffic_spike", action="SCALE", tags=["traffic", "cpu", "latency"])
    for name in ("incident", "rca", "decision", "action_result", "recovery_result"):
        traffic["context"][name]["incident_id"] = "INC-DEMO-TRAFFIC"
    traffic["context"]["rca"].update(root_cause="traffic_spike", affected_component="payment-service", evidence=["Synthetic demo request rate increased without a deployment", "Synthetic demo CPU and latency increased"])
    traffic["context"]["anomaly"]["features"].update(request_rate=1200, cpu=0.95)
    traffic["context"]["anomaly"]["anomaly_id"] = "ANO-DEMO-TRAFFIC"
    traffic["context"]["incident"]["anomaly_ids"] = ["ANO-DEMO-TRAFFIC"]
    traffic["context"]["decision"].update(recommended_action="SCALE", parameters={"replicas": 6}, reason="Synthetic demo demand increase")
    traffic["context"]["action_result"].update(action="SCALE", action_id="ACT-DEMO-TRAFFIC")
    traffic["context"]["deployment_event"] = None
    irrelevant = {"memory": {"incident_id": "INC-DEMO-OTHER", "incident_type": "database_failure", "service": "catalog-service", "root_cause": "database_unavailable", "action": "ESCALATE", "action_success": False, "recovered": False, "tags": ["database"]}, "context": {}, "source": "mock", "resolved": True}
    history = deepcopy(base)
    history["memory"]["incident_id"] = "INC-DEMO-HISTORY"
    for name in ("incident", "rca", "decision", "action_result", "recovery_result"):
        history["context"][name]["incident_id"] = "INC-DEMO-HISTORY"
    history["context"]["anomaly"]["anomaly_id"] = "ANO-DEMO-HISTORY"
    history["context"]["incident"]["anomaly_ids"] = ["ANO-DEMO-HISTORY"]
    for record in history["context"].values():
        if isinstance(record, dict):
            for key in ("timestamp", "started_at", "completed_at"):
                if key in record:
                    record[key] = record[key].replace("2026-09-27", "2026-09-26")
    return [base, traffic, irrelevant, history]


def demo_bundle(incident_id):
    for record in demo_records():
        if record["memory"]["incident_id"] == incident_id:
            return {**record["context"], "memory": record["memory"], "source": "mock"}
    return None
