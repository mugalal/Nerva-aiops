"""Durable workflow state. PostgreSQL is required for real operation.

The SQLite backend is explicit, for controlled local restart tests. Memory is
allowed only in mock mode; it never claims restart durability.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock


class StorageError(RuntimeError):
    pass


_lock = RLock()
_last_error = None
_writer_lease = None


def backend():
    return os.getenv("M4_STATE_BACKEND", "postgres" if os.getenv("DATABASE_URL") else "memory").lower()


def health():
    return {"backend": backend(), "durable": backend() == "postgres", "error": _last_error}


def _sqlite():
    path = os.getenv("M4_SQLITE_PATH")
    if not path:
        raise StorageError("M4_SQLITE_PATH is required for the explicit sqlite backend")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("CREATE TABLE IF NOT EXISTS workflow_state (kind TEXT, identity TEXT, payload TEXT NOT NULL, PRIMARY KEY(kind, identity))")
    return conn


def _guard(operation):
    global _last_error
    try:
        if backend() == "postgres" and _writer_lease is not None:
            _writer_lease.execute("SELECT 1")
        result = operation()
    except Exception as exc:
        _last_error = type(exc).__name__
        raise StorageError("Workflow storage unavailable; operation was not durably acknowledged") from exc
    _last_error = None
    return result


def _upsert(conn, table, key, values):
    from psycopg.types.json import Jsonb
    columns = list(values)
    updates = ", ".join(f"{col}=EXCLUDED.{col}" for col in columns if col != key)
    conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) "
                 f"ON CONFLICT ({key}) DO UPDATE SET {updates}",
                 [Jsonb(value) if isinstance(value, (dict, list)) else value for value in values.values()])


def _save_normalized(conn, incident):
    identity = incident["incident_id"]
    _upsert(conn, "incidents", "incident_id", {"incident_id": identity, "started_at": incident["started_at"],
        "status": incident["status"], "severity": incident["severity"], "payload": incident})
    for key, table, values in (
        ("_rca", "rca_results", lambda p: {"root_cause": p["root_cause"], "confidence": p["confidence"]}),
        ("_proposal", "decision_proposals", lambda p: {"recommended_action": p["recommended_action"]}),
        ("_recovery", "recovery_results", lambda p: {"recovered": p.get("success") is True,
            "recovery_time_seconds": (p.get("details") or {}).get("recovery_time_seconds")}),
    ):
        payload = incident.get(key)
        if payload is not None:
            _upsert(conn, table, "event_key", {"event_key": identity + ":" + key, "incident_id": identity,
                **values(payload), "payload": payload})
    action = incident.get("_action_result") or incident.get("_execution_intent")
    if action:
        _upsert(conn, "actions", "action_id", {"action_id": action["action_id"], "incident_id": identity,
            "action": action["action"], "status": action.get("status", "INTENT"),
            "started_at": action["started_at"], "completed_at": action.get("completed_at"), "payload": action})
    deployment = (incident.get("_evidence") or {}).get("deployment_event")
    if deployment:
        _upsert(conn, "deployment_events", "event_id", {"event_id": deployment["event_id"],
            "service": deployment["service"], "occurred_at": deployment["timestamp"], "payload": deployment})
    experiment = incident.get("_experiment")
    if experiment:
        root_cause = (incident.get("_rca") or {}).get("root_cause")
        rca_correct = root_cause == experiment["expected_root_cause"] if root_cause else None
        recovery = incident.get("_recovery") or {}
        successful = incident["status"] == "RESOLVED" and recovery.get("success") is True
        effect = {"incident_status": incident["status"], "recovery": recovery or None,
                  "finops_context": incident.get("_finops"), "source": incident.get("_provenance")}
        _upsert(conn, "experiment_runs", "run_id", {"run_id": experiment["run_id"], "scenario": experiment["scenario"],
            "incident_id": identity, "injection_time": experiment.get("injection_time"),
            "detection_time": incident.get("detected_at"), "rca_correct": rca_correct,
            "action_time": (incident.get("_action_result") or {}).get("started_at"),
            "recovery_time": incident.get("recovery_validated_at") if successful else None,
            "cost_slo_effect": effect})
    job = incident.get("_archive_outbox")
    if job:
        _upsert(conn, "memory_outbox", "job_id", {"job_id": job["job_id"], "incident_id": identity,
            "status": job["status"], "attempts": job["attempts"], "next_attempt_at": job["next_attempt_at"],
            "payload": job["payload"], "last_error": job.get("last_error"), "delivered_at": job.get("delivered_at")})


def save_incidents(incidents):
    if backend() == "memory":
        return
    def operation():
        with _lock:
            # Copy under the writer lock before handing mutable workflow data to a driver.
            values = json.loads(json.dumps(incidents))
            if backend() == "sqlite":
                with _sqlite() as conn:
                    conn.executemany("INSERT OR REPLACE INTO workflow_state VALUES ('incidents', ?, ?)",
                                     [(item["incident_id"], json.dumps(item)) for item in values])
            elif backend() == "postgres":
                from .connection import connect
                with connect() as conn:
                    for incident in values:
                        _save_normalized(conn, incident)
            else:
                raise StorageError("Unsupported M4_STATE_BACKEND")
    _guard(operation)


def save_anomaly(event, incident_id, incidents=None):
    if backend() == "memory":
        return
    def operation():
        with _lock:
            if backend() == "sqlite":
                with _sqlite() as conn:
                    conn.execute("INSERT OR REPLACE INTO workflow_state VALUES ('anomalies', ?, ?)",
                                 (event["anomaly_id"], json.dumps({"event": event, "incident_id": incident_id})))
                    if incidents is not None:
                        conn.executemany("INSERT OR REPLACE INTO workflow_state VALUES ('incidents', ?, ?)",
                                         [(item["incident_id"], json.dumps(item)) for item in incidents])
            else:
                from .connection import connect
                with connect() as conn:
                    _upsert(conn, "anomalies", "anomaly_id", {"anomaly_id": event["anomaly_id"], "incident_id": incident_id,
                        "service": event["service"], "occurred_at": event["timestamp"], "payload": event})
                    for incident in incidents or []:
                        _save_normalized(conn, incident)
    _guard(operation)


def save_audit(record):
    if backend() == "memory":
        return
    def operation():
        with _lock:
            if backend() == "sqlite":
                with _sqlite() as conn:
                    conn.execute("INSERT OR REPLACE INTO workflow_state VALUES ('audit', ?, ?)",
                                 (record["event_id"], json.dumps(record)))
            else:
                from .connection import connect
                with connect() as conn:
                    _upsert(conn, "timeline_events", "event_key", {"event_key": record["event_id"],
                        "incident_id": record["incident_id"], "event_type": record["event_type"],
                        "occurred_at": record["timestamp"], "payload": record})
                    if record["event_type"] in {"MANUAL_APPROVAL_GRANTED", "MANUAL_APPROVAL_REJECTED"}:
                        _upsert(conn, "approvals", "event_key", {"event_key": record["event_id"],
                            "incident_id": record["incident_id"], "approved": record["event_type"] == "MANUAL_APPROVAL_GRANTED",
                            "decided_at": record["timestamp"], "payload": record["details"]})
    _guard(operation)


def load(kind):
    if backend() == "memory":
        return []
    def operation():
        with _lock:
            if backend() == "sqlite":
                with _sqlite() as conn:
                    values = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM workflow_state WHERE kind=? ORDER BY rowid", (kind,))]
                    return [value["event"] if kind == "anomalies" and "event" in value else value for value in values]
            from psycopg.rows import dict_row
            from .connection import connect
            table = {"incidents": "incidents", "anomalies": "anomalies", "audit": "timeline_events"}[kind]
            with connect() as conn, conn.cursor(row_factory=dict_row) as cursor:
                where = " WHERE event_key IS NOT NULL" if kind == "audit" else ""
                cursor.execute(f"SELECT payload FROM {table}{where} ORDER BY created_at")
                return [row["payload"] for row in cursor.fetchall()]
    return _guard(operation)


def initialize(real_execution=False):
    if real_execution and backend() != "postgres":
        raise StorageError("Real remediation requires PostgreSQL workflow storage and DATABASE_URL")
    if backend() == "postgres":
        from .init_db import init_db
        _guard(init_db)
    elif backend() == "sqlite":
        _guard(lambda: _sqlite().close())
    elif backend() != "memory":
        raise StorageError("Unsupported M4_STATE_BACKEND")


def acquire_lease():
    """One M4 writer per database until distributed incident locking is added."""
    if backend() != "postgres":
        return None
    def operation():
        global _writer_lease
        from .connection import connect
        conn = connect()
        try:
            if not conn.execute("SELECT pg_try_advisory_lock(846042004)").fetchone()[0]:
                raise StorageError("Another M4 writer already owns this workflow database")
            conn.commit()
            conn.autocommit = True
            _writer_lease = conn
            return conn
        except Exception:
            conn.close()
            raise
    return _guard(operation)


def close_lease(lease):
    global _writer_lease
    if lease is not None:
        lease.close()
    if _writer_lease is lease:
        _writer_lease = None


def load_anomaly_owners():
    if backend() == "memory":
        return {}
    def operation():
        if backend() == "sqlite":
            with _sqlite() as conn:
                return {identity: json.loads(payload).get("incident_id") for identity, payload in
                        conn.execute("SELECT identity, payload FROM workflow_state WHERE kind='anomalies'")}
        from .connection import connect
        with connect() as conn:
            return dict(conn.execute("SELECT anomaly_id, incident_id FROM anomalies").fetchall())
    return _guard(operation)


def probe():
    if backend() == "postgres":
        from .connection import connect
        def operation():
            with connect() as conn:
                conn.execute("SELECT 1")
        _guard(operation)
