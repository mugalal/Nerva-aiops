"""Run against a disposable database using M5_TEST_DATABASE_URL."""
import os
import unittest
from copy import deepcopy
from uuid import uuid4
from app.fixtures import demo_records
from app.models import StoreRequest
from app.storage import Repository
from app.main import create_app
from app.providers import Provider
from fastapi.testclient import TestClient


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

    def test_api_restart_keeps_full_evidence_and_copilot_citations(self):
        # Synthetic test inputs stay in mock storage even while PostgreSQL is real.
        payload = deepcopy(demo_records()[0])
        identifier = "TEST-" + uuid4().hex
        payload["memory"]["incident_id"] = identifier
        for name in ("incident", "rca", "decision", "action_result", "recovery_result"):
            payload["context"][name]["incident_id"] = identifier
        repo = Repository(os.environ["M5_TEST_DATABASE_URL"])
        try:
            with TestClient(create_app(repo, Provider("mock"))) as first:
                response = first.post("/internal/memory/store", json=payload)
                self.assertEqual(response.status_code, 200)
                stored = response.json()["record"]
            with TestClient(create_app(Repository(repo.url), Provider("mock"))) as restarted:
                self.assertEqual(restarted.get(f"/internal/memory/{identifier}?source=mock").json()["record"], stored)
                self.assertEqual(restarted.post("/internal/memory/store", json=payload).json()["status"], "already_stored")
                result = restarted.post("/internal/copilot/query", json={"incident_id": identifier,
                    "question": "Did it work?", "source": "mock"}).json()
                self.assertEqual(result["status"], "ok")
                for citation in result["citations"]:
                    actual = stored
                    for part in citation["field"].split("."):
                        actual = actual[part]
                    self.assertEqual(actual, citation["value"])
                    self.assertEqual(citation["source"], "mock")
                self.assertEqual(restarted.get(f"/internal/memory/{identifier}?source=real").status_code, 404)
        finally:
            with repo.connection() as conn:
                conn.execute("DELETE FROM m5_incident_memory WHERE source=%s AND incident_id=%s", ("mock", identifier))
