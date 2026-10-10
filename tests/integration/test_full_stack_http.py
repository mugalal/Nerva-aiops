"""Six production HTTP APIs with controlled M1 inputs and actual PostgreSQL.

The actuator is explicitly mock. This gate proves detection, service handoff,
operator routing, restart durability, measured recovery and memory delivery;
it does not claim live Kubernetes remediation or trained-model accuracy.
"""

from datetime import timedelta
import json
import os
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
import requests

from test_real_http_flow import HttpStack, REPO, utc_now


class FullHttpStack(HttpStack):
    def __init__(self, directory, database_url):
        super().__init__(directory, extra_members=("m2", "m5"))
        self.database_url = database_url

    def start(self):
        self.extra_env.update(
            DATABASE_URL=self.database_url,
            M4_STATE_BACKEND="postgres",
            M4_AUTO_BUILD_DECISIONS="false",
            M4_AUTO_VALIDATE_RECOVERY="false",
            M5_UI_MODE="live",
            M5_MEMORY_BASE_URL=self.urls["m5"],
            M5_SHARED_API_TOKEN=self.approval_token,
            M5_OPERATOR_ID="controlled-test-operator",
        )
        super().start()
        self.start_member("m5", REPO / "services/incident-memory", "app.main:app")
        env = dict(self.env)
        env.update(
            M2_REFERENCE_PATH="none", M2_POLL_ENABLED="false",
            M2_HANDOFF_ENABLED="true", M2_HANDOFF_URL=self.urls["m4"],
            M2_EVIDENCE_PATH=str(self.directory / "m2-evidence.jsonl"),
            M2_HANDOFF_SETTLE_ALERTS="4", M2_CORRELATION_GAP_S="120",
            M2_JSON_LOGGING="off",
            PYTHONPATH=os.pathsep.join(filter(None, (str(REPO), env.get("PYTHONPATH")))),
        )
        self.start_member("m2", REPO / "services/anomaly-engine", "app.main:app",
                          env=env, wait_seconds=60)
        return self

    def rows(self, query, parameters=()):
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            return conn.execute(query, parameters).fetchall()

    def incident_state(self, incident_id):
        return self.rows("SELECT payload FROM incidents WHERE incident_id=%s", (incident_id,))[0]["payload"]

    def outbox(self, incident_id):
        rows = self.rows("SELECT * FROM memory_outbox WHERE incident_id=%s", (incident_id,))
        return rows[0] if rows else None


@pytest.fixture
def full_stack(tmp_path):
    base_url = os.getenv("NEXUS_INTEGRATION_DATABASE_URL")
    if not base_url:
        pytest.fail("NEXUS_INTEGRATION_DATABASE_URL is required for the six-service PostgreSQL integration gate")
    # Isolate records even when CI shares its disposable DB with other tests.
    schema = "full_http_" + uuid4().hex
    with psycopg.connect(base_url) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query))
    query["options"] = (query.get("options", "") + " -csearch_path=" + schema).strip()
    database_url = urlunsplit(parts._replace(query=urlencode(query)))
    stack = FullHttpStack(tmp_path, database_url)
    try:
        yield stack.start()
    finally:
        stack.close()
        with psycopg.connect(base_url) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def wait_until(predicate, *, timeout=30, detail="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.25)
    pytest.fail(f"Timed out waiting for {detail}")


def build_rejected_incident(stack, snapshot, scenario, action):
    """An independent negative case cannot change the M2-created incident."""
    incident_id = "INC-REJECT-" + uuid4().hex
    stack.request("POST", "m4", "/api/incidents/", json={
        "incident_id": incident_id, "severity": "high", "affected_services": ["payment-service"]})
    stack.request("POST", "m4", "/internal/anomalies", params={"incident_id": incident_id}, json={
        "anomaly_id": "AN-REJECT-" + uuid4().hex, "timestamp": snapshot["timestamp"],
        "service": snapshot["service"], "score": 0.95, "severity": "high",
        "model": "controlled-http-negative-case",
        "features": {key: snapshot["metrics"][key]
                     for key in ("request_rate", "latency_p95_ms", "http_5xx_rate", "cpu")}})
    assert stack.request("POST", "m4", f"/internal/decisions/build/{incident_id}").json()["action"] == action
    rejected = stack.request("POST", "m5", f"/internal/ui/incidents/{incident_id}/approval",
                             json={"decision": "reject"}).json()
    assert rejected == {"incident_id": incident_id, "status": "ESCALATED", "approved": False}
    stack.request("GET", "m4", f"/api/incidents/{incident_id}/actions/latest", expected=404)
    stack.request("POST", "m5", f"/internal/ui/incidents/{incident_id}/approval",
                  json={"decision": "approve"}, expected=409)
    bundle = stack.request("GET", "m5", f"/internal/ui/incidents/{incident_id}").json()
    assert bundle["rca"]["root_cause"] == scenario and bundle["action_result"] is None
    assert bundle["recovery_result"] is None and bundle["incident"]["status"] == "ESCALATED"
    assert not stack.rows("SELECT * FROM actions WHERE incident_id=%s", (incident_id,))
    assert stack.rows("SELECT approved FROM approvals WHERE incident_id=%s", (incident_id,)) == [{"approved": False}]
    assert "ACTION_EXECUTED" not in {record["event_type"] for record in bundle["audit"]}
    return incident_id


@pytest.mark.parametrize("scenario,phase,action", [
    ("faulty_deployment", "fault", "ROLLBACK"),
    ("traffic_spike", "spike", "SCALE"),
])
def test_full_stack_postgres_restart_recovery_and_archive(full_stack, scenario, phase, action):
    stack = full_stack
    assert stack.request("GET", "m4", "/ready").json()["workflow_storage"] == {
        "backend": "postgres", "durable": True, "error": None}
    assert stack.request("GET", "m5", "/ready").json()["storage"] == "postgresql"
    assert stack.request("GET", "m5", "/internal/ui/config").json()["approval_enabled"] is True
    assert stack.request("GET", "m5", "/").headers["content-type"].startswith("text/html")
    assert stack.request("GET", "m2", "/health").json()["detector"] == "threshold_baseline"

    end = utc_now() - timedelta(seconds=20)
    baseline = stack.request("POST", "m1", "/internal/baselines/measure", json={
        "service": "payment-service", "start": (end - timedelta(seconds=75)).isoformat(),
        "end": end.isoformat(), "step_seconds": 15}).json()
    assert baseline["sample_count"] == 6
    healthy = stack.request("GET", "m1", "/internal/telemetry/snapshot",
                            params={"service": "payment-service"}).json()
    assert stack.request("POST", "m2", "/internal/anomalies/evaluate", json=healthy).json()["score"] == 0
    assert stack.request("GET", "m4", "/api/incidents/").json() == []
    assert stack.request("GET", "m5", "/internal/ui/overview").json()["incidents"] == []

    injection_time = utc_now().isoformat()
    stack.request("POST", "m1", "/__test/state", json={"phase": phase})
    events = []
    for _ in range(4):
        snapshot = stack.request("GET", "m1", "/internal/telemetry/snapshot",
                                 params={"service": "payment-service"}).json()
        event = stack.request("POST", "m2", "/internal/anomalies/evaluate", json=snapshot).json()
        assert event["score"] >= 0.1 and event["model"] == "threshold_baseline"
        events.append(event)
    (candidate,) = stack.request("GET", "m2", "/internal/correlation/incidents").json()
    incident_id = candidate["incident_id"]
    assert candidate["anomaly_ids"] == [event["anomaly_id"] for event in events]
    detail = stack.request("GET", "m2", f"/internal/correlation/incidents/{incident_id}").json()
    assert detail["alert_count"] == 4 and detail["handoff"]["incident_created"] is True
    assert detail["handoff"]["error"] is None
    stored_anomaly = stack.request("GET", "m4", "/internal/anomalies",
                                   params={"incident_id": incident_id}).json()
    assert stored_anomaly == events[0]

    decision = stack.request("POST", "m4", f"/internal/decisions/build/{incident_id}").json()
    assert decision["action"] == action
    proposal = stack.request("GET", "m4", f"/api/incidents/{incident_id}/proposal").json()
    assert proposal["recommended_action"] == action
    assert proposal["parameters"] == ({"from_version": "v2", "to_version": "v1"}
                                       if action == "ROLLBACK" else {"replicas": 9})
    run_id = "RUN-FULL-HTTP-" + uuid4().hex
    stack.request("POST", "m4", f"/api/incidents/{incident_id}/experiment", json={
        "run_id": run_id, "scenario": "bad_deployment" if action == "ROLLBACK" else "traffic_spike",
        "injection_time": injection_time, "injection_evidence": "controlled M1 fixture state endpoint"})
    (listed,) = stack.request("GET", "m5", "/internal/ui/overview").json()["incidents"]
    assert listed["incident_id"] == incident_id and listed["status"] == "AWAITING_APPROVAL"
    bundle = stack.request("GET", "m5", f"/internal/ui/incidents/{incident_id}").json()
    assert bundle["source"] == "mixed" and bundle["decision"] == proposal
    assert bundle["anomaly"] == stored_anomaly and bundle["rca"]["root_cause"] == scenario
    assert bundle["action_result"] is None and bundle["recovery_result"] is None
    assert bundle["evidence"]["baseline"] == baseline
    assert set(bundle["provider_modes"].values()) == {"real", "mock"}

    rejected_id = build_rejected_incident(stack, snapshot, scenario, action)
    for headers in ({}, {"Authorization": "Bearer invalid-test-token"}):
        stack.request("POST", "m4", f"/api/incidents/{incident_id}/approve", headers=headers, expected=401)
        stack.request("POST", "m4", f"/api/incidents/{incident_id}/reject", headers=headers, expected=401)
    assert stack.request("GET", "m4", f"/api/incidents/{incident_id}").json()["status"] == "AWAITING_APPROVAL"
    stack.restart_member("m4")
    restored = stack.request("GET", "m5", f"/internal/ui/incidents/{incident_id}").json()
    assert restored["incident"] == bundle["incident"] and restored["anomaly"] == stored_anomaly
    assert restored["decision"] == proposal and restored["rca"] == bundle["rca"]

    result = stack.request("POST", "m5", f"/internal/ui/incidents/{incident_id}/approval",
                           json={"decision": "approve"}).json()
    assert result["incident_id"] == incident_id and result["action"] == action and result["status"] == "SUCCESS"
    executing = stack.request("GET", "m4", f"/api/incidents/{incident_id}/context").json()
    assert executing["incident"]["status"] == "VALIDATING"
    assert executing["workflow"]["action_completed_at"] == result["completed_at"]
    deadline = executing["workflow"]["recovery_deadline_at"]
    assert deadline is not None
    stack.restart_member("m4")
    restored = stack.request("GET", "m4", f"/api/incidents/{incident_id}/context").json()
    assert restored["decision"] == proposal and restored["action_result"] == result
    assert restored["workflow"]["recovery_deadline_at"] == deadline
    assert stack.request("POST", "m5", f"/internal/ui/incidents/{incident_id}/approval",
                         json={"decision": "approve"}).json() == result
    assert len(stack.rows("SELECT * FROM actions WHERE incident_id=%s", (incident_id,))) == 1
    assert stack.rows("SELECT approved FROM approvals WHERE incident_id=%s", (incident_id,)) == [{"approved": True}]

    pending = stack.request("POST", "m4", f"/internal/recovery/validate/{incident_id}", expected=202)
    assert pending.json()["detail"]["retryable"] is True
    stack.stop_member("m5")
    stack.request("POST", "m1", "/__test/state", json={
        "phase": "rolled_back" if action == "ROLLBACK" else "scaled",
        "replicas": 1 if action == "ROLLBACK" else proposal["parameters"]["replicas"]})

    def recovered():
        response = requests.post(stack.urls["m4"] + f"/internal/recovery/validate/{incident_id}", timeout=20)
        assert response.status_code in {200, 202}, response.text
        if response.status_code == 200:
            assert response.json()["status"] == "RESOLVED"
            return response.json()
    wait_until(recovered, detail="measured M1 SLO recovery")
    recovery = stack.request("GET", "m1", f"/internal/recovery/{incident_id}").json()
    assert recovery["result"]["recovered"] is True and recovery["result"]["slo_restored"] is True
    assert recovery["action_completed_at"] == result["completed_at"]
    assert len(recovery["measurement_window"]["points"]) == 3
    assert all(point["metrics"]["latency_p95_ms"] <= baseline["thresholds"]["latency_p95_ms_max"]
               for point in recovery["measurement_window"]["points"])
    failed_delivery = wait_until(lambda: (job if (job := stack.outbox(incident_id)) and job["attempts"] >= 1
                                          and job["last_error"] else None), detail="retained M5 outage retry")
    assert failed_delivery["status"] == "PENDING" and failed_delivery["delivered_at"] is None
    assert failed_delivery["payload"]["source"] == "mock"
    assert failed_delivery["payload"]["memory"]["tags"] == ["source:mixed"]
    stack.restart_member("m4")
    restored_job = stack.outbox(incident_id)
    assert restored_job["job_id"] == failed_delivery["job_id"]
    assert restored_job["payload"] == failed_delivery["payload"] and restored_job["status"] == "PENDING"
    assert restored_job["attempts"] >= failed_delivery["attempts"]
    assert stack.request("GET", "m4", f"/api/incidents/{incident_id}/context").json()["workflow"]["recovery_deadline_at"] == deadline
    stack.restart_member("m5")
    delivered = wait_until(lambda: (job if (job := stack.outbox(incident_id)) and job["status"] == "DELIVERED"
                                    else None), detail="acknowledged M5 archive after restart")
    assert delivered["attempts"] > failed_delivery["attempts"] and delivered["last_error"] is None
    assert delivered["delivered_at"] is not None and delivered["payload"] == failed_delivery["payload"]
    archived = stack.request("GET", "m5", f"/internal/memory/{incident_id}", params={"source": "mock"}).json()["record"]
    assert archived["memory"] == delivered["payload"]["memory"] and archived["resolved"] is True
    for key, value in delivered["payload"]["context"].items():
        assert archived["context"][key] == value
    for headers in ({}, {"Authorization": "Bearer invalid-test-token"}):
        stack.request("POST", "m5", "/internal/memory/store", json=delivered["payload"],
                      headers=headers, expected=401)
    acknowledgement = stack.request("POST", "m5", "/internal/memory/store", json=delivered["payload"],
                                    headers={"Authorization": "Bearer " + stack.approval_token,
                                             "Idempotency-Key": delivered["job_id"]}).json()
    assert acknowledgement == {"status": "already_stored", "record": archived}
    stack.request("GET", "m5", f"/internal/memory/{incident_id}", params={"source": "real"}, expected=404)
    assert stack.request("POST", "m5", "/internal/memory/search",
                         json={"source": "real", "service": "payment-service"}).json()["results"] == []
    stack.restart_member("m5")
    final = stack.request("GET", "m5", f"/internal/ui/incidents/{incident_id}").json()
    assert final["incident"]["status"] == "RESOLVED" and final["workflow"]["archive_status"] == "DELIVERED"
    assert final["archive"]["source"] == "mock" and final["archive"]["resolved"] is True
    assert final["action_result"] == result and final["memory"] == archived["memory"]
    assert final["recovery_result"] == recovery["result"]
    assert final["anomaly"] == stored_anomaly and final["decision"] == proposal
    answer = stack.request("POST", "m5", "/internal/copilot/query", json={
        "incident_id": incident_id, "source": "mock", "question": "Did it work?"}).json()
    assert answer["status"] == "ok" and answer["citations"]
    for citation in answer["citations"]:
        value = archived
        for key in citation["field"].split("."):
            value = value[key]
        assert citation["source"] == "mock" and citation["incident_id"] == incident_id and citation["value"] == value

    (experiment,) = stack.rows("SELECT * FROM experiment_runs WHERE run_id=%s", (run_id,))
    assert experiment["scenario"] == ("bad_deployment" if action == "ROLLBACK" else "traffic_spike")
    assert experiment["incident_id"] == incident_id and experiment["rca_correct"] is True
    assert experiment["injection_time"] <= experiment["detection_time"] <= experiment["action_time"] <= experiment["recovery_time"]
    assert experiment["cost_slo_effect"]["incident_status"] == "RESOLVED"
    assert experiment["cost_slo_effect"]["recovery"]["success"] is True
    for table in ("anomalies", "rca_results", "decision_proposals", "approvals", "actions", "recovery_results", "timeline_events"):
        assert stack.rows(sql.SQL("SELECT * FROM {} WHERE incident_id=%s").format(sql.Identifier(table)), (incident_id,))
    rows = stack.rows("SELECT source,payload FROM m5_incident_memory")
    assert rows and all(row["source"] == "mock" for row in rows)
    assert len([row for row in rows if json.loads(row["payload"])["memory"]["incident_id"] == incident_id]) == 1
    assert not stack.rows("SELECT * FROM actions WHERE incident_id=%s", (rejected_id,))
