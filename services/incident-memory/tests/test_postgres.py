"""Run against a disposable database using M5_TEST_DATABASE_URL."""
import os
import unittest
from uuid import uuid4
from app.fixtures import demo_records
from app.models import StoreRequest
from app.storage import Repository


@unittest.skipUnless(os.getenv("M5_TEST_DATABASE_URL"), "No PostgreSQL integration database configured")
class PostgresTests(unittest.TestCase):
    def test_persistent_idempotent_roundtrip(self):
        repo = Repository(os.environ["M5_TEST_DATABASE_URL"])
        self.assertTrue(repo.postgres)
        repo.initialize()
        payload = demo_records()[2]
        payload["memory"]["incident_id"] = "TEST-" + uuid4().hex
        request = StoreRequest.model_validate(payload)
        try:
            record, status = repo.save(request)
            self.assertEqual(status, "stored")
            self.assertEqual(repo.save(request)[1], "already_stored")
            self.assertEqual(Repository(repo.url).get(request.memory.incident_id, "mock"), record)
        finally:
            with repo.connection() as conn:
                conn.execute("DELETE FROM m5_incident_memory WHERE source=%s AND incident_id=%s", ("mock", request.memory.incident_id))
