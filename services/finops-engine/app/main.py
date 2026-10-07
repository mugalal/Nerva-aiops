import sys
from pathlib import Path

from fastapi import FastAPI

# Make the repo root importable so we can use shared/ (same trick as M1).
# main.py -> app -> finops-engine -> services -> repo root = parents[3]
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.config.settings import get_runtime_settings  # noqa: E402

from .models import HealthResponse  # noqa: E402

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

    return api


app = create_app()