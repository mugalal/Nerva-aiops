import pytest
from app.state_machine.states import IncidentStatus
from app.state_machine.machine import transition


def test_valid_transition():
    result = transition(
        IncidentStatus.DETECTED,
        IncidentStatus.CORRELATING
    )

    assert result == IncidentStatus.CORRELATING

def test_invalid_transition():
    with pytest.raises(ValueError):
        transition(
            IncidentStatus.DETECTED,
            IncidentStatus.RESOLVED
        )

def test_execution_requires_approval():
    with pytest.raises(ValueError):
        transition(
            IncidentStatus.AWAITING_APPROVAL,
            IncidentStatus.EXECUTING
        )
def test_execution_with_approval():
    result = transition(
        IncidentStatus.AWAITING_APPROVAL,
        IncidentStatus.EXECUTING,
        approved=True
    )

    assert result == IncidentStatus.EXECUTING

def test_resolved_requires_recovery_validation():
    with pytest.raises(ValueError):
        transition(
            IncidentStatus.VALIDATING,
            IncidentStatus.RESOLVED
        )
def test_resolved_with_recovery_validation():
    result = transition(
        IncidentStatus.VALIDATING,
        IncidentStatus.RESOLVED,
        recovery_validated=True
    )

    assert result == IncidentStatus.RESOLVED
