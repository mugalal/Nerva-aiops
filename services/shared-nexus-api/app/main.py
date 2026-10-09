from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from app.api.incidents import router as incidents_router
from app.api.approvals import router as approvals_router
from app.api.decisions import router as decisions_router
from app.api.remediation import router as remediation_router
from app.api.recovery import router as recovery_router
from app.api.anomalies import router as anomalies_router
import os
from app.providers import rca_provider, finops_provider, evidence_provider, recovery_provider
from app.remediation import executor
app = FastAPI(
    title="NEXUS Shared API",
    version="0.1.0"
)
app.include_router(incidents_router)
app.include_router(approvals_router)
app.include_router(decisions_router)
app.include_router(remediation_router)
app.include_router(recovery_router)
app.include_router(anomalies_router)
@app.get("/health")
def health():
    modes = {
        "rca": rca_provider.RCA_PROVIDER, "finops": finops_provider.FINOPS_PROVIDER,
        "evidence": evidence_provider.EVIDENCE_PROVIDER, "recovery": recovery_provider.RECOVERY_PROVIDER,
    }
    return {
        "service": "shared-nexus-api",
        "status": "degraded" if "mock" in modes.values() or executor.REMEDIATION_BACKEND == "mock" else "ok",
        "version": "0.1.0",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "provider_modes": modes,
        "remediation_backend": executor.REMEDIATION_BACKEND,
    }
