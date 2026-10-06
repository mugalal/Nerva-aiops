from app.providers.rca_provider import get_rca_result
from app.providers.finops_provider import get_finops_context
from app.decision_engine.engine import decide_from_rca
from app.api.incidents import update_incident_status
from app.state_machine.states import IncidentStatus
from app.remediation.executor import execute_remediation
from app.remediation.audit import add_audit_record
from app.providers.recovery_provider import validate_recovery


def build_decision(incident_id: str):
    rca = get_rca_result()
    finops = get_finops_context()

    result = decide_from_rca(
        rca,
        finops
    )
    add_audit_record(
        incident_id,
        "DECISION_PROPOSED",
        {
            "action": result.action.value,
            "reason": result.reason,
            "confidence": result.confidence,
            "approval_required": result.approval_required,
            "scale_option": result.scale_option,
        }
)

    update_incident_status(
        incident_id,
        IncidentStatus.ACTION_PROPOSED
    )
    update_incident_status(
        incident_id,
        IncidentStatus.AWAITING_APPROVAL
    )

    return result

def execute_approved_action(
    incident_id: str,
    action,
    approved: bool,
    replicas: int | None = None
):
    update_incident_status(
        incident_id,
        IncidentStatus.EXECUTING,
        approved=approved
    )
    add_audit_record(
        incident_id,
        "ACTION_APPROVED",
        {
            "action": action.value,
            "replicas": replicas,
        }
    )
    try:
        result = execute_remediation(
            action,
            approved=approved,
            replicas=replicas
        )
    except ValueError as exc:   
        add_audit_record(
            incident_id,
            "ACTION_EXECUTION_FAILED",
            {
                "action": action.value,
                "error": str(exc),
            }
        )
        update_incident_status(
            incident_id,
            IncidentStatus.FAILED_REMEDIATION
        )
        update_incident_status(
            incident_id,
            IncidentStatus.ESCALATED
        )
        raise
  
    add_audit_record(
        incident_id,
        "ACTION_EXECUTED",
        {
            "action": action.value,
            "success": result.success,
            "message": result.message,
            "execution_id": result.execution_id,
        }
    )
    update_incident_status(
        incident_id,
        IncidentStatus.VALIDATING
    )

    return result

def validate_and_apply_recovery(incident_id: str):
    recovery = validate_recovery(incident_id)

    return apply_recovery_result(
        incident_id,
        recovery["success"]
    )

def apply_recovery_result(
    
    incident_id: str,
    success: bool
):
    add_audit_record(
        incident_id,
        "RECOVERY_VALIDATION",
        {
            "success": success,
        }
    )
    if success:
        return update_incident_status(
            incident_id,
            IncidentStatus.RESOLVED,
            recovery_validated=True
        )

    update_incident_status(
        incident_id,
        IncidentStatus.FAILED_REMEDIATION
    )

    return update_incident_status(
        incident_id,
        IncidentStatus.ESCALATED
    )