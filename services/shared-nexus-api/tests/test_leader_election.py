"""A second M4 replica waits as a standby and takes over when the leader's lease is released.

The PostgreSQL advisory lock itself is exercised in test_postgres_lease.py
(CI); here the lease is a stand-in so the election logic runs everywhere.
"""
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db import workflow_store
from app.main import app
from app.orchestration import leadership


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch):
    monkeypatch.setenv("M4_LEADER_RETRY_SECONDS", "0.1")
    yield
    workflow_store.set_standby(False)


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class Lock:
    """The advisory lock: one holder; released when the holder goes away."""

    def __init__(self):
        self.holder = None

    def acquirer(self, name):
        def acquire():
            if self.holder is None:
                self.holder = name
                return SimpleNamespace(name=name)
            return None
        return acquire


def leader_starter(started):
    def lead(lease):
        started.append(lease.name)
        return SimpleNamespace(close=lambda: None, thread=SimpleNamespace(is_alive=lambda: False))
    return lead


def test_standby_takes_over_when_the_leader_releases_the_lease(monkeypatch):
    monkeypatch.setattr(workflow_store, "close_lease", lambda lease: None)
    lock, started = Lock(), []
    first = leadership.Runtime(leader_starter(started), acquire=lock.acquirer("a"))
    first.start_election()
    assert wait_for(lambda: first.role == "leader")
    second = leadership.Runtime(leader_starter(started), acquire=lock.acquirer("b"))
    second.start_election()
    time.sleep(0.3)
    assert second.role == "standby" and started == ["a"]
    lock.holder = None              # the leader's database session ended
    assert wait_for(lambda: second.role == "leader")
    assert started == ["a", "b"]
    first.close(); second.close()


def test_a_leader_that_fails_to_start_releases_the_lease_and_retries(monkeypatch):
    released, attempts = [], []
    monkeypatch.setattr(workflow_store, "close_lease", released.append)

    def lead(lease):
        attempts.append(lease)
        if len(attempts) == 1:
            raise RuntimeError("restore failed")
        return SimpleNamespace(close=lambda: None, thread=SimpleNamespace(is_alive=lambda: False))

    runtime = leadership.Runtime(lead, acquire=lambda: SimpleNamespace(name="x"))
    runtime.start_election()
    assert wait_for(lambda: runtime.role == "leader")
    assert len(attempts) == 2 and released and released[0] is attempts[0]
    runtime.close()


def test_a_standby_is_alive_but_not_ready_and_refuses_writes():
    client = TestClient(app)
    workflow_store.set_standby(True)
    assert client.get("/live").status_code == 200
    ready = client.get("/ready")
    assert ready.status_code == 503 and ready.json()["status"] == "standby"
    with pytest.raises(workflow_store.Standby):
        workflow_store._fence(None)
    workflow_store.set_standby(False)


def test_election_settings_are_validated(monkeypatch):
    monkeypatch.setenv("M4_LEADER_RETRY_SECONDS", "0")
    with pytest.raises(ValueError):
        leadership.retry_seconds()
    monkeypatch.setenv("M4_LEADER_ELECTION", "false")
    assert leadership.enabled() is False
