from fastapi import FastAPI
from app.api.incidents import router as incidents_router
from app.api.approvals import router as approvals_router
from app.api.decisions import router as decisions_router
from app.api.remediation import router as remediation_router

app = FastAPI(
    title="NEXUS Shared API",
    version="0.1.0"
)
app.include_router(incidents_router)
app.include_router(approvals_router)
app.include_router(decisions_router)
app.include_router(remediation_router)
@app.get("/health")
def health():
    return {
        "service": "shared-nexus-api",
        "status": "ok",
        "version": "0.1.0"
    }