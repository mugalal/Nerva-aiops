"""Leader election for M4 replicas over the existing PostgreSQL writer lease.

The lease (workflow_store) already guaranteed at most one writer: a session
advisory lock plus a generation number that fences an older owner's writes.
But a second replica could not start at all ("another M4 writer already owns
this workflow database"), so there was no standby to take over.

With election (the default on PostgreSQL), every replica starts serving at
once. The one that wins the advisory lock is the leader: it restores the
workflow and runs the lifecycle worker. The others are standbys: alive
(/live 200), NOT ready (/ready 503, so the Service sends them no traffic),
refusing writes, and retrying the lock every M4_LEADER_RETRY_SECONDS. When the
leader's database session ends (crash, eviction, network loss), PostgreSQL
releases its lock, one standby acquires it, increments the fencing generation
(so a half-dead old leader can no longer commit) and becomes leader.
M4_LEADER_ELECTION=false keeps the old single-writer startup.
"""
import logging
import os
from threading import Event, Thread

from app.db import workflow_store

logger = logging.getLogger(__name__)


def enabled():
    return os.getenv("M4_LEADER_ELECTION", "true").strip().lower() in {"1", "true", "yes", "on"}


def retry_seconds():
    raw = os.getenv("M4_LEADER_RETRY_SECONDS", "2")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("M4_LEADER_RETRY_SECONDS must be a number in [0.1, 60]") from exc
    if not 0.1 <= value <= 60:
        raise ValueError("M4_LEADER_RETRY_SECONDS must be a number in [0.1, 60]")
    return value


class Runtime:
    """Owns the lease and the worker for one process, as leader or standby."""

    def __init__(self, lead, acquire=None):
        self._lead = lead                      # callable(lease) -> worker; restores state and starts it
        self._acquire = acquire or workflow_store.try_acquire_lease
        self.lease = None
        self.worker = None
        self.role = "starting"
        self._stop = Event()
        self._thread = None

    def run_single(self, lease):
        """No election: this process is the writer (or startup fails, as before)."""
        self.lease, self.role = lease, "leader"
        self.worker = self._lead(lease)

    def start_election(self):
        self.role = "standby"
        workflow_store.set_standby(True)
        self._thread = Thread(target=self._campaign, name="m4-leader-election", daemon=True)
        self._thread.start()

    def _campaign(self):
        interval = retry_seconds()
        while not self._stop.is_set():
            try:
                lease = self._acquire()
            except Exception:
                logger.exception("M4 leader election: lease attempt failed; retrying")
                lease = None
            if lease is not None:
                workflow_store.set_standby(False)
                try:
                    self.lease = lease
                    self.worker = self._lead(lease)
                    self.role = "leader"   # Only once the workflow is restored and the worker runs.
                    logger.info("M4 elected leader")
                    return
                except Exception:
                    logger.exception("M4 leader failed to start; releasing the lease")
                    workflow_store.close_lease(lease)
                    self.lease, self.worker, self.role = None, None, "standby"
                    workflow_store.set_standby(True)
            self._stop.wait(interval)

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.worker is not None:
            self.worker.close()
        # A bounded in-flight read/notification may outlive shutdown. Keep the
        # writer lease until it finishes, so another process cannot overlap it.
        if self.worker is None or not self.worker.thread.is_alive():
            workflow_store.close_lease(self.lease)
        workflow_store.set_standby(False)
