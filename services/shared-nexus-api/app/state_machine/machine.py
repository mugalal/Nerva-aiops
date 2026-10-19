from .states import IncidentStatus

ALLOWED_TRANSITIONS = {
    # Every pre-decision state may escalate, but only when diagnosis has failed
    # for good: the incident is handed to a human instead of waiting forever.
    IncidentStatus.DETECTED: {
        IncidentStatus.CORRELATING,
        IncidentStatus.ESCALATED,
    },
    IncidentStatus.CORRELATING: {
        IncidentStatus.DIAGNOSING,
        IncidentStatus.ESCALATED,
    },
    IncidentStatus.DIAGNOSING: {
        IncidentStatus.DIAGNOSED,
        IncidentStatus.ESCALATED,
    },
    IncidentStatus.DIAGNOSED: {
        IncidentStatus.ACTION_PROPOSED,
        IncidentStatus.ESCALATED,
    },
    IncidentStatus.ACTION_PROPOSED: {
        IncidentStatus.AWAITING_APPROVAL,
        IncidentStatus.ESCALATED,
    },
    IncidentStatus.AWAITING_APPROVAL: {
        IncidentStatus.EXECUTING,
        IncidentStatus.ESCALATED,
        IncidentStatus.DIAGNOSED,
    },
    IncidentStatus.EXECUTING: {
        IncidentStatus.VALIDATING,
        IncidentStatus.FAILED_REMEDIATION,
        IncidentStatus.ESCALATED,
    },
    IncidentStatus.VALIDATING: {
        IncidentStatus.RESOLVED,
        IncidentStatus.FAILED_REMEDIATION,
    },
    IncidentStatus.FAILED_REMEDIATION: {
        IncidentStatus.ESCALATED,
    },
    IncidentStatus.RESOLVED: set(),
    IncidentStatus.ESCALATED: {
        IncidentStatus.VALIDATING,
    },
}

_PRE_DECISION = {IncidentStatus.DETECTED, IncidentStatus.CORRELATING,
                 IncidentStatus.DIAGNOSING, IncidentStatus.DIAGNOSED}


def can_transition(current_status: IncidentStatus, next_status: IncidentStatus) -> bool:
    return next_status in ALLOWED_TRANSITIONS[current_status]

def transition(
    current_status: IncidentStatus,
    next_status: IncidentStatus,
    approved: bool = False,
    recovery_validated: bool = False,
    reconciled: bool = False,
    outcome_unknown: bool = False,
    diagnosis_failed: bool = False,
) -> IncidentStatus:

    if not can_transition(current_status, next_status):
        raise ValueError(
            f"Invalid transition: {current_status} -> {next_status}"
        )

    if (
        current_status == IncidentStatus.AWAITING_APPROVAL
        and next_status == IncidentStatus.EXECUTING
        and not approved
    ):
        raise ValueError(
            "Cannot move to EXECUTING without approval"
        )

    if (
        current_status == IncidentStatus.VALIDATING
        and next_status == IncidentStatus.RESOLVED
        and not recovery_validated
    ):
        raise ValueError(
            "Cannot move to RESOLVED without successful recovery validation"
        )

    if (
        current_status == IncidentStatus.ESCALATED
        and next_status == IncidentStatus.VALIDATING
        and not reconciled
    ):
        raise ValueError(
            "Cannot move from ESCALATED to VALIDATING without operator reconciliation"
        )

    if (
        current_status == IncidentStatus.EXECUTING
        and next_status == IncidentStatus.ESCALATED
        and not outcome_unknown
    ):
        raise ValueError(
            "EXECUTING escalates directly only when the action outcome is unknown"
        )

    if (
        current_status in _PRE_DECISION
        and next_status == IncidentStatus.ESCALATED
        and not diagnosis_failed
    ):
        raise ValueError(
            "An undiagnosed incident escalates only when diagnosis has failed"
        )

    return next_status
