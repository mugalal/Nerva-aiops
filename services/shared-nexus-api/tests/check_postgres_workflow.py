"""Explicit real PostgreSQL verification with a controlled mock actuator.

Run with DATABASE_URL set to a dedicated verification database/schema. This is
not a Kubernetes recovery proof, and it does not claim measured injection time.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from uuid import uuid4

for setting in ("RCA_PROVIDER", "EVIDENCE_PROVIDER", "FINOPS_PROVIDER", "RECOVERY_PROVIDER", "REMEDIATION_BACKEND"):
    os.environ[setting] = "mock"
os.environ["M4_STATE_BACKEND"] = "postgres"
os.environ["M4_AUTO_BUILD_DECISIONS"] = "false"
os.environ["M4_AUTO_VALIDATE_RECOVERY"] = "false"

from fastapi.testclient import TestClient
from app.main import app
from app.api.incidents import find_incident, persist_incidents
from app.db.connection import connect
from app.db.export_experiments import build_rows
from app.db.repository import get_records
from app.db import workflow_store


def main():
    identifier = "INC-PG-" + uuid4().hex
    headers = {"Authorization": "Bearer " + (os.getenv("NEXUS_APPROVAL_TOKEN") or os.getenv("M5_SHARED_API_TOKEN") or ""),
               "X-Nexus-Approver": "controlled-postgres-verifier"}
    with TestClient(app) as client:
        assert client.post("/api/incidents/", json={"incident_id": identifier, "severity": "high",
               "affected_services": ["payment-service"]}).status_code == 200
        event = {"anomaly_id": "A-" + uuid4().hex, "timestamp": datetime.now(timezone.utc).isoformat(),
                 "service": "payment-service", "score": 0.94, "severity": "high", "model": "controlled-verification",
                 "features": {"latency_p95_ms": 850}}
        assert client.post("/internal/anomalies", params={"incident_id": identifier}, json=event).status_code == 200
        assert client.post("/internal/decisions/build/" + identifier).status_code == 200
        assert client.post("/api/incidents/" + identifier + "/experiment", headers=headers,
               json={"run_id": "RUN-" + identifier, "scenario": "bad_deployment"}).status_code == 200
        assert client.post("/api/incidents/" + identifier + "/approve", headers=headers).status_code == 200
        original = find_incident(identifier)
        expected_action = original["_action_result"]
        expected_deadline = original["recovery_deadline_at"]

    # A separate interpreter loads retained PostgreSQL state and reads the APIs.
    child = """
import json,sys
from fastapi.testclient import TestClient
from app.main import app
from app.api.incidents import find_incident
from app.remediation.audit import audit_records
with TestClient(app) as client:
    incident=find_incident(sys.argv[1])
    assert incident['status']=='VALIDATING'
    context=client.get('/api/incidents/'+sys.argv[1]+'/context').json()
    assert context['anomaly']['anomaly_id'] in incident['anomaly_ids']
    print(json.dumps({'action':incident['_action_result'],'deadline':incident['recovery_deadline_at'],
                      'anomaly_retained':True,'audit_retained':any(r['incident_id']==sys.argv[1] for r in audit_records)}))
"""
    result = subprocess.run([sys.executable, "-c", child, identifier], check=True, capture_output=True, text=True, env=os.environ.copy())
    restored = json.loads(result.stdout.strip())
    assert restored["action"] == expected_action and restored["deadline"] == expected_deadline
    assert restored["audit_retained"]
    with TestClient(app) as client:
        assert client.post("/internal/recovery/validate/" + identifier).status_code == 200
        incident = find_incident(identifier)
        assert incident["status"] == "RESOLVED"
        assert incident["_archive_outbox"]["payload"]["source"] == "mock"
        assert get_records("incidents", incident_id=identifier)[0]["status"] == "RESOLVED"
        tables = {table: len(get_records(table, incident_id=identifier)) for table in (
            "anomalies", "rca_results", "decision_proposals", "approvals", "actions", "recovery_results", "timeline_events", "memory_outbox")}
        assert all(count > 0 for count in tables.values())
        rows = build_rows(get_records("experiment_runs", incident_id=identifier))
        assert rows[0]["injection_time"] is None and rows[0]["mttd_seconds"] is None
        assert rows[0]["mttr_seconds"] >= 0
        # Persist an unknown intent without executing it; restart refuses replay.
        ambiguous = "INC-PG-AMBIGUOUS-" + uuid4().hex
        assert client.post("/api/incidents/", json={"incident_id": ambiguous, "severity": "high",
               "affected_services": ["payment-service"]}).status_code == 200
        uncertain = find_incident(ambiguous)
        uncertain.update(status="EXECUTING", _execution_intent={"action_id": "ACT-" + uuid4().hex,
            "incident_id": ambiguous, "action": "ROLLBACK", "started_at": datetime.now(timezone.utc).isoformat(), "status": "INTENT"})
        persist_incidents()
    with TestClient(app) as client:
        uncertain = find_incident(ambiguous)
        assert uncertain["status"] == "ESCALATED" and uncertain["_execution_ambiguous"]
        assert client.post("/internal/remediation/execute", headers=headers, json={"incident_id": ambiguous,
               "action": "ROLLBACK", "approved": True}).status_code == 409
    print(json.dumps({"source": "controlled-postgres-restart", "incident_id": identifier,
        "database": "postgresql", "actuator": "mock", "restart_in_separate_process": True,
        "completed_action_retained": True, "recovery_deadline_retained": True,
        "ambiguous_intent_not_repeated": True, "normalized_rows": tables,
        "experiment_mttd": None, "experiment_mttr_seconds": rows[0]["mttr_seconds"],
        "injection_time_invented": False}, indent=2))


if __name__ == "__main__":
    main()
