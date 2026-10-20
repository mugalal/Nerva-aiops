from dotenv import load_dotenv
from contextlib import asynccontextmanager

load_dotenv()

import logging
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from app.api.incidents import router as incidents_router
from app.api.approvals import router as approvals_router
from app.api.decisions import router as decisions_router
from app.api.remediation import router as remediation_router
from app.api.recovery import router as recovery_router
from app.api.anomalies import router as anomalies_router
from app.api.experiments import router as experiments_router
from app.api.services import router as services_router
from app.api.auth import (approval_token, ingest_token, named_approver_tokens, read_auth_required,
                          verify_reader_auth)
from app.db import workflow_store
from app.orchestration import leadership
from app.orchestration.lifecycle import restore_state, validate_settings, Worker
from app.orchestration.provenance import configured_modes
from app.providers import rca_provider, finops_provider, evidence_provider, recovery_provider
from app.providers.errors import sanitize_message
from app.remediation import executor
from shared.registry import get_registry, registry_info

logger = logging.getLogger(__name__)


def validate_startup():
    """Refuse to start with a configuration that real operation cannot run safely."""
    real = executor.REMEDIATION_BACKEND != "mock"
    if real and not approval_token():
        raise RuntimeError("Real remediation requires a configured operator approval token")
    if "real" in configured_modes().values() and not ingest_token():
        raise RuntimeError("Real providers require NEXUS_INGEST_TOKEN for incident ingest and diagnosis")
    if ingest_token() and approval_token() and ingest_token() == approval_token():
        raise RuntimeError("NEXUS_INGEST_TOKEN must differ from the operator approval token")
    named = named_approver_tokens()  # RuntimeError on a malformed personal-token list.
    if any(secret in {ingest_token(), approval_token()} for secret in named):
        raise RuntimeError("A personal approver token must differ from the shared ingest and operator tokens")
    executor.hpa_policy()  # ValueError on an unknown autoscaler policy.
    get_registry()  # RegistryError: an explicitly configured registry must load.
    validate_settings()
    leadership.retry_seconds()
    return real


@asynccontextmanager
async def lifespan(app):
    real = validate_startup()
    workflow_store.initialize(real_execution=real)
    runtime.reset()
    if workflow_store.backend() == "postgres" and leadership.enabled():
        runtime.current.start_election()   # serve now; lead once the lease is won
    else:
        runtime.current.run_single(workflow_store.acquire_lease())
    try:
        yield
    finally:
        runtime.current.close()


def _lead(lease):
    restore_state()
    worker = Worker(lease)
    worker.start()
    return worker


class _RuntimeHolder:
    def __init__(self):
        self.current = leadership.Runtime(_lead)

    def reset(self):
        self.current = leadership.Runtime(_lead)


runtime = _RuntimeHolder()


app = FastAPI(
    title="NEXUS Shared API",
    version="0.1.0", lifespan=lifespan
)
app.include_router(incidents_router)
app.include_router(approvals_router)
app.include_router(decisions_router)
app.include_router(remediation_router)
app.include_router(recovery_router)
app.include_router(anomalies_router)
app.include_router(experiments_router)
app.include_router(services_router)


_OPEN_PATHS = {"/live", "/health", "/ready", "/metrics", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def read_authentication(request, call_next):
    """NEXUS_REQUIRE_READ_AUTH: incident, anomaly and audit reads need a NEXUS token too."""
    if request.method in {"GET", "HEAD"} and request.url.path not in _OPEN_PATHS:
        try:
            verify_reader_auth(request.headers.get("authorization"), request.headers.get("x-nexus-read-token"))
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
    return await call_next(request)


@app.exception_handler(workflow_store.StorageError)
async def storage_error(request, exc):
    category = ("lease_lost" if isinstance(exc, workflow_store.LeaseLost)
                else "conflict" if isinstance(exc, workflow_store.StorageConflict) else "storage_unavailable")
    status = 409 if category == "conflict" else 503
    return JSONResponse(status_code=status, content={"detail": {
        "category": category, "message": sanitize_message(str(exc)), "retryable": category != "lease_lost"}})


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    logger.exception("Unhandled M4 error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": {
        "category": "internal_error", "message": "Internal error; see M4 logs", "retryable": False}})


@app.get("/live")
def live():
    """Liveness: the process can serve and still owns its writer lease. No I/O."""
    if workflow_store.lease_lost():
        return JSONResponse(status_code=503, content={"status": "lease_lost"})
    return {"status": "alive"}


@app.get("/health")
def health():
    if workflow_store.lease_lost():
        return JSONResponse(status_code=503, content={
            "service": "shared-nexus-api",
            "status": "unavailable",
            "detail": "Writer lease lost; instance must restart",
        })
    try:
        workflow_store.probe()
    except workflow_store.StorageError:
        pass
    modes = {
        "rca": rca_provider.RCA_PROVIDER, "finops": finops_provider.FINOPS_PROVIDER,
        "evidence": evidence_provider.EVIDENCE_PROVIDER, "recovery": recovery_provider.RECOVERY_PROVIDER,
    }
    storage = workflow_store.health()
    from app.orchestration import archive
    breakers = {"rca": rca_provider._breaker.state, "finops": finops_provider._breaker.state,
                "evidence": evidence_provider._breaker.state, "recovery": recovery_provider._breaker.state,
                "memory_archive": archive._archive_breaker.state}
    return {
        "service": "shared-nexus-api",
        "status": "unavailable" if storage["error"] else "degraded" if "mock" in modes.values() or executor.REMEDIATION_BACKEND == "mock" or not storage["durable"] else "ok",
        "version": "0.1.0",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "provider_modes": modes,
        "remediation_backend": executor.REMEDIATION_BACKEND,
        "workflow_storage": storage,
        "dependency_breakers": breakers,
        "service_registry": registry_info(),
        "auth": {"operator_token_configured": bool(approval_token()), "ingest_token_configured": bool(ingest_token()),
                 "personal_approvers": len(named_approver_tokens()),
                 "read_auth_required": read_auth_required()},
    }


@app.get("/ready")
def ready():
    if workflow_store.is_standby():
        # Alive, but not the leader: the Service must not route traffic here.
        return JSONResponse(status_code=503, content={"service": "shared-nexus-api", "status": "standby",
                                                      "detail": "Standby replica; another M4 replica is the leader"})
    result = health()
    if isinstance(result, JSONResponse):
        return result
    if result["status"] == "unavailable" or (executor.REMEDIATION_BACKEND != "mock" and result["status"] != "ok"):
        return JSONResponse(status_code=503, content=result)
    return result
