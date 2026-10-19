"""Anti-flapping: one problem on one service is one incident, not one per alert burst."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.incidents import find_incident, persist_incidents
from app.main import app
from app.remediation.audit import audit_records

client = TestClient(app)


@pytest.fixture(autouse=True)
def coalescing(monkeypatch):
    monkeypatch.setenv("M4_COALESCE_INCIDENTS", "true")
    monkeypatch.setenv("M4_INCIDENT_COOLDOWN_SECONDS", "300")


def _create(incident_id, service="payment-service"):
    return client.post("/api/incidents/", json={"incident_id": incident_id, "severity": "high",
                                                "affected_services": [service]})


def _anomaly(anomaly_id, service="payment-service"):
    return {"anomaly_id": anomaly_id, "timestamp": datetime.now(timezone.utc).isoformat(), "service": service,
            "score": 0.9, "severity": "high", "model": "zscore", "features": {"latency_p95_ms": 900.0}}


def _close(incident_id, status="RESOLVED", closed_ago=0):
    incident = find_incident(incident_id)
    incident["status"] = status
    incident["_closed_at"] = (datetime.now(timezone.utc) - timedelta(seconds=closed_ago)).isoformat()
    persist_incidents()


def _events(kind):
    return [r for r in audit_records if r["event_type"] == kind]


def test_second_detection_while_open_joins_the_open_incident():
    assert _create("INC-A").status_code == 200
    response = _create("INC-B")
    assert response.status_code == 200
    assert response.json()["incident_id"] == "INC-A"
    assert find_incident("INC-B") is None
    assert _events("INCIDENT_COALESCED")[-1]["details"]["duplicate_incident_id"] == "INC-B"

    # Anomalies M2 sends under the alias are linked to the incident it joined.
    assert client.post("/internal/anomalies?incident_id=INC-B", json=_anomaly("AN-1")).status_code == 200
    assert find_incident("INC-A")["anomaly_ids"] == ["AN-1"]
    assert find_incident("INC-A")["_diagnosis_pending"] is True
    assert client.get("/api/incidents/INC-B").json()["incident_id"] == "INC-A"
    # A repeated create of the alias is idempotent.
    assert _create("INC-B").json()["incident_id"] == "INC-A"


def test_other_services_are_never_coalesced():
    _create("INC-PAY")
    assert _create("INC-CART", service="cart-service").json()["incident_id"] == "INC-CART"


def test_alerts_inside_the_cooldown_are_recorded_but_not_acted_on():
    _create("INC-A")
    _close("INC-A")
    assert _create("INC-FLAP").json()["incident_id"] == "INC-A"
    assert client.post("/internal/anomalies?incident_id=INC-FLAP", json=_anomaly("AN-FLAP")).status_code == 200
    resolved = find_incident("INC-A")
    assert resolved["status"] == "RESOLVED"
    assert resolved["_recurrences"] == 1
    assert not resolved.get("_diagnosis_pending")
    assert _events("INCIDENT_COALESCED")[-1]["details"]["reason"] == "cooldown"


def test_after_the_cooldown_a_new_detection_is_a_new_incident():
    _create("INC-A")
    _close("INC-A", closed_ago=301)
    assert _create("INC-LATER").json()["incident_id"] == "INC-LATER"


def test_an_alias_that_keeps_alerting_past_the_cooldown_becomes_its_own_incident():
    _create("INC-A")
    _close("INC-A")
    _create("INC-PERSIST")
    _close("INC-A", closed_ago=301)  # the cooldown has now elapsed
    assert client.post("/internal/anomalies?incident_id=INC-PERSIST", json=_anomaly("AN-LATE")).status_code == 200
    reopened = find_incident("INC-PERSIST")
    assert reopened is not None and reopened["status"] == "DETECTED"
    assert reopened["anomaly_ids"] == ["AN-LATE"] and reopened["_diagnosis_pending"] is True
    assert "AN-LATE" not in find_incident("INC-A")["anomaly_ids"]
    assert _events("INCIDENT_OPENED_AFTER_COOLDOWN")[-1]["incident_id"] == "INC-PERSIST"


def test_closed_at_is_stamped_on_terminal_states_and_cleared_on_reopen():
    _create("INC-A")
    incident = find_incident("INC-A")
    incident["status"] = "ESCALATED"
    persist_incidents()
    assert find_incident("INC-A")["_closed_at"]
    incident["status"] = "VALIDATING"  # operator reconciliation reopens validation
    persist_incidents()
    assert "_closed_at" not in find_incident("INC-A")


def test_disabled_coalescing_keeps_independent_incidents(monkeypatch):
    monkeypatch.setenv("M4_COALESCE_INCIDENTS", "false")
    _create("INC-A")
    assert _create("INC-B").json()["incident_id"] == "INC-B"
