"""
Hand one incident from a RUNNING M2 to the shared API, by hand.

    python -m app.realdata handoff --to http://127.0.0.1:18004

This is the same two-call sequence the team's operator script uses
(scripts/run-integration-incident.ps1), with M2's real anomaly in place of the
hand-written `manual-m1-observation` one. It is also what M2 does by itself
when M2_HANDOFF_ENABLED is on; this version lets you do it once, deliberately,
and see what came back.

It asks M2 for the incident and for its first and strongest anomalies (through
the same route M3's provider calls), creates the incident on the shared API,
links the anomalies, and finally reads `GET /internal/anomalies` back, which
is what M3 will see.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from shared.contracts import AnomalyEvent, Incident

from ..handoff import HandoffError, SharedApiHandoff
from .final import LiveCheckError, _get_json

FRESH_FOR_S = 300      # the team's script only accepts an anomaly from the last five minutes


def push_incident(m2_url: str, to_url: str, incident_id: str | None = None) -> list[str]:
    """Returns the lines to print. Raises LiveCheckError or HandoffError."""
    m2 = m2_url.rstrip("/")
    listed = _get_json(f"{m2}/internal/correlation/incidents?limit=50")
    if not listed:
        raise LiveCheckError("M2 has no incident candidates yet, so there is nothing to hand over")
    if incident_id is None:
        chosen = listed[0]                                  # newest first
    else:
        chosen = next((i for i in listed if i["incident_id"] == incident_id), None)
        if chosen is None:
            raise LiveCheckError(f"M2 has no incident {incident_id!r}")
    incident = Incident.model_validate(chosen)

    first = AnomalyEvent.model_validate(
        _get_json(f"{m2}/internal/anomalies?incident_id={quote(incident.incident_id)}&pick=first"))
    peak = AnomalyEvent.model_validate(
        _get_json(f"{m2}/internal/anomalies?incident_id={quote(incident.incident_id)}&pick=peak"))

    lines = [f"incident {incident.incident_id}: {len(incident.anomaly_ids)} alerts in M2, severity {incident.severity}"]
    age = (datetime.now(timezone.utc) - peak.timestamp).total_seconds()
    if age > FRESH_FOR_S:
        lines.append(f"NOTE: the newest evidence is {age / 60:.0f} minutes old. The team's operator script only "
                     "accepts an anomaly from the last five minutes, and M3/M4 may treat older evidence as stale.")

    shared = SharedApiHandoff(to_url)
    shared.create_incident(incident)
    lines.append(f"created the incident on {shared.base_url}")
    shared.link_anomaly(incident.incident_id, first)
    lines.append(f"linked the first alert  {first.anomaly_id}  (score {first.score}, {first.timestamp:%H:%M:%S})")
    if peak.anomaly_id != first.anomaly_id:
        shared.link_anomaly(incident.incident_id, peak)
        lines.append(f"linked the strongest    {peak.anomaly_id}  (score {peak.score}, {peak.timestamp:%H:%M:%S})")

    seen = _get_json(f"{shared.base_url}/internal/anomalies?incident_id={quote(incident.incident_id)}")
    lines.append(
        f"M3 will read: {seen['anomaly_id']}  model={seen['model']}  score={seen['score']}  "
        f"features={seen['features']}"
    )
    return lines


__all__ = ["push_incident", "HandoffError", "LiveCheckError"]
