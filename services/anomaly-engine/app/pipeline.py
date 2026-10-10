"""
The M2 pipeline (M2 runbook, Days 5, 6, 9, 12).

    TelemetrySnapshot  ->  score  ->  AnomalyEvent
                                          |
                                  alert?  v
                              correlate into an incident candidate
                              save the evidence
                              hand it to the shared API (if switched on)

Which detector scores a reading:

* If a frozen reference exists for the service, the per-service ruler
  (`z_score_per_service`): how many usual wobbles from this service's normal.
* Otherwise the threshold alarm (`threshold_baseline`), which needs no
  healthy history. A service nobody has trained on still gets watched.

The event's `model` field says which one scored it.

Awkward readings, handled on purpose:

* A reading older than the newest one already seen for that service is scored
  without "change since last time" features, and doesn't replace the newest.
  Otherwise one late reading would corrupt the next reading's change features.
* "Change since the last reading" only means something if the last reading is
  recent. If it is more than `change_max_age_s` old (M1 was down, say), the
  new reading is scored with no change features. Otherwise the first reading
  after an outage would look like a sudden jump.
* NaN or missing values score 0.0: missing data is never a confident anomaly.
* If the service's version changed just before an alert, that is recorded as
  deployment context on the incident (the runbook's "shared deployment
  context").

Handoff to the shared API (see handoff.py):

* The moment an incident opens, it is created there and its FIRST alert is
  linked, so downstream modules can start right away.
* The shared API tells M3 about the LATEST linked anomaly. A fault takes about
  a minute to show fully in M1's one-minute windows, so the first alert can be
  a half-developed reading. Once the incident has `handoff_settle_alerts`
  alerts, the STRONGEST alert is linked too (once), and from then on that is
  what M3 sees.
* A failed delivery never affects scoring. It is recorded on the incident and
  retried on a later alert, no sooner than `handoff_retry_s` seconds later.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from shared.contracts import AnomalyEvent, Incident, TelemetrySnapshot

from .baseline import (
    DEFAULT_CONFIG,
    MODEL_NAME as THRESHOLD_MODEL_NAME,
    BaselineConfig,
    baseline_score,
    breached_rules,
    is_alert,
)
from .correlation import Candidate, Correlator
from .evidence import EvidenceStore
from .features import FEATURE_ORDER, extract_features
from .handoff import HandoffError, SharedApiHandoff
from .model import ZScoreDetector
from .reference import DETECTOR_NAME as RULER_MODEL_NAME
from .reference import Reference

log = logging.getLogger("m2.pipeline")

# A feature counts as a "signal" of an incident when it is further from normal
# than the noisiest normal reading seen in training (stored with the reference),
# and never less than this many wobbles. If the reference doesn't record that,
# half the alert line is used.
MIN_SIGNAL_WOBBLES = 3.0

# "high" severity starts at this many times the alert line. A label only: it
# has not been evaluated, and nothing downstream should depend on it yet.
HIGH_SEVERITY_MULTIPLE = 4.0

_EPS = 1e-9


@dataclass(frozen=True)
class Reading:
    event: AnomalyEvent
    alert: bool
    detector: str
    wobbles: dict[str, float] | None       # per feature, ruler only
    incident: Incident | None              # the candidate this alert belongs to


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _finite(value: float) -> float | None:
    """JSON has no NaN; an unmeasurable value is written as null."""
    return float(value) if math.isfinite(value) else None


class Pipeline:
    def __init__(
        self,
        reference: Reference | None = None,
        config: BaselineConfig = DEFAULT_CONFIG,
        correlator: Correlator | None = None,
        evidence: EvidenceStore | None = None,
        context_window_s: float = 300.0,
        recent_limit: int = 500,
        change_max_age_s: float = 300.0,
        handoff: SharedApiHandoff | None = None,
        handoff_settle_alerts: int = 4,
        handoff_retry_s: float = 10.0,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.reference = reference
        self.config = config
        self.correlator = correlator or Correlator()
        self.evidence = evidence or EvidenceStore(None)
        self.context_window_s = context_window_s
        self.change_max_age_s = change_max_age_s
        self.handoff = handoff
        self.handoff_settle_alerts = handoff_settle_alerts
        self.handoff_retry_s = handoff_retry_s
        self.now = now
        self._previous: dict[str, TelemetrySnapshot] = {}
        self._version_change: dict[str, dict] = {}
        self._recent: deque[AnomalyEvent] = deque(maxlen=recent_limit)
        self._latest: dict[str, AnomalyEvent] = {}
        self._handoff_error: str | None = None
        self._lock = threading.RLock()
        use_async = os.getenv("M2_ASYNC_DISPATCH", "false").lower() in {"1", "true", "yes", "on"}
        if use_async:
            from app.dispatch import Dispatcher
            self._dispatcher = Dispatcher(self._deliver)
        else:
            self._dispatcher = None

    # -- what is configured ------------------------------------------------

    def uses_ruler_for(self, service: str) -> bool:
        return self.reference is not None and service in self.reference.services

    @property
    def detector_name(self) -> str:
        return RULER_MODEL_NAME if self.reference is not None else THRESHOLD_MODEL_NAME

    def handoff_status(self) -> str:
        """For /health: "disabled", "ok", or "degraded: <last error>"."""
        if self.handoff is None:
            return "disabled"
        return "ok" if self._handoff_error is None else f"degraded: {self._handoff_error}"

    # -- the work ----------------------------------------------------------

    def process(self, snapshot: TelemetrySnapshot) -> Reading:
        with self._lock:
            reading, opened = self._score(snapshot)
        if reading.alert and self.handoff is not None:
            if self._dispatcher is not None:
                self._dispatcher.submit(reading.incident.incident_id)
            else:
                self._deliver(reading.incident.incident_id)   # outside the lock: it talks to the network
        return reading

    def _score(self, snapshot: TelemetrySnapshot) -> tuple[Reading, bool]:
        service = snapshot.service
        previous = self._previous.get(service)
        in_order = previous is None or snapshot.timestamp >= previous.timestamp
        recent_enough = (
            previous is not None
            and in_order
            and (snapshot.timestamp - previous.timestamp).total_seconds() <= self.change_max_age_s
        )
        features = extract_features(snapshot, previous if recent_enough else None)
        if in_order:
            if previous is not None and previous.version != snapshot.version:
                self._version_change[service] = {
                    "from": previous.version,
                    "to": snapshot.version,
                    "at": snapshot.timestamp,
                }
            self._previous[service] = snapshot

        vector = features.as_vector()
        wobbles: dict[str, float] | None = None

        if self.uses_ruler_for(service):
            detector = self.reference.detector
            line = self.reference.alert
            score = float(detector.score(service, vector))
            wobbles = dict(zip(FEATURE_ORDER, detector.explain(service, vector)))
            alert = score >= line.score - _EPS
            severity = self._ruler_severity(score)
            model_name = RULER_MODEL_NAME
            floor = max(MIN_SIGNAL_WOBBLES, line.normal_max_wobbles or line.wobbles / 2)
            signals = sorted(n for n, w in wobbles.items() if w >= floor) if alert else []
        else:
            score, severity = baseline_score(features, self.config)
            alert = is_alert(score, self.config)
            model_name = THRESHOLD_MODEL_NAME
            signals = breached_rules(features, self.config) if alert else []

        event = AnomalyEvent(
            anomaly_id=f"ANO-{uuid.uuid4().hex[:8]}",
            timestamp=snapshot.timestamp,
            service=service,
            score=round(min(max(score, 0.0), 1.0), 4),
            severity=severity,
            model=model_name,
            features=features.to_contract(),
        )

        incident = None
        opened = False
        if alert:
            context = self._context_for(service, snapshot.timestamp)
            record = {
                "timestamp": _iso(snapshot.timestamp),
                "service": service,
                "anomaly_id": event.anomaly_id,
                "score": event.score,
                "severity": severity,
                "model": model_name,
                "features": {n: _finite(v) for n, v in zip(FEATURE_ORDER, vector)},
                "wobbles": None if wobbles is None else {n: _finite(w) for n, w in wobbles.items()},
                "signals": signals,
                "context": context,
            }
            known = {c.incident_id for c in self.correlator.candidates(service, limit=1)}
            candidate = self.correlator.observe(event, signals, context, record)
            opened = candidate.incident_id not in known
            self.evidence.append(record)
            incident = candidate.to_incident()
            self._recent.append(event)
            if opened:
                log.info(
                    "incident opened",
                    extra={"incident_id": candidate.incident_id, "service_name": service, "provider": "m2"},
                )

        self._latest[service] = event
        return Reading(event=event, alert=alert, detector=model_name, wobbles=wobbles, incident=incident), opened

    def _ruler_severity(self, score: float) -> str:
        line = self.reference.alert
        if score < line.score - _EPS:
            return "low"
        if ZScoreDetector.wobbles_of(score) >= HIGH_SEVERITY_MULTIPLE * line.wobbles:
            return "high"
        return "medium"

    def _context_for(self, service: str, moment: datetime) -> dict | None:
        change = self._version_change.get(service)
        if change is None:
            return None
        age = (moment - change["at"]).total_seconds()
        if not 0 <= age <= self.context_window_s:
            return None
        return {
            "version_change": {
                "from": change["from"],
                "to": change["to"],
                "at": _iso(change["at"]),
                "seconds_before_alert": age,
            }
        }

    # -- handing the incident on -------------------------------------------

    def _pending_steps(self, candidate: Candidate) -> list[tuple[str, object]]:
        state = candidate.handoff
        steps: list[tuple[str, object]] = []
        if not state.incident_created:
            steps.append(("incident", candidate.to_incident()))
        onset = candidate.onset()
        if onset.anomaly_id not in state.linked:
            steps.append(("onset", onset))
        if not state.peak_done and len(candidate.anomaly_events) >= self.handoff_settle_alerts:
            peak = candidate.peak()
            if peak.anomaly_id != onset.anomaly_id and peak.anomaly_id not in state.linked:
                steps.append(("peak", peak))
            else:
                state.peak_done = True       # the first alert already was the strongest
        return steps

    def _deliver(self, incident_id: str) -> None:
        with self._lock:
            candidate = self.correlator.get(incident_id)
            if candidate is None:
                return
            state = candidate.handoff
            now = self.now()
            if state.in_flight:
                return
            if state.error and state.last_attempt is not None \
                    and (now - state.last_attempt).total_seconds() < self.handoff_retry_s:
                return
            steps = self._pending_steps(candidate)
            if not steps:
                return
            state.in_flight = True
            state.attempts += 1
            state.last_attempt = now
            service = candidate.service

        done: list[tuple[str, object]] = []
        error: str | None = None
        try:
            for kind, item in steps:
                try:
                    if kind == "incident":
                        self.handoff.create_incident(item, now)
                    else:                                    # "onset" or "peak"
                        self.handoff.link_anomaly(incident_id, item)
                except HandoffError as exc:
                    error = str(exc)
                    break
                done.append((kind, item))
        finally:
            with self._lock:
                for kind, item in done:
                    if kind == "incident":
                        state.incident_created = True
                    else:
                        state.linked.add(item.anomaly_id)
                        if kind == "peak":
                            state.peak_done = True
                state.error = error
                state.in_flight = False
                self._handoff_error = error

        extra = {"incident_id": incident_id, "service_name": service, "provider": "shared-api"}
        if error:
            log.warning("handoff failed: %s", error, extra={**extra, "error_category": "handoff_failed"})
        elif done:
            log.info("handed to the shared API: %s", ", ".join(k for k, _ in done), extra=extra)

    # -- reading it back ---------------------------------------------------

    def recent_alerts(self, service: str | None = None, limit: int = 50) -> list[AnomalyEvent]:
        with self._lock:
            found = [e for e in self._recent if service is None or e.service == service]
        return list(reversed(found))[:limit]

    def latest(self, service: str) -> AnomalyEvent | None:
        with self._lock:
            return self._latest.get(service)

    def incidents(self, service: str | None = None, limit: int = 50) -> list[Incident]:
        with self._lock:
            return [c.to_incident() for c in self.correlator.candidates(service, limit)]

    def incident_detail(self, incident_id: str) -> dict | None:
        with self._lock:
            candidate = self.correlator.get(incident_id)
            return None if candidate is None else candidate.detail()

    def incident_anomaly(self, incident_id: str, pick: str = "peak") -> AnomalyEvent | None:
        """One anomaly for an incident: the form M3 asks for."""
        with self._lock:
            candidate = self.correlator.get(incident_id)
            return None if candidate is None else candidate.pick(pick)
