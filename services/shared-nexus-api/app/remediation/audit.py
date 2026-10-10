import json
import os
from pathlib import Path
from datetime import datetime, timezone

audit_records = []

_AUDIT_PATH = Path(os.getenv("M4_AUDIT_FILE", "/data/audit_log.json") if os.path.exists("/data") else "./data/audit_log.json")

def _load_audit():
    try:
        if _AUDIT_PATH.exists():
            data = json.loads(_AUDIT_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                audit_records.extend(data)
    except Exception:
        pass

def _persist_audit():
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _AUDIT_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(audit_records, indent=2), encoding="utf-8")
        tmp.replace(_AUDIT_PATH)
    except Exception:
        pass

_load_audit()

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
    _persist_audit()

    return record