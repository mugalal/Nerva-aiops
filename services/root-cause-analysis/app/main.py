from pathlib import Path

from fastapi import FastAPI, HTTPException

from .models import AnalyzeRequest, RCAResult
from .service import RCAService
from .providers.base import ProviderError


# ---------------------------------------------------------
# Application setup
# ---------------------------------------------------------

app = FastAPI(
    title="NEXUS Root Cause Analysis Service",
    version="0.1.0",
    description="M3 Root Cause Analysis service for the NEXUS AIOps platform.",
)


# ---------------------------------------------------------
# Paths
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MOCKS_DIR = PROJECT_ROOT / "mocks"


# ---------------------------------------------------------
# RCA service
# ---------------------------------------------------------

rca_service = RCAService(
    mocks_dir=MOCKS_DIR
)


# ---------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return await rca_service.health()


# ---------------------------------------------------------
# RCA endpoint
# ---------------------------------------------------------

@app.post(
    "/internal/rca/analyze",
    response_model=RCAResult,
)
async def analyze_root_cause(
    request: AnalyzeRequest,
) -> RCAResult:

    try:
        return await rca_service.analyze(request)

    except ProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail={
            "category": exc.category, "provider": exc.provider,
            "message": exc.message, "retryable": exc.retryable,
        }) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(status_code=500, detail="RCA analysis failed") from exc
