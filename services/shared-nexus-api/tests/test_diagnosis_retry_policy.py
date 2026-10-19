from unittest.mock import patch

import pytest

from app.api.incidents import (
    IncidentCreate,
    create_incident,
    find_incident,
)
from app.orchestration.lifecycle import _diagnose
from app.providers.errors import IntegrationError


@pytest.mark.parametrize(
    "retryable",
    [False, True],
)
def test_diagnosis_retry_policy(retryable):
    incident_id = f"INC-RETRY-POLICY-{retryable}"

    create_incident(
        IncidentCreate(
            incident_id=incident_id,
            severity="high",
            affected_services=["payment-service"],
        )
    )

    incident = find_incident(incident_id)
    incident["status"] = "DIAGNOSING"
    incident["_diagnosis_pending"] = True

    error = IntegrationError(
        "M3 RCA request failed with status 502",
        status_code=503,
        retryable=retryable,
    )

    with patch(
        "app.orchestration.orchestrator.build_decision",
        side_effect=error,
    ):
        _diagnose(incident_id)

    updated = find_incident(incident_id)

    assert updated["_diagnosis_pending"] is retryable
    assert updated["_diagnosis_last_error"] == str(error)

    if retryable:
        assert updated.get("_diagnosis_retry_at") is not None
        assert updated["status"] == "DIAGNOSING"
    else:
        assert updated.get("_diagnosis_retry_at") is None
        assert updated.get("_diagnosis_attempt") is None
        # Never left in DIAGNOSING with nothing scheduled: a human takes over.
        assert updated["status"] == "ESCALATED"
        assert updated["_diagnosis_failed"] is True


def test_repeated_retryable_failures_escalate_at_the_attempt_limit(monkeypatch):
    from app.remediation.audit import audit_records
    monkeypatch.setenv("M4_DIAGNOSIS_MAX_ATTEMPTS", "3")
    incident_id = "INC-RETRY-LIMIT"
    create_incident(IncidentCreate(incident_id=incident_id, severity="high", affected_services=["payment-service"]))
    incident = find_incident(incident_id)
    incident["status"] = "DIAGNOSING"
    incident["_diagnosis_pending"] = True
    error = IntegrationError("M3 unreachable", status_code=503, retryable=True)
    with patch("app.orchestration.orchestrator.build_decision", side_effect=error):
        for _ in range(2):
            _diagnose(incident_id)
            assert find_incident(incident_id)["status"] == "DIAGNOSING"
        _diagnose(incident_id)
    updated = find_incident(incident_id)
    assert updated["status"] == "ESCALATED"
    assert updated["_diagnosis_pending"] is False
    failed = [r for r in audit_records if r["incident_id"] == incident_id and r["event_type"] == "DIAGNOSIS_FAILED"]
    assert failed and failed[-1]["details"]["attempts"] == 3


def test_pre_decision_escalation_requires_a_failed_diagnosis():
    from app.state_machine.machine import transition
    from app.state_machine.states import IncidentStatus
    with pytest.raises(ValueError):
        transition(IncidentStatus.DIAGNOSING, IncidentStatus.ESCALATED)
    assert transition(IncidentStatus.DIAGNOSING, IncidentStatus.ESCALATED,
                      diagnosis_failed=True) == IncidentStatus.ESCALATED