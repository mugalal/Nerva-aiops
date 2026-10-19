"""Bounded, observable asynchronous handoff to the shared API.

* Bounded: at most `maxsize` incidents wait in the queue; `workers` threads deliver.
* Deduplicated: an incident is queued or in flight at most once. A submit while
  it is in flight marks it to run once more afterwards, so no update is lost.
* No silent loss: a submit that finds the queue full is DEFERRED, counted and
  logged, never dropped. The pending work itself lives in the pipeline's
  incident candidates, and a periodic sweep re-submits every incident that
  still has undelivered steps (this is also how failed deliveries are retried,
  with the pipeline's exponential backoff). Candidates and their handoff
  progress are saved to M2_STATE_PATH (state.py), so a restart resumes them.
* Graceful shutdown: close() stops intake, drains what is queued within the
  timeout, and reports anything left undelivered.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Iterable

log = logging.getLogger("m2.dispatch")


class Dispatcher:
    def __init__(self, fn: Callable[[str], None], maxsize: int = 256, workers: int = 2,
                 sweep: Callable[[], Iterable[str]] | None = None, sweep_interval_s: float = 5.0) -> None:
        if maxsize < 1 or workers < 1 or sweep_interval_s <= 0:
            raise ValueError("dispatcher maxsize, workers and sweep interval must be positive")
        self._fn = fn
        self._q: queue.Queue[str | None] = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._queued: set[str] = set()
        self._in_flight: set[str] = set()
        self._again: set[str] = set()
        self._deferred: set[str] = set()
        self._closing = threading.Event()
        self._sweep = sweep
        self._sweep_interval_s = sweep_interval_s
        self.counters = {"submitted": 0, "completed_calls": 0, "failed_calls": 0, "deferred": 0,
                         "deduplicated": 0, "swept": 0}
        self._threads = [threading.Thread(target=self._run, name=f"m2-dispatch-{n}", daemon=True)
                         for n in range(workers)]
        if sweep is not None:
            self._threads.append(threading.Thread(target=self._sweeper, name="m2-dispatch-sweep", daemon=True))
        for thread in self._threads:
            thread.start()

    def submit(self, incident_id: str) -> bool:
        """True when queued (or already pending); False when deferred by backpressure or closing."""
        with self._lock:
            if self._closing.is_set():
                self._deferred.add(incident_id)
                return False
            self.counters["submitted"] += 1
            if incident_id in self._queued:
                self.counters["deduplicated"] += 1
                return True
            if incident_id in self._in_flight:
                self._again.add(incident_id)
                self.counters["deduplicated"] += 1
                return True
            try:
                self._q.put_nowait(incident_id)
            except queue.Full:
                if incident_id not in self._deferred:
                    self.counters["deferred"] += 1
                    log.warning("dispatch queue full; incident deferred to the retry sweep",
                                extra={"incident_id": incident_id, "error_category": "backpressure"})
                self._deferred.add(incident_id)
                return False
            self._queued.add(incident_id)
            self._deferred.discard(incident_id)
            return True

    def _run(self) -> None:
        while True:
            incident_id = self._q.get()
            if incident_id is None:
                return
            with self._lock:
                self._queued.discard(incident_id)
                self._in_flight.add(incident_id)
            try:
                self._fn(incident_id)
                with self._lock:
                    self.counters["completed_calls"] += 1
            except Exception:  # noqa: BLE001 - delivery problems are recorded, never fatal
                with self._lock:
                    self.counters["failed_calls"] += 1
                log.exception("dispatch failed", extra={"incident_id": incident_id})
            finally:
                with self._lock:
                    self._in_flight.discard(incident_id)
                    again = incident_id in self._again
                    self._again.discard(incident_id)
                if again:
                    self.submit(incident_id)

    def _sweeper(self) -> None:
        while not self._closing.wait(self._sweep_interval_s):
            self.sweep_once()

    def sweep_once(self) -> int:
        """Re-submit every incident with undelivered handoff steps (and anything deferred)."""
        with self._lock:
            pending = set(self._deferred)
        try:
            pending.update(self._sweep() if self._sweep is not None else ())
        except Exception:  # noqa: BLE001
            log.exception("dispatch sweep failed")
        count = 0
        for incident_id in sorted(pending):
            if self.submit(incident_id):
                count += 1
        with self._lock:
            self.counters["swept"] += count
        return count

    def stats(self) -> dict:
        with self._lock:
            return {**self.counters, "queued": len(self._queued), "in_flight": len(self._in_flight),
                    "deferred_now": len(self._deferred), "capacity": self._q.maxsize,
                    "workers": sum(1 for t in self._threads if t.name.startswith("m2-dispatch-") and
                                   not t.name.endswith("sweep") and t.is_alive()),
                    "closing": self._closing.is_set()}

    def close(self, timeout: float = 10.0) -> dict:
        """Stop intake, drain the queue within `timeout`, then stop the workers."""
        self._closing.set()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._queued and not self._in_flight:
                    break
            time.sleep(0.02)
        workers = [t for t in self._threads if not t.name.endswith("sweep")]
        for _ in workers:
            try:
                self._q.put_nowait(None)
            except queue.Full:
                break
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            undelivered = sorted(self._queued | self._in_flight | self._deferred)
        if undelivered:
            log.warning("dispatcher closed with undelivered incidents: %s", ", ".join(undelivered))
        return {"undelivered": undelivered}
