"""
Client for M1's telemetry API (M2 runbook, Day 3).

Reads what M1 serves, exactly as the frozen contract defines it:

    GET /health
    GET /internal/telemetry/snapshot?service=...
    GET /internal/telemetry/window?service=...&start=...&end=...&step_seconds=15

Rules, taken from docs/runtime-conventions.md ("Never fabricate telemetry"):

* If M1 answers with an error, raise M1Error carrying M1's own category and
  message. Never return a made-up reading in its place.
* If M1 can't be reached, raise M1Error. Never fall back to mock data.
* An empty window is an error, not an empty success.

Only the standard library is used, so nothing extra needs installing.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from shared.contracts import Metrics, TelemetrySnapshot

DEFAULT_M1_URL = "http://localhost:8001"


class M1Error(Exception):
    """M1 could not give us real data. Carries M1's own words where it has any."""

    def __init__(self, message: str, category: str = "unknown", status: int | None = None,
                 retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.category = category
        self.status = status
        self.retryable = retryable

    def __str__(self) -> str:
        where = f" (HTTP {self.status})" if self.status else ""
        return f"M1 error [{self.category}]{where}: {self.message}"


def _utc_text(moment: datetime) -> str:
    """2026-09-27T10:00:00Z, the format M1's query parameters accept."""
    if moment.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class M1Client:
    def __init__(self, base_url: str = DEFAULT_M1_URL, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise self._error_from(exc) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise M1Error(
                f"could not reach M1 at {self.base_url}: {getattr(exc, 'reason', exc)}",
                category="unreachable",
                retryable=True,
            ) from None
        except json.JSONDecodeError:
            raise M1Error(f"M1 at {url} did not return JSON", category="bad_response") from None

    @staticmethod
    def _error_from(exc: urllib.error.HTTPError) -> M1Error:
        """M1 reports errors as {"detail": {"category", "message", "retryable"}}."""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", {})
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            detail = {}
        if not isinstance(detail, dict):
            detail = {"message": str(detail)}
        return M1Error(
            detail.get("message", exc.reason or "request failed"),
            category=detail.get("category", f"http_{exc.code}"),
            status=exc.code,
            retryable=bool(detail.get("retryable", False)),
        )

    # -- endpoints --------------------------------------------------------

    def health(self) -> dict:
        return self._get("/health")

    def snapshot(self, service: str) -> TelemetrySnapshot:
        return TelemetrySnapshot.model_validate(
            self._get("/internal/telemetry/snapshot", {"service": service})
        )

    def window(
        self,
        service: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 15,
    ) -> list[TelemetrySnapshot]:
        """Verify the upstream identity and sampling grid before using history."""
        body = self._get(
            "/internal/telemetry/window",
            {
                "service": service,
                "start": _utc_text(start),
                "end": _utc_text(end),
                "step_seconds": step_seconds,
            },
        )
        points = body.get("points")
        if not points:
            raise M1Error(
                f"M1 returned no readings for {service} between {_utc_text(start)} and {_utc_text(end)}",
                category="no_data",
            )
        def parse_time(value):
            try:
                result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (AttributeError, ValueError, TypeError):
                raise M1Error("M1 returned an invalid history timestamp", category="bad_response") from None
            if result.tzinfo is None or result.utcoffset() is None:
                raise M1Error("M1 returned a timezone-naive history timestamp", category="bad_response")
            return result

        expected_start, expected_end = parse_time(_utc_text(start)), parse_time(_utc_text(end))
        version = body.get("version")
        if (body.get("service") != service or not isinstance(version, str) or not version.strip()
                or version == "unknown" or body.get("step_seconds") != step_seconds
                or parse_time(body.get("start")) != expected_start
                or parse_time(body.get("end")) != expected_end or not isinstance(points, list)):
            raise M1Error("M1 history identity, boundaries or sampling step do not match the request",
                          category="bad_response")
        times = [parse_time(point.get("timestamp")) for point in points]
        if (any(a >= b for a, b in zip(times, times[1:]))
                or any(not expected_start <= stamp <= expected_end for stamp in times)
                or any(abs((stamp - expected_start).total_seconds() / step_seconds
                           - round((stamp - expected_start).total_seconds() / step_seconds)) > 1e-6
                       for stamp in times)):
            raise M1Error("M1 history has duplicate, unordered, out-of-range or misaligned readings",
                          category="bad_response")
        snapshots = [
            TelemetrySnapshot(
                timestamp=point["timestamp"],
                service=service,
                version=version,
                metrics=Metrics.model_validate(point["metrics"]),   # strict: extras rejected
            )
            for point in points
        ]
        return snapshots
