"""
The M2 pipeline (M2 runbook, Days 5, 6, 9, 12).

    TelemetrySnapshot  ->  score  ->  AnomalyEvent
                                          |
                                  alert?  v
                              correlate into an incident candidate
                              save the evidence

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
"""

from __future__ import annotations

import math
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from shared.contracts import AnomalyEvent, Incident, TelemetrySnapshot

from .baseline import (
    DEFAULT_CONFIG,
    MODEL_NAME as THRESHOLD_MODEL_NAME,
    BaselineConfig,
    baseline_score,
    breached_rules,
    is_alert,
)
from .correlation import Correlator
from .evidence import EvidenceStore
from .features import FEATURE_ORDER, extract_features
from .model import ZScoreDetector
from .reference import DETECTOR_NAME as RULER_MODEL_NAME
from .reference import Reference

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
    ) -> None:
        self.reference = reference
        self.config = config
        self.correlator = correlator or Correlator()
        self.evidence = evidence or EvidenceStore(None)
        self.context_window_s = context_window_s
        self.change_max_age_s = change_max_age_s
        self._previous: dict[str, TelemetrySnapshot] = {}
        self._version_change: dict[str, dict] = {}
        self._recent: deque[AnomalyEvent] = deque(maxlen=recent_limit)
        self._latest: dict[str, AnomalyEvent] = {}
        self._lock = threading.RLock()

    # -- what is configured ------------------------------------------------

    def uses_ruler_for(self, service: str) -> bool:
        return self.reference is not None and service in self.reference.services

    @property
    def detector_name(self) -> str:
        return RULER_MODEL_NAME if self.reference is not None else THRESHOLD_MODEL_NAME

    # -- the work ----------------------------------------------------------

    def process(self, snapshot: TelemetrySnapshot) -> Reading:
        service = snapshot.service
        with self._lock:
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
                candidate = self.correlator.observe(event, signals, context, record)
                self.evidence.append(record)
                incident = candidate.to_incident()
                self._recent.append(event)

            self._latest[service] = event
            return Reading(event=event, alert=alert, detector=model_name, wobbles=wobbles, incident=incident)

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
