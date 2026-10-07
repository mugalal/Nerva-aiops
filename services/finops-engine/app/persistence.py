"""Thin bridge between M6 and the shared DB package. Never breaks an API response."""
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("finops-engine")

# app/persistence.py -> app -> finops-engine -> services
_DB_PARENT = Path(__file__).resolve().parents[2] / "shared-nexus-api" / "app"


def _ensure_path() -> None:
    if str(_DB_PARENT) not in sys.path:
        sys.path.insert(0, str(_DB_PARENT))


def db_configured() -> bool:
    return bool(os.getenv("DATABASE_URL"))


def db_reachable() -> bool:
    try:
        _ensure_path()
        from db.connection import connect

        with connect(timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def _insert(values: dict) -> bool:
    if not db_configured():
        return False
    try:
        _ensure_path()
        from db.repository import insert_record

        insert_record("finops_recommendations", values)
        return True
    except Exception:
        log.exception("could not persist finops record")
        return False


def save_recommendation(req, resp) -> bool:
    rec = resp.recommendation
    return _insert({
        "kind": "rightsizing",
        "service": rec.service,
        "status": resp.status,
        "reason": resp.reason,
        "current_replicas": rec.current.replicas,
        "current_cpu_request_m": rec.current.cpu_request_m,
        "current_memory_request_mb": rec.current.memory_request_mb,
        "recommended_replicas": rec.recommended.replicas,
        "recommended_cpu_request_m": rec.recommended.cpu_request_m,
        "recommended_memory_request_mb": rec.recommended.memory_request_mb,
        "estimated_monthly_saving_pct": rec.estimated_monthly_saving_pct,
        "reliability_risk": rec.reliability_risk,
        "assumptions": resp.assumptions,
        "window_start": req.window.start,
        "window_end": req.window.end,
    })


def save_scale_options(req, ctx) -> bool:
    return _insert({
        "kind": "scale_options",
        "service": ctx.service,
        "incident_id": req.incident_id,
        "status": "OPTIONS_PROVIDED" if ctx.temporary_scale_options else "NO_OPTIONS",
        "current_replicas": req.current_replicas,
        "current_cpu_request_m": req.cpu_request_m,
        "current_memory_request_mb": req.memory_request_mb,
        "options": [o.model_dump() for o in ctx.temporary_scale_options],
    })