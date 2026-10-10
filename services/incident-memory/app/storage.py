import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class StorageUnavailable(Exception):
    pass


class MemoryConflict(Exception):
    pass


class Repository:
    """Idempotent snapshots; missing context may be enriched, existing facts cannot change."""
    def __init__(self, url=None):
        self.url = url or os.getenv("DATABASE_URL") or "sqlite:///./data/memory.db"
        self.postgres = self.url.startswith(("postgres://", "postgresql://"))
        if not self.postgres and not self.url.startswith("sqlite:///"):
            raise ValueError("DATABASE_URL must use postgresql:// or sqlite:///")

    @contextmanager
    def connection(self):
        connection = None
        try:
            if self.postgres:
                import psycopg
                connection = psycopg.connect(self.url, connect_timeout=3)
            else:
                path = Path(self.url.removeprefix("sqlite:///"))
                path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(path, timeout=3)
            yield connection
            connection.commit()
        except MemoryConflict:
            if connection:
                connection.rollback()
            raise
        except Exception as exc:
            if connection:
                connection.rollback()
            raise StorageUnavailable("Incident memory database is unavailable") from exc
        finally:
            if connection:
                connection.close()

    def initialize(self):
        with self.connection() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS m5_incident_memory (
                source TEXT NOT NULL, incident_id TEXT NOT NULL,
                payload TEXT NOT NULL, stored_at TEXT NOT NULL,
                PRIMARY KEY (source, incident_id))""")

    def save(self, request):
        payload = request.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, allow_nan=False)
        stored_at = datetime.now(timezone.utc).isoformat()
        p = "%s" if self.postgres else "?"
        with self.connection() as conn:
            cursor = conn.execute(
                f"INSERT INTO m5_incident_memory VALUES ({p},{p},{p},{p}) ON CONFLICT (source,incident_id) DO NOTHING",
                (request.source, request.memory.incident_id, serialized, stored_at),
            )
            created = cursor.rowcount == 1
            row = conn.execute(
                f"SELECT payload,stored_at FROM m5_incident_memory WHERE source={p} AND incident_id={p}",
                (request.source, request.memory.incident_id),
            ).fetchone()
            status = "stored" if created else "already_stored"
            if row[0] != serialized:
                existing = json.loads(row[0])
                if any(existing[key] != payload[key] for key in ("memory", "source", "resolved")):
                    raise MemoryConflict("A different memory snapshot already exists; do not overwrite recorded facts")
                for key, incoming in payload["context"].items():
                    previous = existing["context"].get(key)
                    if incoming is not None:
                        if previous is not None and previous != incoming:
                            raise MemoryConflict(f"Recorded context.{key} cannot be overwritten")
                        existing["context"][key] = incoming
                merged = json.dumps(existing, sort_keys=True, allow_nan=False)
                if merged != row[0]:
                    updated = conn.execute(
                        f"UPDATE m5_incident_memory SET payload={p} WHERE source={p} AND incident_id={p} AND payload={p}",
                        (merged, request.source, request.memory.incident_id, row[0]),
                    )
                    if updated.rowcount != 1:
                        raise MemoryConflict("Concurrent enrichment detected; retry the request")
                    row = (merged, row[1])
                    status = "enriched"
        return self.unpack(row), status

    @staticmethod
    def unpack(row):
        return {**json.loads(row[0]), "stored_at": row[1]}

    def get(self, incident_id, source):
        p = "%s" if self.postgres else "?"
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT payload,stored_at FROM m5_incident_memory WHERE source={p} AND incident_id={p}",
                (source, incident_id),
            ).fetchone()
        return self.unpack(row) if row else None

    def all(self, source):
        p = "%s" if self.postgres else "?"
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT payload,stored_at FROM m5_incident_memory WHERE source={p} ORDER BY incident_id", (source,)
            ).fetchall()
        return [self.unpack(row) for row in rows]
