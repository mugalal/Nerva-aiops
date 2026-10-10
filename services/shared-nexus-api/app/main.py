from dotenv import load_dotenv
from contextlib import asynccontextmanager

load_dotenv()

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.api.incidents import router as incidents_router
from app.api.approvals import router as approvals_router
from app.api.decisions import router as decisions_router
from app.api.remediation import router as remediation_router
from app.api.recovery import router as recovery_router
from app.api.anomalies import router as anomalies_router
import os
from app.providers import rca_provider, finops_provider, evidence_provider, recovery_provider
from app.remediation import executor
from app.db import workflow_store
from app.orchestration.lifecycle import restore_state, Worker
from app.api.experiments import router as experiments_router


@asynccontextmanager
async def lifespan(app):
    real = executor.REMEDIATION_BACKEND != "mock"
    if real and not (os.getenv("NEXUS_APPROVAL_TOKEN") or os.getenv("M5_SHARED_API_TOKEN")):
        raise RuntimeError("Real remediation requires a configured operator approval token")
    workflow_store.initialize(real_execution=real)
    lease = workflow_store.acquire_lease()
    worker = None
    try:
        restore_state()
        worker = Worker(lease)
        worker.start()
        try:
            yield
        finally:
            worker.close()
    finally:
        # A bounded in-flight read/notification may outlive shutdown. Keep the
        # writer lease until it finishes, so another process cannot overlap it.
        if worker is None or not worker.thread.is_alive():
            workflow_store.close_lease(lease)


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


@app.exception_handler(workflow_store.StorageError)
async def storage_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc), "retryable": True})


@app.get("/health")
async def health():
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
    return {
        "service": "shared-nexus-api",
        "status": "unavailable" if storage["error"] else "degraded" if "mock" in modes.values() or executor.REMEDIATION_BACKEND == "mock" or not storage["durable"] else "ok",
        "version": "0.1.0",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "provider_modes": modes,
        "remediation_backend": executor.REMEDIATION_BACKEND,
        "workflow_storage": storage,
    }


@app.get("/ready")
async def ready():
    result = await health() if isinstance(health, type(ready)) else health()
    if isinstance(result, JSONResponse):
        return result
    if result["status"] == "unavailable" or (executor.REMEDIATION_BACKEND != "mock" and result["status"] != "ok"):
        return JSONResponse(status_code=503, content=result)
    return result
