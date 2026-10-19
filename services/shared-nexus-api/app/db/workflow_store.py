"""Durable workflow state. PostgreSQL is required for real operation.

The SQLite backend is explicit, for controlled local restart tests. Memory is
allowed only in mock mode; it never claims restart durability.

Each incident is written on its own, conditional on the revision it replaces
(`_revision` in the payload). A write that does not find the expected revision
is rejected as a conflict instead of overwriting a newer update. With
PostgreSQL every write transaction also re-checks the writer fencing token, so
a process that has lost its writer lease cannot commit after another process
took ownership.
"""
import json
import logging
import os
import socket
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

LEASE_KEY = 846042004


class StorageError(RuntimeError):
    pass


class StorageConflict(StorageError):
    """A competing writer changed the incident; the update was not applied."""


class LeaseLost(StorageError):
    """This process no longer owns the workflow database."""


_last_error = None
_lease_lost = False
_writer_lease = None
_lease_generation = None
_standby = False


class Standby(StorageError):
    """This replica is a standby: the elected leader handles every write."""


def set_standby(value: bool) -> None:
    global _standby
    _standby = bool(value)


def is_standby() -> bool:
    return _standby


def lease_lost() -> bool:
    return _lease_lost


def backend():
    return os.getenv("M4_STATE_BACKEND", "postgres" if os.getenv("DATABASE_URL") else "memory").lower()


def health():
    return {"backend": backend(), "durable": backend() == "postgres", "error": _last_error,
            "lease_lost": _lease_lost, "lease_generation": _lease_generation, "standby": _standby}


def _sqlite():
    path = os.getenv("M4_SQLITE_PATH")
    if not path:
        raise StorageError("M4_SQLITE_PATH is required for the explicit sqlite backend")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5, isolation_level=None)
    conn.execute("CREATE TABLE IF NOT EXISTS workflow_state (kind TEXT, identity TEXT, payload TEXT NOT NULL, PRIMARY KEY(kind, identity))")
    return conn


class _SqliteTransaction:
    """BEGIN IMMEDIATE ... COMMIT, so a revision check and its write are atomic."""

    def __enter__(self):
        self.conn = _sqlite()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, kind, value, traceback):
        try:
            self.conn.execute("ROLLBACK" if kind else "COMMIT")
        finally:
            self.conn.close()


def _mark_lease_lost(reason):
    global _lease_lost, _last_error
    if not _lease_lost:
        logger.error("M4 writer lease lost: %s", reason)
    _lease_lost = True
    _last_error = "LeaseLost"


def _guard(operation):
    global _last_error
    if _lease_lost:
        raise LeaseLost("Writer lease lost; this instance must restart")
    try:
        result = operation()
    except StorageError as exc:
        _last_error = type(exc).__name__
        raise
    except Exception as exc:
        _last_error = type(exc).__name__
        raise StorageError("Workflow storage unavailable; operation was not durably acknowledged") from exc
    _last_error = None
    return result


def _fence(conn):
    """Reject the transaction unless this process still holds the newest lease."""
    if _standby:
        raise Standby("This M4 replica is a standby; the elected leader handles writes")
    if _lease_generation is None:
        return
    row = conn.execute("SELECT generation FROM m4_writer_lease WHERE id = 1 FOR SHARE").fetchone()
    if row is None or row[0] != _lease_generation:
        _mark_lease_lost(f"fencing generation is {row[0] if row else None}, expected {_lease_generation}")
        raise LeaseLost("Another M4 writer owns the workflow database; this instance must restart")


def _upsert(conn, table, key, values):
    from psycopg.types.json import Jsonb
    columns = list(values)
    updates = ", ".join(f"{col}=EXCLUDED.{col}" for col in columns if col != key)
    conn.execute(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) "
                 f"ON CONFLICT ({key}) DO UPDATE SET {updates}",
                 [Jsonb(value) if isinstance(value, (dict, list)) else value for value in values.values()])


def _revision(incident):
    value = incident.get("_revision", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise StorageError("Incident write is missing its new revision")
    return value


def _write_incident_row(conn, incident):
    """Create at revision 1 only if absent; otherwise update only from revision - 1."""
    from psycopg.types.json import Jsonb
    identity, revision = incident["incident_id"], _revision(incident)
    values = [incident["started_at"], incident["status"], incident["severity"], Jsonb(incident), revision]
    if revision == 1:
        cursor = conn.execute(
            "INSERT INTO incidents (incident_id, started_at, status, severity, payload, revision) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (incident_id) DO UPDATE SET "
            "started_at=EXCLUDED.started_at, status=EXCLUDED.status, severity=EXCLUDED.severity, "
            "payload=EXCLUDED.payload, revision=EXCLUDED.revision WHERE incidents.revision = 0",
            [identity, *values])  # revision 0: a row written before revisions existed
    else:
        cursor = conn.execute(
            "UPDATE incidents SET started_at=%s, status=%s, severity=%s, payload=%s, revision=%s "
            "WHERE incident_id=%s AND revision=%s", [*values, identity, revision - 1])
    if cursor.rowcount != 1:
        raise StorageConflict(f"Incident {identity} was changed by another writer; reload before retrying")


def _save_normalized(conn, incident):
    identity = incident["incident_id"]
    _write_incident_row(conn, incident)
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


def _sqlite_write_incidents(conn, incidents):
    for incident in incidents:
        identity, revision = incident["incident_id"], _revision(incident)
        row = conn.execute("SELECT payload FROM workflow_state WHERE kind='incidents' AND identity=?",
                           (identity,)).fetchone()
        stored = json.loads(row[0]).get("_revision", 0) if row else None
        if (stored is None and revision != 1) or (stored is not None and stored != revision - 1):
            raise StorageConflict(f"Incident {identity} was changed by another writer; reload before retrying")
        conn.execute("INSERT OR REPLACE INTO workflow_state VALUES ('incidents', ?, ?)", (identity, json.dumps(incident)))


def save_incidents(incidents):
    """Atomically write the given incidents, each conditional on its prior revision."""
    if backend() == "memory" or not incidents:
        return
    def operation():
        # Copy before handing mutable workflow data to a driver.
        values = json.loads(json.dumps(incidents))
        if backend() == "sqlite":
            with _SqliteTransaction() as conn:
                _sqlite_write_incidents(conn, values)
        elif backend() == "postgres":
            from .connection import connect
            with connect() as conn:
                _fence(conn)
                for incident in values:
                    _save_normalized(conn, incident)
        else:
            raise StorageError("Unsupported M4_STATE_BACKEND")
    _guard(operation)


def save_anomaly(event, incident_id, incidents=None):
    if backend() == "memory":
        return
    def operation():
        values = json.loads(json.dumps(incidents or []))
        if backend() == "sqlite":
            with _SqliteTransaction() as conn:
                conn.execute("INSERT OR REPLACE INTO workflow_state VALUES ('anomalies', ?, ?)",
                             (event["anomaly_id"], json.dumps({"event": event, "incident_id": incident_id})))
                _sqlite_write_incidents(conn, values)
        else:
            from .connection import connect
            with connect() as conn:
                _fence(conn)
                _upsert(conn, "anomalies", "anomaly_id", {"anomaly_id": event["anomaly_id"], "incident_id": incident_id,
                    "service": event["service"], "occurred_at": event["timestamp"], "payload": event})
                for incident in values:
                    _save_normalized(conn, incident)
    _guard(operation)


def save_audit(record):
    if backend() == "memory":
        return
    def operation():
        if backend() == "sqlite":
            with _SqliteTransaction() as conn:
                conn.execute("INSERT OR REPLACE INTO workflow_state VALUES ('audit', ?, ?)",
                             (record["event_id"], json.dumps(record)))
        else:
            from .connection import connect
            with connect() as conn:
                _fence(conn)
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
        if backend() == "sqlite":
            conn = _sqlite()
            try:
                values = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM workflow_state WHERE kind=? ORDER BY rowid", (kind,))]
            finally:
                conn.close()
            return [value["event"] if kind == "anomalies" and "event" in value else value for value in values]
        from psycopg.rows import dict_row
        from .connection import connect
        table = {"incidents": "incidents", "anomalies": "anomalies", "audit": "timeline_events"}[kind]
        with connect() as conn, conn.cursor(row_factory=dict_row) as cursor:
            if kind == "incidents":
                cursor.execute("SELECT payload, revision FROM incidents ORDER BY created_at")
                # The column is authoritative; legacy payloads predate `_revision`.
                return [dict(row["payload"], _revision=row["revision"]) for row in cursor.fetchall()]
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


def _owner():
    return f"{socket.gethostname()}:{os.getpid()}"


def acquire_lease():
    """One M4 writer per database, fenced by a generation counter."""
    if backend() != "postgres":
        return None
    lease = try_acquire_lease()
    if lease is None:
        raise StorageError("Another M4 writer already owns this workflow database")
    return lease


def try_acquire_lease():
    """The writer lease, or None while another process holds it (leader election)."""
    if backend() != "postgres":
        return None
    def operation():
        global _writer_lease, _lease_generation, _lease_lost
        from .connection import connect
        conn = connect()
        try:
            if not conn.execute("SELECT pg_try_advisory_lock(%s)", [LEASE_KEY]).fetchone()[0]:
                conn.close()
                return None
            # Taking the row lock waits for any older owner's in-flight fenced
            # transaction; once incremented, those writers can no longer commit.
            generation = conn.execute(
                "INSERT INTO m4_writer_lease (id, generation, owner) VALUES (1, 1, %s) "
                "ON CONFLICT (id) DO UPDATE SET generation = m4_writer_lease.generation + 1, "
                "owner = EXCLUDED.owner, acquired_at = now() RETURNING generation", [_owner()]).fetchone()[0]
            conn.commit()
            conn.autocommit = True
            _writer_lease, _lease_generation, _lease_lost = conn, generation, False
            return conn
        except Exception:
            conn.close()
            raise
    return _guard(operation)


def check_lease():
    """Proactively verify ownership; called by the lease monitor even when idle."""
    if backend() != "postgres" or _writer_lease is None or _lease_lost:
        return not _lease_lost
    try:
        held = _writer_lease.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
            "AND classid = 0 AND objid = %s AND objsubid = 1 AND granted)", [LEASE_KEY]).fetchone()[0]
        generation = _writer_lease.execute("SELECT generation FROM m4_writer_lease WHERE id = 1").fetchone()
    except Exception as exc:
        _mark_lease_lost(f"lease connection failed: {type(exc).__name__}")
        return False
    if not held:
        _mark_lease_lost("advisory lock is no longer held by this session")
        return False
    if generation is None or generation[0] != _lease_generation:
        _mark_lease_lost("another process took a newer writer generation")
        return False
    return True


def close_lease(lease):
    global _writer_lease, _lease_generation
    if lease is not None:
        try:
            lease.close()
        except Exception:
            pass
    if _writer_lease is lease:
        _writer_lease, _lease_generation = None, None


def reset_lease_state():
    """For tests: forget a previous lease loss in this process."""
    global _lease_lost, _last_error
    _lease_lost, _last_error = False, None


def load_anomaly_owners():
    if backend() == "memory":
        return {}
    def operation():
        if backend() == "sqlite":
            conn = _sqlite()
            try:
                return {identity: json.loads(payload).get("incident_id") for identity, payload in
                        conn.execute("SELECT identity, payload FROM workflow_state WHERE kind='anomalies'")}
            finally:
                conn.close()
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
