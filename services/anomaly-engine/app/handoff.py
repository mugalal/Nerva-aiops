"""
Handoff to the shared API (M2 runbook: "Produces AnomalyEvent/incident
candidates for M3/shared core").

The team's integration (docs/REAL_SERVICE_INTEGRATION.md and
scripts/run-integration-incident.ps1) feeds an incident into the system in two
calls, which this client repeats exactly:

    1. POST /api/incidents/                       create the incident
         { incident_id, started_at, severity, affected_services, anomaly_ids: [] }
    2. POST /internal/anomalies?incident_id=...   link an anomaly to it
         the AnomalyEvent, unchanged

Until now an operator wrote the anomaly by hand and labelled it
`manual-m1-observation`. This sends M2's real events instead.

What the shared API enforces, and what that means here (read from its routes):

* The path is `/api/incidents/` WITH the trailing slash. Without it the server
  answers 307, which a POST does not follow. The exact path is used.
* `started_at` may not be in the future. M1's clock and the shared API's can
  differ by a few seconds, so the incident start is capped at the current time.
* An anomaly may not be in the future (422) and may not be altered after it is
  stored (409 if the same id arrives with different evidence). The event is
  never altered; if it is rejected as "future" it is retried on a later alert.
* An incident that already exists answers 409. That is treated as success so a
  half-finished handoff can be completed.
* `GET /internal/anomalies?incident_id=...` (what M3 calls) returns the LATEST
  linked anomaly by timestamp.

Failures are returned as HandoffError. The caller keeps scoring regardless.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from shared.contracts import AnomalyEvent, Incident


class HandoffError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status

    def __str__(self) -> str:
        return f"HTTP {self.status}: {self.message}" if self.status else self.message


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _detail_text(raw: bytes) -> str:
    """The shared API reports errors as {"detail": "..."} or a list of problems."""
    try:
        detail = json.loads(raw.decode("utf-8")).get("detail", "")
    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return raw.decode("utf-8", errors="replace")[:200]
    if isinstance(detail, list):
        return "; ".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail)[:300]
    return str(detail)[:300]


class SharedApiHandoff:
    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict, query: dict | None = None) -> tuple[int, bytes]:
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        try:
            data = json.dumps(payload, allow_nan=False).encode("utf-8")
        except ValueError:
            raise HandoffError("the event contains a value that cannot be sent (NaN or infinity)") from None
        headers = {"Content-Type": "application/json"}
        token = os.getenv("NEXUS_INGEST_TOKEN") or os.getenv("NEXUS_SERVICE_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HandoffError(f"shared API unreachable at {self.base_url}: {getattr(exc, 'reason', exc)}") from None

    def create_incident(self, incident: Incident, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        started = min(incident.started_at, now)          # the shared API rejects a start in the future
        status, raw = self._post(
            "/api/incidents/",
            {
                "incident_id": incident.incident_id,
                "started_at": _iso(started),
                "severity": incident.severity,
                "affected_services": list(incident.affected_services),
                "anomaly_ids": [],
            },
        )
        if status == 200 or status == 409:                # 409: it already exists, which is fine
            return
        raise HandoffError(f"creating the incident was refused: {_detail_text(raw)}", status)

    def link_anomaly(self, incident_id: str, event: AnomalyEvent) -> None:
        status, raw = self._post(
            "/internal/anomalies",
            event.model_dump(mode="json"),
            query={"incident_id": incident_id},
        )
        if status == 200:
            return
        raise HandoffError(f"linking the anomaly was refused: {_detail_text(raw)}", status)
