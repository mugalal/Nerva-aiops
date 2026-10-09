"""
Live polling of M1 (M2 runbook, Days 5 and 12).

Every `interval_s` seconds, for each watched service, ask M1 for the latest
snapshot and run it through the pipeline.

What happens when the data isn't good (runbook Day 12: "NaN, missing data,
stale data, Prometheus timeout... Do not turn missing telemetry into a
high-confidence anomaly"). In every case below the answer is the same: no
reading is scored, nothing is made up, and the service's state says why.

    M1 unreachable / timed out    state "unreachable"
    M1 answered with an error     state is M1's own error category
    M1 returned no readings       state "no_data"
    M1 returned a malformed one   state "bad_response"
    newest reading is too old     state "stale"
    same reading as last time     skipped quietly (M1 repeats a reading until
                                  Prometheus has a newer one)

The next good reading puts the state back to "ok". A bad poll never stops the
loop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable

from pydantic import ValidationError

from shared.contracts import TelemetrySnapshot

from .pipeline import Pipeline, Reading
from .realdata.m1_client import M1Client, M1Error

log = logging.getLogger("m2.poller")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


class Poller:
    def __init__(
        self,
        client: M1Client,
        pipeline: Pipeline,
        services: list[str],
        stale_after_s: float = 120.0,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.client = client
        self.pipeline = pipeline
        self.services = list(services)
        self.stale_after_s = stale_after_s
        self.now = now
        self.status: dict[str, dict] = {
            s: {"state": "starting", "message": "", "last_ok": None, "last_poll": None} for s in self.services
        }
        self._last_seen: dict[str, datetime] = {}

    def _mark(self, service: str, state: str, message: str = "") -> None:
        entry = self.status[service]
        entry["state"] = state
        entry["message"] = message
        entry["last_poll"] = _iso(self.now())
        if state == "ok":
            entry["last_ok"] = entry["last_poll"]
        elif state != "starting":
            log.warning(
                "m1 poll for %s: %s %s", service, state, message,
                extra={"service_name": service, "provider": "m1", "error_category": state},
            )

    def dependency_state(self) -> str:
        """"ok" while every watched service is fine (or not yet polled),
        otherwise a short description of what is wrong."""
        bad = {s: e["state"] for s, e in self.status.items() if e["state"] not in ("ok", "starting")}
        if not bad:
            return "ok"
        return "degraded: " + ", ".join(f"{s} {state}" for s, state in sorted(bad.items()))

    def poll_once(self) -> list[Reading]:
        readings: list[Reading] = []
        for service in self.services:
            try:
                snapshot: TelemetrySnapshot = self.client.snapshot(service)
            except M1Error as exc:
                self._mark(service, exc.category, exc.message)
                continue
            except ValidationError as exc:
                self._mark(service, "bad_response", f"{exc.error_count()} problem(s) with M1's reading")
                continue

            age = (self.now() - snapshot.timestamp).total_seconds()
            if age > self.stale_after_s:
                self._mark(service, "stale", f"newest reading is {age:.0f}s old")
                continue

            last = self._last_seen.get(service)
            if last is not None and snapshot.timestamp <= last:
                self._mark(service, "ok")           # nothing new yet; not a problem
                continue

            self._last_seen[service] = snapshot.timestamp
            readings.append(self.pipeline.process(snapshot))
            self._mark(service, "ok")
        return readings

    async def run(self, interval_s: float) -> None:
        while True:
            try:
                await asyncio.to_thread(self.poll_once)
            except Exception:  # noqa: BLE001 - one bad poll must never stop the loop
                log.exception("unexpected error while polling M1")
            await asyncio.sleep(interval_s)
