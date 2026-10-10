import pytest
from threading import Thread, Event
from fastapi.testclient import TestClient
from app.main import app
from app.api.incidents import lock_for, find_incident, incidents, update_incident_status, persist_incidents
from app.state_machine.states import IncidentStatus
from app.remediation.audit import audit_records

client = TestClient(app)


def test_concurrent_incidents_do_not_block_each_other(monkeypatch):
    """F1: With per-incident locking, an active operation on INC-A must not block INC-B."""
    monkeypatch.setenv("NEXUS_APPROVAL_TOKEN", "secret-token")

    # Create both incidents
    res_a = client.post("/api/incidents/", json={
        "incident_id": "INC-CONCUR-A",
        "severity": "high",
        "affected_services": ["payment-service"]
    })
    assert res_a.status_code == 200

    res_b = client.post("/api/incidents/", json={
        "incident_id": "INC-CONCUR-B",
        "severity": "medium",
        "affected_services": ["cart-service"]
    })
    assert res_b.status_code == 200

    a_holding = Event()
    release_a = Event()
    b_finished = Event()
    b_status_code = None

    def hold_a():
        with lock_for("INC-CONCUR-A"):
            a_holding.set()
            assert release_a.wait(timeout=5)

    def operate_b():
        nonlocal b_status_code
        # Try to read context of INC-CONCUR-B while INC-CONCUR-A is locked
        resp = client.get("/api/incidents/INC-CONCUR-B/context")
        b_status_code = resp.status_code
        b_finished.set()

    thread_a = Thread(target=hold_a)
    thread_b = Thread(target=operate_b)

    thread_a.start()
    assert a_holding.wait(timeout=2)

    # Launch operation on INC-B while INC-A is still holding its lock
    thread_b.start()
    assert b_finished.wait(timeout=2), "INC-B was blocked by INC-A's lock!"

    release_a.set()
    thread_a.join(timeout=3)
    thread_b.join(timeout=3)

    assert b_status_code == 200


def test_reconcile_ambiguous_incident_confirmed(monkeypatch):
    """F4: Operator can confirm an ambiguous execution and transition to VALIDATING."""
    monkeypatch.setenv("NEXUS_APPROVAL_TOKEN", "secret-token")

    incident_id = "INC-AMBIGUOUS-CONFIRM"
    client.post("/api/incidents/", json={
        "incident_id": incident_id,
        "severity": "critical",
        "affected_services": ["payment-service"]
    })

    # Simulate ambiguous crash outcome (status=ESCALATED, _execution_ambiguous=True)
    with lock_for(incident_id):
        inc = find_incident(incident_id)
        inc["status"] = "ESCALATED"
        inc["_execution_ambiguous"] = True
        inc["_execution_intent"] = {
            "action_id": "ACT-TEST-123",
            "action": "ROLLBACK",
            "target": "payment-service"
        }
        persist_incidents()

    # Reconcile as CONFIRMED
    resp = client.post(
        f"/api/incidents/{incident_id}/reconcile",
        json={"outcome": "CONFIRMED", "note": "Pods verified healthy via kubectl"},
        headers={"Authorization": "Bearer secret-token"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "VALIDATING"

    # Verify incident state in store
    inc = find_incident(incident_id)
    assert inc.get("_execution_ambiguous") is None
    assert inc["_action_result"]["status"] == "SUCCESS"

    assert any(
        record["event_type"] == "MANUAL_RECONCILIATION_CONFIRMED"
        for record in audit_records if record["incident_id"] == incident_id
    )


def test_reconcile_ambiguous_incident_failed(monkeypatch):
    """F4: Operator can mark ambiguous execution as failed, retaining ESCALATED status."""
    monkeypatch.setenv("NEXUS_APPROVAL_TOKEN", "secret-token")

    incident_id = "INC-AMBIGUOUS-FAIL"
    client.post("/api/incidents/", json={
        "incident_id": incident_id,
        "severity": "critical",
        "affected_services": ["payment-service"]
    })

    # Simulate ambiguous crash outcome
    with lock_for(incident_id):
        inc = find_incident(incident_id)
        inc["status"] = "ESCALATED"
        inc["_execution_ambiguous"] = True
        inc["_execution_intent"] = {
            "action_id": "ACT-TEST-456",
            "action": "ROLLBACK",
            "target": "payment-service"
        }
        persist_incidents()

    # Reconcile as FAILED
    resp = client.post(
        f"/api/incidents/{incident_id}/reconcile",
        json={"outcome": "FAILED", "note": "Rollout timed out and crashed"},
        headers={"Authorization": "Bearer secret-token"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ESCALATED"

    inc = find_incident(incident_id)
    assert inc["_action_result"]["status"] == "FAILED"
    assert any(
        record["event_type"] == "MANUAL_RECONCILIATION_FAILED"
        for record in audit_records if record["incident_id"] == incident_id
    )
