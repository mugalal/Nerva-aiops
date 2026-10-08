import sys
from pathlib import Path

from fastapi import FastAPI

# Make the repo root importable so we can use shared/ (same trick as M1).
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.config.settings import get_runtime_settings  # noqa: E402

from . import cost_model, persistence
from .models import (  # noqa: E402
    FinOpsContext,
    HealthResponse,
    LiveRecommendRequest,
    RecommendRequest,
    RecommendResponse,
    ScaleOptionsRequest,
)
from .rightsizing import recommend  # noqa: E402
from .scale_options import build_scale_options  # noqa: E402
from .live_recommend import recommend_live

SERVICE_NAME = "finops-engine"


def compute_status() -> str:
    # "degraded" when a database is configured but unreachable.
    if persistence.db_configured() and not persistence.db_reachable():
        return "degraded"
    return "ok"


def create_app() -> FastAPI:
    settings = get_runtime_settings(SERVICE_NAME)
    api = FastAPI(title=SERVICE_NAME, version=settings.service_version)

    @api.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            service=settings.service_name,
            status=compute_status(),
            version=settings.service_version,
            environment=settings.environment,
        )
    
    @api.post("/internal/finops/recommend", response_model=RecommendResponse)
    def recommend_endpoint(request: RecommendRequest) -> RecommendResponse:
        response = recommend(request)
        persistence.save_recommendation(request, response)
        return response

    @api.post("/internal/finops/scale-options", response_model=FinOpsContext)
    def scale_options_endpoint(request: ScaleOptionsRequest) -> FinOpsContext:
        response = build_scale_options(request)
        persistence.save_scale_options(request, response)
        return response

    @api.get("/internal/finops/assumptions")
    def assumptions() -> dict:
        return {"cost_model": cost_model.ASSUMPTIONS}

    @api.post("/internal/finops/recommend-live", response_model=RecommendResponse)
    def recommend_live_endpoint(request: LiveRecommendRequest) -> RecommendResponse:
        response = recommend_live(request, settings.m1_telemetry_base_url)
        persistence.save_recommendation(request, response)
        return response

    return api


app = create_app()