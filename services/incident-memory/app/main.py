from contextlib import asynccontextmanager
import hmac
import logging
import os
from pathlib import Path
import sys

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from shared.config import get_runtime_settings
from shared.logging import configure_json_logging

from .models import StoreRequest, SearchRequest, CopilotRequest, ApprovalRequest, ChatRequest, ActionConfirmation
from .actions import ActionDrafts
from .chat import chat, chat_config
from .storage import Repository, StorageUnavailable, MemoryConflict
from .retrieval import search
from .copilot import answer
from .providers import Provider, ProviderUnavailable

settings = get_runtime_settings("incident-memory")
configure_json_logging(settings.service_name, settings.service_version, settings.environment, settings.log_level)
logger = logging.getLogger(__name__)


def create_app(repository=None, provider=None):
    repo = repository or Repository()
    upstream = provider or Provider()
    drafts = ActionDrafts()

    @asynccontextmanager
    async def lifespan(app):
        try:
            repo.initialize()
        except StorageUnavailable:
            logger.error("memory storage unavailable at startup")
        yield

    app = FastAPI(title="NEXUS Incident Memory & Copilot", version=settings.service_version, lifespan=lifespan)
    app.state.repository = repo
    app.state.provider = upstream
    app.state.action_drafts = drafts

    @app.middleware("http")
    async def refresh_ui_assets(request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(StorageUnavailable)
    async def storage_error(request, exc):
        return JSONResponse(status_code=503, content={"status": "unavailable", "detail": str(exc)})

    @app.exception_handler(ProviderUnavailable)
    async def provider_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"status": "degraded", "detail": str(exc), "source": upstream.mode})

    @app.exception_handler(MemoryConflict)
    async def conflict(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        try:
            repo.initialize()  # Also recovers after an initial DB outage.
            status, reason = "ok", None
        except StorageUnavailable:
            status, reason = "unavailable", "Incident memory database is unavailable"
        if status == "ok":
            if upstream.mode == "mock":
                status, reason = "degraded", "UI is using explicitly selected mock data"
            else:
                try:
                    upstream.overview()
                except ProviderUnavailable:
                    status, reason = "degraded", "Shared UI API is unavailable; memory endpoints remain available"
        return {"service": settings.service_name, "status": status, "version": settings.service_version,
                "environment": settings.environment, "storage": "postgresql" if repo.postgres else "sqlite",
                "ui_mode": upstream.mode, "reason": reason}

    @app.get("/ready")
    def ready():
        result = health()
        # Explicit mock previews may be ready, but unavailable storage/upstream is never ready.
        if result["status"] == "unavailable" or (upstream.mode == "live" and result["status"] != "ok"):
            return JSONResponse(status_code=503, content=result)
        return result

    @app.post("/internal/memory/store")
    def store(request: StoreRequest, raw: Request):
        token = os.getenv("M5_SHARED_API_TOKEN")
        if upstream.mode == "live" and not token:
            raise HTTPException(503, "Configure the private backend token before archiving incidents")
        if token and not hmac.compare_digest(raw.headers.get("authorization", ""), f"Bearer {token}"):
            raise HTTPException(401, "A valid backend authorization token is required")
        record, status = repo.save(request)
        logger.info("incident memory stored", extra={"incident_id": request.memory.incident_id})
        return {"status": status, "record": record}

    @app.post("/internal/memory/search")
    def memory_search(request: SearchRequest):
        return search(repo, request)

    @app.get("/internal/memory/{incident_id}")
    def memory_get(incident_id: str, source: str = Query("real", pattern="^(real|mock)$")):
        record = repo.get(incident_id, source)
        if not record:
            raise HTTPException(404, "No memory record exists for this incident and source")
        return {"record": record}

    @app.post("/internal/copilot/query")
    def copilot(request: CopilotRequest):
        return answer(repo, request)

    @app.get("/internal/ui/config")
    def config():
        return {"source": "mock" if upstream.mode == "mock" else "real", "ui_mode": upstream.mode,
                "approval_enabled": upstream.approval_enabled(),
                "operator": os.getenv("M5_OPERATOR_ID") if upstream.approval_enabled() else None,
                "chat": chat_config()}

    @app.post("/internal/copilot/chat")
    def copilot_chat(request: ChatRequest):
        return chat(repo, request, provider=upstream, drafts=drafts)

    @app.post("/internal/copilot/actions/{draft_id}/submit")
    def submit_action(draft_id: str, confirmation: ActionConfirmation, raw: Request):
        origin = raw.headers.get("origin")
        if origin and origin != str(raw.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin action requests are not accepted")
        return drafts.submit(draft_id, upstream)

    @app.post("/internal/copilot/actions/{draft_id}/decline")
    def decline_action(draft_id: str, raw: Request):
        origin = raw.headers.get("origin")
        if origin and origin != str(raw.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin action decisions are not accepted")
        return drafts.decline(draft_id)

    @app.get("/internal/ui/overview")
    def overview():
        return upstream.overview()

    @app.get("/internal/ui/incidents/{incident_id}")
    def detail(incident_id: str):
        result = upstream.detail(incident_id)
        if result is None:
            raise HTTPException(404, "Incident not found")
        archive_source = "real" if result.get("source") == "real" else "mock"
        archived = repo.get(incident_id, archive_source)
        if archived:
            result = {**result, "memory": archived["memory"], "archive": {
                "source": archived["source"], "stored_at": archived["stored_at"], "resolved": archived["resolved"]}}
        return result

    @app.post("/internal/ui/incidents/{incident_id}/approval")
    def approval(incident_id: str, request: ApprovalRequest, raw: Request):
        # Same-origin browser requests only. M4 still owns auth and approval policy.
        origin = raw.headers.get("origin")
        if origin and origin != str(raw.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin approvals are not accepted")
        return upstream.approval(incident_id, request.decision)

    app.mount("/assets", StaticFiles(directory=ROOT / "ui"), name="ui")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "ui" / "index.html")

    return app


app = create_app()
