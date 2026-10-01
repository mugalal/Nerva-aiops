from datetime import datetime, timezone
from pathlib import Path
import json
import logging
import sys

from fastapi import FastAPI, HTTPException, Query

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.config import get_runtime_settings
from shared.logging import configure_json_logging


settings = get_runtime_settings("telemetry-intelligence")
configure_json_logging(
    settings.service_name,
    settings.service_version,
    settings.environment,
    settings.log_level,
)
logger = logging.getLogger(__name__)

app = FastAPI(title="NEXUS Telemetry Intelligence", version=settings.service_version)


@app.get("/health")
def health() -> dict:
    return {
        "service": settings.service_name,
        "status": "ok",
        "version": settings.service_version,
        "environment": settings.environment,
    }


@app.get("/internal/telemetry/snapshot")
def telemetry_snapshot(service: str = Query(..., min_length=1)) -> dict:
    mock_path = ROOT / "mocks" / "mock_metrics.json"
    if not mock_path.exists():
        raise HTTPException(status_code=503, detail="mock telemetry fixture is unavailable")

    payload = json.loads(mock_path.read_text(encoding="utf-8"))
    if payload.get("service") != service:
        payload["service"] = service
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    logger.info("served telemetry snapshot", extra={"service_name": service})
    return payload


@app.post("/internal/recovery/validate")
def recovery_validate(request: dict) -> dict:
    service = request.get("service")
    if not service:
        raise HTTPException(status_code=422, detail="service is required")

    return {
        "incident_id": request.get("incident_id", "INC-UNKNOWN"),
        "recovered": False,
        "before": {},
        "after": {},
        "recovery_time_seconds": None,
        "slo_restored": False,
        "status": "unknown",
        "reason": "real recovery validation is not wired yet; M1 base scaffold is using explicit unknown state",
    }

