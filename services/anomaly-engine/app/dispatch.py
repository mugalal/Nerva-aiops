import logging
import queue
import threading

log = logging.getLogger("m2.dispatch")


class Dispatcher:
    """One worker thread; collapses repeat submits for the same incident."""

    def __init__(self, fn, maxsize: int = 256):
        self._fn = fn
        self._q = queue.Queue(maxsize=maxsize)
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="m2-dispatch", daemon=True)
        self._thread.start()

    def submit(self, incident_id: str) -> None:
        with self._lock:
            if incident_id in self._pending:
                return
            try:
                self._q.put_nowait(incident_id)
            except queue.Full:
                log.warning("dispatch queue full; alert will be retried on next poll", extra={"incident_id": incident_id})
                return
            self._pending.add(incident_id)

    def _run(self):
        while True:
            incident_id = self._q.get()
            with self._lock:
                self._pending.discard(incident_id)
            try:
                self._fn(incident_id)
            except Exception:
                log.exception("dispatch failed", extra={"incident_id": incident_id})
