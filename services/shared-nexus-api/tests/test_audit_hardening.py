from datetime import datetime, timezone
import hashlib
import json
from unittest.mock import Mock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.incidents import create_incident, find_incident, incidents, IncidentCreate, lock_for
from app.api.approvals import proposal_digest
from app.decision_engine.models import DecisionAction
from app.orchestration.lifecycle import work_once
from app.remediation.executor import RolloutUnconfirmed
from app.providers.resilient import Breaker, CircuitOpen, make_session
from app.remediation.audit import audit_records

client = TestClient(app)


def test_proposal_digest_matching_and_mismatch():
    proposal = {"action": "SCALE", "target": "payment-service", "replicas": 4}
    digest = proposal_digest(proposal)
    assert isinstance(digest, str) and len(digest) == 64

    # Mismatched proposal should have different digest
    tampered = {"action": "SCALE", "target": "payment-service", "replicas": 8}
    assert proposal_digest(tampered) != digest


def test_approve_rejects_tampered_proposal_digest(monkeypatch):
    incident_id = "INC-DIGEST-TEST"
    create_incident(IncidentCreate(incident_id=incident_id, severity="high", affected_services=["payment-service"]))
    incident = find_incident(incident_id)
    proposal = {"incident_id": incident_id, "recommended_action": "SCALE", "target": "payment-service"}
    incident["_proposal"] = proposal
    incident["proposed_action"] = "SCALE"
    incident["scale_option"] = {"replicas": 3}
    incident["status"] = "AWAITING_APPROVAL"

    # With correct digest
    valid_digest = proposal_digest(proposal)
    # With wrong digest -> 409
    resp = client.post(f"/api/incidents/{incident_id}/approve", headers={"x-proposal-digest": "wrong_sha256_digest"})
    assert resp.status_code == 409
    assert "The proposal changed since review" in resp.json()["detail"]


def test_circuit_breaker_trips_and_recovers():
    breaker = Breaker(threshold=3, cooldown=0.1)
    assert breaker.allow() is True

    # Record 3 failures
    breaker.record(False)
    breaker.record(False)
    breaker.record(False)

    # Breaker should now be open
    assert breaker.allow() is False

    # After cooldown, probe should be allowed
    import time
    time.sleep(0.15)
    assert breaker.allow() is True

    # A success resets failure count
    breaker.record(True)
    assert breaker.allow() is True

    # Second outage: must trip again (F3 fix)
    breaker.record(False)
    breaker.record(False)
    breaker.record(False)
    assert breaker.allow() is False
    time.sleep(0.15)
    assert breaker.allow() is True


def test_rollout_unconfirmed_audit_type():
    from app.orchestration import orchestrator
    incident_id = "INC-ROLLOUT-FAIL"
    create_incident(IncidentCreate(incident_id=incident_id, severity="high", affected_services=["payment-service"]))
    incident = find_incident(incident_id)
    incident["status"] = "AWAITING_APPROVAL"
    incident["proposed_action"] = "SCALE"
    incident["scale_option"] = {"replicas": 2}
    incident["_proposal"] = {"action": "SCALE"}

    def fail_rollout(*args, **kwargs):
        raise RolloutUnconfirmed("Timeout waiting for rollout")

    with patch("app.orchestration.orchestrator.execute_remediation", fail_rollout):
        res = orchestrator.execute_approved_action(incident_id, DecisionAction.SCALE, approved=True, replicas=2)
        assert res.success is True

    # Status must advance to VALIDATING so M1 can measure recovery
    assert incident["status"] == "VALIDATING"

    # Audit records must record ACTION_APPLIED_ROLLOUT_UNCONFIRMED
    records = [r for r in audit_records if r["incident_id"] == incident_id]
    unconfirmed = [r for r in records if r["event_type"] == "ACTION_APPLIED_ROLLOUT_UNCONFIRMED"]
    assert len(unconfirmed) >= 1
