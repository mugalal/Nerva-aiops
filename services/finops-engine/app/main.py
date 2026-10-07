import sys
from pathlib import Path
from fastapi import FastAPI
from . import cost_model
from shared.config.settings import get_runtime_settings  
from .models import FinOpsContext, HealthResponse, RecommendRequest, RecommendResponse, ScaleOptionsRequest
from .rightsizing import recommend
from .scale_options import build_scale_options

# Make the repo root importable so we can use shared/ (same trick as M1).
# main.py -> app -> finops-engine -> services -> repo root = parents[3]
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SERVICE_NAME = "finops-engine"


def compute_status() -> str:
    # M6 has no dependencies yet. Later: "degraded" if the DB is configured
    # but unreachable, "unavailable" if the cost model cannot load.
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
        return recommend(request)

    @api.post("/internal/finops/scale-options", response_model=FinOpsContext)
    def scale_options_endpoint(request: ScaleOptionsRequest) -> FinOpsContext:
        return build_scale_options(request)

    @api.get("/internal/finops/assumptions")
    def assumptions() -> dict:
        return {"cost_model": cost_model.ASSUMPTIONS}

    return api


app = create_app()