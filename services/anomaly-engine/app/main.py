"""
M2 anomaly-engine service.

    GET  /health
    POST /internal/anomalies/evaluate            TelemetrySnapshot -> AnomalyEvent
    GET  /internal/anomalies?incident_id=...     ONE AnomalyEvent for an incident (what M3 asks for)
    GET  /internal/anomalies/recent              recent alerts, newest first
    GET  /internal/correlation/incidents         incident candidates (the frozen Incident contract)
    GET  /internal/correlation/incidents/{id}    one candidate plus the evidence behind it

(`/internal/anomalies/*` and `/internal/correlation/*` are M2's, per the
architecture document. The shared core's `/api/incidents/*` is not.)

Run (from services/anomaly-engine):
    uvicorn app.main:app --host 0.0.0.0 --port 8002

The shared runtime variables (docs/runtime-conventions.md) are read through
`shared.config`: SERVICE_NAME, SERVICE_VERSION, ENVIRONMENT, LOG_LEVEL,
M1_TELEMETRY_BASE_URL, SHARED_NEXUS_API_BASE_URL. Logs are structured JSON
through `shared.logging`. M2's own settings:

    M2_REFERENCE_PATH      the frozen reference. Unset: reference/reference.json if it
                           exists. "none": don't use one (threshold alarm only).
    M2_POLL_ENABLED        true to pull live snapshots from M1 (default false)
    M2_POLL_SERVICES       comma-separated services to watch (default payment-service)
    M2_POLL_INTERVAL_S     seconds between polls (default 15)
    M2_STALE_AFTER_S       a reading older than this is ignored (default 120)
    M2_CORRELATION_GAP_S   alerts closer together than this are one incident (default 120)
    M2_EVIDENCE_PATH       where alert evidence is appended. Unset: data/evidence.jsonl when
                           polling is on, nowhere otherwise. "none": nowhere.
    M2_HANDOFF_ENABLED     true to hand incidents to the shared API as they open (default false)
    M2_HANDOFF_URL         where the shared API is. Unset: SHARED_NEXUS_API_BASE_URL.
    M2_HANDOFF_SETTLE_ALERTS  alerts after which the strongest one is linked too (default 4)
    M2_JSON_LOGGING        "off" to leave logging alone (default on)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query

from shared.config.settings import get_runtime_settings
from shared.contracts import AnomalyEvent, Incident, TelemetrySnapshot
from shared.logging.json_logging import configure_json_logging

from .correlation import Correlator
from .evidence import EvidenceStore
from .handoff import SharedApiHandoff
from .pipeline import Pipeline
from .poller import Poller
from .realdata.m1_client import M1Client
from .reference import Reference, load_reference

SERVICE_NAME_DEFAULT = "anomaly-engine"

_SERVICE_DIR = Path(__file__).resolve().parents[1]
_OFF = {"", "none", "off"}


@dataclass
class State:
    pipeline: Pipeline
    poller: Poller | None
    reference_status: str
    interval_s: float


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _load_reference_from_env() -> tuple[Reference | None, str]:
    setting = os.getenv("M2_REFERENCE_PATH")
    if setting is not None and setting.strip().lower() in _OFF:
        return None, "disabled"
    path = Path(setting) if setting else _SERVICE_DIR / "reference" / "reference.json"
    if not path.exists():
        return None, "missing"
    try:
        return load_reference(path), "loaded"
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        # A broken reference must not stop the service: fall back to the
        # threshold alarm, and say so in /health.
        return None, f"invalid: {exc}"


def build_state() -> State:
    settings = get_runtime_settings(SERVICE_NAME_DEFAULT)
    reference, reference_status = _load_reference_from_env()
    polling = _truthy(os.getenv("M2_POLL_ENABLED"))

    evidence_setting = os.getenv("M2_EVIDENCE_PATH")
    if evidence_setting is not None:
        evidence_path = None if evidence_setting.strip().lower() in _OFF else evidence_setting
    else:
        evidence_path = str(_SERVICE_DIR / "data" / "evidence.jsonl") if polling else None

    handoff = None
    if _truthy(os.getenv("M2_HANDOFF_ENABLED")):
        handoff = SharedApiHandoff(os.getenv("M2_HANDOFF_URL") or settings.shared_nexus_api_base_url)

    pipeline = Pipeline(
        reference=reference,
        correlator=Correlator(gap_s=float(os.getenv("M2_CORRELATION_GAP_S", "120"))),
        evidence=EvidenceStore(evidence_path),
        handoff=handoff,
        handoff_settle_alerts=int(os.getenv("M2_HANDOFF_SETTLE_ALERTS", "4")),
    )

    poller = None
    if polling:
        services = [s.strip() for s in os.getenv("M2_POLL_SERVICES", "payment-service").split(",") if s.strip()]
        poller = Poller(
            M1Client(settings.m1_telemetry_base_url, timeout=5.0),
            pipeline,
            services,
            stale_after_s=float(os.getenv("M2_STALE_AFTER_S", "120")),
        )
    return State(pipeline, poller, reference_status, float(os.getenv("M2_POLL_INTERVAL_S", "15")))


_state = build_state()


def reset_state() -> None:
    """Forget everything and rebuild from the environment. Used by tests."""
    global _state
    _state = build_state()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if os.getenv("M2_JSON_LOGGING", "on").strip().lower() != "off":
        settings = get_runtime_settings(SERVICE_NAME_DEFAULT)
        configure_json_logging(settings.service_name, settings.service_version, settings.environment,
                               settings.log_level)
    task = None
    if _state.poller is not None:
        task = asyncio.create_task(_state.poller.run(_state.interval_s))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="M2 Anomaly Engine",
    version=get_runtime_settings(SERVICE_NAME_DEFAULT).service_version,
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    """The minimum health response from docs/runtime-conventions.md, plus what
    M2 depends on. "degraded" when M1 is being polled and something is wrong
    with it, when handing incidents to the shared API is failing, or when the
    reference file is unusable (the threshold alarm still runs, so M2 can keep
    scoring)."""
    settings = get_runtime_settings(SERVICE_NAME_DEFAULT)
    m1 = "disabled" if _state.poller is None else _state.poller.dependency_state()
    shared_api = _state.pipeline.handoff_status()
    degraded = (
        m1.startswith("degraded")
        or shared_api.startswith("degraded")
        or _state.reference_status.startswith("invalid")
    )
    return {
        "service": settings.service_name,
        "status": "degraded" if degraded else "ok",
        "version": settings.service_version,
        "environment": settings.environment,
        "detector": _state.pipeline.detector_name,
        "dependencies": {"m1": m1, "shared_api": shared_api, "reference": _state.reference_status},
    }


def _unknown_incident(incident_id: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"category": "unknown_incident", "message": f"no incident {incident_id!r}", "retryable": False},
    )


@app.post("/internal/anomalies/evaluate", response_model=AnomalyEvent)
def evaluate(snapshot: TelemetrySnapshot) -> AnomalyEvent:
    """Score one snapshot and return an AnomalyEvent.

    Every snapshot gets an event, including healthy ones (low score). Events
    that cross the alert line are also correlated into incident candidates,
    their evidence is saved, and (if switched on) they are handed to the
    shared API. The event is stamped with the snapshot's own timestamp, not
    the wall clock, so replayed history stays meaningful.
    """
    return _state.pipeline.process(snapshot).event


@app.get("/internal/anomalies", response_model=AnomalyEvent)
def incident_anomaly(
    incident_id: str = Query(min_length=1),
    pick: Literal["first", "peak", "latest"] = "peak",
) -> AnomalyEvent:
    """ONE anomaly for an incident, as M3's provider asks for it
    (`GET /internal/anomalies?incident_id=...`, one AnomalyEvent back).

    `pick` chooses which alert of the incident: `peak` (default) is the
    strongest, `first` is the onset, `latest` is the most recent.
    """
    event = _state.pipeline.incident_anomaly(incident_id, pick)
    if event is None:
        raise _unknown_incident(incident_id)
    return event


@app.get("/internal/anomalies/recent", response_model=list[AnomalyEvent])
def recent(service: str | None = None, limit: int = Query(50, ge=1, le=500)) -> list[AnomalyEvent]:
    """The most recent events that crossed the alert line, newest first."""
    return _state.pipeline.recent_alerts(service, limit)


@app.get("/internal/correlation/incidents", response_model=list[Incident])
def incidents(service: str | None = None, limit: int = Query(50, ge=1, le=200)) -> list[Incident]:
    """Incident candidates, newest first. One candidate per fault, however
    many alerts or metrics it took to see it."""
    return _state.pipeline.incidents(service, limit)


@app.get("/internal/correlation/incidents/{incident_id}")
def incident_detail(incident_id: str) -> dict:
    """One candidate with its evidence: the signals involved, deployment
    context, how much has been handed to the shared API, and every alert with
    all eight features and per-feature distances."""
    detail = _state.pipeline.incident_detail(incident_id)
    if detail is None:
        raise _unknown_incident(incident_id)
    return detail
