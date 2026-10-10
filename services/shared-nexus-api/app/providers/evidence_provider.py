from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import requests
from app.contracts import DeploymentEvent, TelemetrySnapshot
from app.providers.errors import IntegrationError
from app.providers.resilient import Breaker, make_session, resilient_call, CircuitOpen

EVIDENCE_PROVIDER = os.getenv("EVIDENCE_PROVIDER", "real").lower()
M1_EVIDENCE_URL = os.getenv("M1_EVIDENCE_URL", os.getenv("M1_TELEMETRY_BASE_URL", "http://localhost:8001").rstrip("/") + "/internal/evidence/capture")

_breaker = Breaker(threshold=5, cooldown=30.0)
_session = make_session(retry_post=True)


def capture_incident_evidence(incident_id: str, service: str, scenario: str, incident_started_at: str | None = None):
    m1_scenario = "bad_deployment" if scenario == "faulty_deployment" else scenario
    if m1_scenario not in {"bad_deployment", "traffic_spike"}:
        raise ValueError("Unsupported remediation scenario")
    if EVIDENCE_PROVIDER == "mock":
        mocks = Path(__file__).resolve().parents[4] / "mocks"
        before = json.loads((mocks / "mock_metrics.json").read_text(encoding="utf-8"))
        before["service"] = service
        deployment = json.loads((mocks / "mock_deployment_event.json").read_text(encoding="utf-8"))
        if m1_scenario == "bad_deployment":
            before["version"] = deployment["new_version"]
        return {"incident_id": incident_id, "service": service, "scenario": m1_scenario,
                "captured_at": datetime.now(timezone.utc).isoformat(), "before": before,
                "deployment_event": deployment,
                "source": "mock"}
    if EVIDENCE_PROVIDER != "real":
        raise ValueError(f"Unsupported evidence provider: {EVIDENCE_PROVIDER}")
    try:
        response = resilient_call(_breaker, _session, "POST", M1_EVIDENCE_URL, json={"incident_id": incident_id, "service": service, "scenario": m1_scenario}, timeout=10)
    except CircuitOpen as exc:
        raise IntegrationError("M1 evidence capture circuit is open", status_code=503, retryable=True) from exc
    except requests.RequestException as exc:
        raise IntegrationError("M1 evidence capture is unavailable", status_code=503, retryable=True) from exc
    if response.status_code != 200:
        is_client_error = 400 <= response.status_code < 500
        raise IntegrationError(
            f"M1 evidence capture failed with status {response.status_code}",
            status_code=response.status_code if is_client_error else 503,
            retryable=not is_client_error,
        )
    try:
        data = response.json()
        before = TelemetrySnapshot.model_validate(data["before"])
        if (data["incident_id"] != incident_id or data["service"] != service
                or data["scenario"] != m1_scenario or before.service != service):
            raise ValueError("identity mismatch")
        captured_at = datetime.fromisoformat(data["captured_at"].replace("Z", "+00:00"))
        if (captured_at.tzinfo is None or captured_at > datetime.now(timezone.utc) + timedelta(seconds=5)
                or before.timestamp > captured_at + timedelta(seconds=5)):
            raise ValueError("invalid capture timestamp")
        if m1_scenario == "bad_deployment":
            deployment = DeploymentEvent.model_validate(data["deployment_event"])
            if (deployment.service != service or deployment.new_version != before.version
                    or deployment.old_version.lower() in {"unknown", "n/a"}
                    or deployment.old_version == deployment.new_version
                    or deployment.timestamp > captured_at + timedelta(seconds=5)):
                raise ValueError("invalid rollback deployment evidence")
        baseline = data.get("baseline")
        baseline_version = deployment.old_version if m1_scenario == "bad_deployment" else before.version
        if not isinstance(baseline, dict) or baseline.get("service") != service or baseline.get("version") != baseline_version:
            raise ValueError("matching measured recovery baseline is required")
        measured = datetime.fromisoformat(baseline["measured_at"].replace("Z", "+00:00"))
        start = datetime.fromisoformat(baseline["window_start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(baseline["window_end"].replace("Z", "+00:00"))
        count = baseline["sample_count"]
        if (any(value.tzinfo is None for value in (measured, start, end))
                or isinstance(count, bool) or not isinstance(count, int) or count < 5
                or (end - start).total_seconds() < 60 or end > measured
                or measured > captured_at + timedelta(seconds=5)):
            raise ValueError("recovery baseline measurement or coverage is invalid")
        if incident_started_at is None:
            raise ValueError("Incident start is required for a pre-incident recovery baseline")
        incident_start = datetime.fromisoformat(incident_started_at.replace("Z", "+00:00"))
        if incident_start.tzinfo is None or measured > incident_start + timedelta(seconds=5):
            raise ValueError("recovery baseline must have been measured before this incident")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise IntegrationError("M1 capture returned invalid or mismatched evidence", status_code=502, retryable=False) from exc
    return data
