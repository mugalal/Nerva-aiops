from datetime import datetime, timezone
from uuid import uuid4
from app.db import workflow_store

audit_records = []

def add_audit_record(
    incident_id: str,
    event_type: str,
    details: dict
):
    record = {
        "event_id": "AUD-" + uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "incident_id": incident_id,
        "event_type": event_type,
        "details": details,
    }

    workflow_store.save_audit(record)
    audit_records.append(record)

    return record
