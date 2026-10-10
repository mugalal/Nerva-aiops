from .states import IncidentStatus

ALLOWED_TRANSITIONS = {
    IncidentStatus.DETECTED: {
        IncidentStatus.CORRELATING,
    },
    IncidentStatus.CORRELATING: {
        IncidentStatus.DIAGNOSING,
    },
    IncidentStatus.DIAGNOSING: {
        IncidentStatus.DIAGNOSED,
    },
    IncidentStatus.DIAGNOSED: {
        IncidentStatus.ACTION_PROPOSED,
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

def can_transition(current_status: IncidentStatus, next_status: IncidentStatus) -> bool:
    return next_status in ALLOWED_TRANSITIONS[current_status]

def transition(
    current_status: IncidentStatus,
    next_status: IncidentStatus,
    approved: bool = False,
    recovery_validated: bool = False,
    reconciled: bool = False,
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


    return next_status
