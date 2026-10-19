"""Append-only, hash-chained audit trail.

Every record carries `prev_hash` (the previous record's `hash`) and `hash`, a
SHA-256 over its own canonical JSON. With NEXUS_AUDIT_HMAC_KEY set the hash is
an HMAC, so someone who can write the database but does not hold the key
cannot recompute a consistent chain. `verify_chain` detects an edited record
and a record removed from the middle. It cannot detect records cut off the end
of the trail; an exported copy kept elsewhere covers that.
"""
import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4
from app.db import workflow_store

audit_records = []
_chain_lock = Lock()


def _key():
    key = os.getenv("NEXUS_AUDIT_HMAC_KEY")
    return key.encode("utf-8") if key else None


def _canonical(record):
    body = {name: value for name, value in record.items() if name != "hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def record_hash(record, key=None):
    if record.get("hash_alg") == "hmac-sha256":
        if key is None:
            return None  # Cannot be checked without the key.
        return hmac.new(key, _canonical(record), hashlib.sha256).hexdigest()
    return hashlib.sha256(_canonical(record)).hexdigest()


def add_audit_record(
    incident_id: str,
    event_type: str,
    details: dict
):
    key = _key()
    with _chain_lock:
        record = {
            "event_id": "AUD-" + uuid4().hex,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "incident_id": incident_id,
            "event_type": event_type,
            "details": details,
            "prev_hash": audit_records[-1].get("hash") if audit_records else None,
            "hash_alg": "hmac-sha256" if key else "sha256",
        }
        record["hash"] = record_hash(record, key)

        workflow_store.save_audit(record)
        audit_records.append(record)

    return record


def verify_chain(records=None):
    """Check every chained record's hash and its link to the record before it."""
    key = _key()
    records = list(audit_records if records is None else records)
    problems, unchained, unverifiable, previous = [], 0, 0, None
    for index, record in enumerate(records):
        if "hash" not in record:
            unchained += 1  # Written before the chain existed.
            previous = None
            continue
        expected = record_hash(record, key)
        if expected is None:
            unverifiable += 1
        elif not hmac.compare_digest(expected, record["hash"]):
            problems.append({"index": index, "event_id": record.get("event_id"), "problem": "content does not match its hash"})
        if previous is not None and record.get("prev_hash") != previous:
            problems.append({"index": index, "event_id": record.get("event_id"),
                             "problem": "does not link to the previous record (a record was removed or reordered)"})
        previous = record["hash"]
    return {"valid": not problems, "records": len(records), "chained": len(records) - unchained,
            "legacy_unchained": unchained, "unverifiable_without_key": unverifiable, "problems": problems,
            "head_hash": previous}
