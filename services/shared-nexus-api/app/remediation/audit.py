from datetime import datetime, timezone

audit_records = []


def add_audit_record(
    incident_id: str,
    event_type: str,
    details: dict
):
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "incident_id": incident_id,
        "event_type": event_type,
        "details": details,
    }

    audit_records.append(record)

    return record