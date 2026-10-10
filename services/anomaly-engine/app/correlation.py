"""
Incident correlation (M2 runbook, Day 6).

    "Group related anomaly signals by service, time proximity, shared
     deployment/context. Output one incident candidate, not one incident per
     metric."

A fault shows up as a run of alerts: a bad deployment alerts on every reading
for as long as it lasts, and on several metrics at once. Passed on as they
come, that is dozens of alerts for a single problem. The correlator turns the
run into ONE candidate:

* same service
* alerts within `gap_s` seconds of the previous one join the same candidate
  (the gap tolerates a missed reading or a brief dip back under the line)
* a longer silence ends it; the next alert starts a new candidate

Each candidate records the signals (metrics) that were abnormal at any point,
which is the runbook's question "which abnormal signals belong to the same
incident?", plus any deployment context seen just before it began.

The candidate is handed on as the frozen `Incident` contract with status
DETECTED. Moving it through the later states is for the modules after M2.

Each candidate keeps its actual `AnomalyEvent`s, so a consumer can ask for one
(the first, the strongest, or the latest) and the handoff to the shared API can
say what has already been delivered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from shared.contracts import AnomalyEvent, Incident

_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class HandoffState:
    """How much of this incident the shared API has been given."""

    incident_created: bool = False
    linked: set[str] = field(default_factory=set)       # anomaly ids the shared API now holds
    peak_done: bool = False                             # the strongest alert was linked after settling
    attempts: int = 0
    last_attempt: datetime | None = None
    error: str | None = None
    in_flight: bool = False

    def summary(self) -> dict:
        return {
            "incident_created": self.incident_created,
            "linked_anomaly_ids": sorted(self.linked),
            "attempts": self.attempts,
            "last_attempt": None if self.last_attempt is None
            else self.last_attempt.isoformat().replace("+00:00", "Z"),
            "error": self.error,
        }


@dataclass
class Candidate:
    incident_id: str
    service: str
    started_at: datetime
    last_alert_at: datetime
    severity: str
    anomaly_ids: list[str] = field(default_factory=list)
    signals: set[str] = field(default_factory=set)
    peak_score: float = 0.0
    context: dict | None = None
    events: list[dict] = field(default_factory=list)
    anomaly_events: list[AnomalyEvent] = field(default_factory=list)
    handoff: HandoffState = field(default_factory=HandoffState)

    def onset(self) -> AnomalyEvent:
        """The first alert: when the incident began."""
        return min(self.anomaly_events, key=lambda e: e.timestamp)

    def peak(self) -> AnomalyEvent:
        """The strongest alert so far (the latest one if scores tie)."""
        return max(self.anomaly_events, key=lambda e: (e.score, e.timestamp))

    def latest(self) -> AnomalyEvent:
        return max(self.anomaly_events, key=lambda e: e.timestamp)

    def pick(self, which: str) -> AnomalyEvent:
        return {"first": self.onset, "peak": self.peak, "latest": self.latest}[which]()

    def to_incident(self) -> Incident:
        return Incident(
            incident_id=self.incident_id,
            started_at=self.started_at,
            status="DETECTED",
            severity=self.severity,
            affected_services=[self.service],
            anomaly_ids=list(self.anomaly_ids),
        )

    def detail(self) -> dict:
        """The incident plus the evidence behind it, for M3 and M5."""
        return {
            "incident": self.to_incident().model_dump(mode="json"),
            "last_alert_at": self.last_alert_at.isoformat().replace("+00:00", "Z"),
            "alert_count": len(self.anomaly_ids),
            "peak_score": self.peak_score,
            "signals": sorted(self.signals),
            "context": self.context,
            "handoff": self.handoff.summary(),
            "events": self.events,
        }


class Correlator:
    def __init__(self, gap_s: float = 120.0, max_candidates: int = 200) -> None:
        self.gap_s = gap_s
        self.max_candidates = max_candidates
        self._open: dict[str, Candidate] = {}
        self._all: list[Candidate] = []

    def observe(
        self,
        event: AnomalyEvent,
        signals: list[str],
        context: dict | None,
        record: dict,
    ) -> Candidate:
        """Add one alert. Returns the candidate it joined or started.

        `record` is the evidence line for this alert; the incident id is added
        to it here, since only now is it known.
        """
        candidate = self._open.get(event.service)
        if candidate is not None:
            gap = abs((event.timestamp - candidate.last_alert_at).total_seconds())
            if gap > self.gap_s:
                candidate = None

        if candidate is None:
            candidate = Candidate(
                incident_id=f"INC-{event.timestamp:%Y%m%d-%H%M%S}-{event.service}",
                service=event.service,
                started_at=event.timestamp,
                last_alert_at=event.timestamp,
                severity=event.severity,
                context=context,
            )
            self._open[event.service] = candidate
            self._all.append(candidate)
            del self._all[: max(0, len(self._all) - self.max_candidates)]

        candidate.started_at = min(candidate.started_at, event.timestamp)
        candidate.last_alert_at = max(candidate.last_alert_at, event.timestamp)
        if _RANK.get(event.severity, 0) > _RANK.get(candidate.severity, 0):
            candidate.severity = event.severity
        candidate.peak_score = max(candidate.peak_score, event.score)
        candidate.signals.update(signals)
        if candidate.context is None and context is not None:
            candidate.context = context

        record["incident_id"] = candidate.incident_id
        candidate.anomaly_ids.append(event.anomaly_id)
        candidate.anomaly_events.append(event)
        candidate.events.append(record)
        return candidate

    def candidates(self, service: str | None = None, limit: int = 50) -> list[Candidate]:
        """Newest first."""
        found = [c for c in self._all if service is None or c.service == service]
        return list(reversed(found))[:limit]

    def get(self, incident_id: str) -> Candidate | None:
        return next((c for c in self._all if c.incident_id == incident_id), None)
