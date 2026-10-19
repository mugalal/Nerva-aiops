"""
Durable incident candidates (M2 audit finding: "handoff state held in memory").

The correlator's candidates, and how far each one has been handed to the
shared API, used to live only in memory: a restart forgot an incident whose
creation or anomaly links had not been delivered yet. This store writes them
to one JSON file after every change (atomically: temp file, then rename) and
reads them back at startup, so the retry sweep finishes the handoff.

Only bookkeeping is stored; the scoring reference and baselines are rebuilt
from configuration as before. A missing file is a fresh start. A corrupt file
is moved aside (`.corrupt`), logged and treated as a fresh start: losing the
candidates is no worse than before this store existed, and the shared API
de-duplicates re-detected incidents.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

from shared.contracts import AnomalyEvent

from .correlation import Candidate, Correlator, HandoffState

log = logging.getLogger("m2.state")
VERSION = 1


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def candidate_to_dict(candidate: Candidate) -> dict:
    state = candidate.handoff
    return {
        "incident_id": candidate.incident_id, "service": candidate.service,
        "started_at": _time(candidate.started_at), "last_alert_at": _time(candidate.last_alert_at),
        "severity": candidate.severity, "anomaly_ids": list(candidate.anomaly_ids),
        "signals": sorted(candidate.signals), "peak_score": candidate.peak_score, "context": candidate.context,
        "events": candidate.events,
        "anomaly_events": [event.model_dump(mode="json") for event in candidate.anomaly_events],
        "handoff": {"incident_created": state.incident_created, "linked": sorted(state.linked),
                    "peak_done": state.peak_done, "last_linked_at": _time(state.last_linked_at),
                    "attempts": state.attempts, "last_attempt": _time(state.last_attempt), "error": state.error},
    }


def candidate_from_dict(data: dict) -> Candidate:
    handoff = data["handoff"]
    return Candidate(
        incident_id=data["incident_id"], service=data["service"],
        started_at=_parse(data["started_at"]), last_alert_at=_parse(data["last_alert_at"]),
        severity=data["severity"], anomaly_ids=list(data["anomaly_ids"]), signals=set(data["signals"]),
        peak_score=float(data["peak_score"]), context=data.get("context"), events=list(data.get("events", [])),
        anomaly_events=[AnomalyEvent.model_validate(event) for event in data["anomaly_events"]],
        # A delivery that was in flight when the process stopped is simply retried.
        handoff=HandoffState(incident_created=bool(handoff["incident_created"]), linked=set(handoff["linked"]),
                             peak_done=bool(handoff["peak_done"]), last_linked_at=_parse(handoff["last_linked_at"]),
                             attempts=int(handoff["attempts"]), last_attempt=_parse(handoff["last_attempt"]),
                             error=handoff.get("error"), in_flight=False),
    )


class CandidateStore:
    def __init__(self, path: str | Path | None) -> None:
        self.path = None if path is None else Path(path)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def save(self, correlator: Correlator) -> None:
        if self.path is None:
            return
        payload = {"version": VERSION,
                   "candidates": [candidate_to_dict(c) for c in reversed(correlator.candidates(None, correlator.max_candidates))]}
        text = json.dumps(payload, sort_keys=True, allow_nan=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, self.path)

    def load(self, correlator: Correlator) -> int:
        """Restore saved candidates into an empty correlator; returns how many."""
        if self.path is None or not self.path.exists():
            return 0
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("version") != VERSION:
                raise ValueError(f"unsupported state version {payload.get('version')}")
            candidates = [candidate_from_dict(item) for item in payload["candidates"]]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            aside = self.path.with_suffix(self.path.suffix + ".corrupt")
            os.replace(self.path, aside)
            log.error("M2 candidate state unreadable (%s); moved to %s and starting fresh", exc, aside)
            return 0
        correlator.restore(candidates)
        log.info("restored %d incident candidates from %s", len(candidates), self.path)
        return len(candidates)
