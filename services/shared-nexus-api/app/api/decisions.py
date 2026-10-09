from fastapi import APIRouter
from pydantic import BaseModel
from app.decision_engine.engine import decide_from_rca
from app.decision_engine.models import DecisionResult
from app.orchestration.orchestrator import build_decision
from app.providers.errors import as_http_error

router = APIRouter(prefix="/internal/decisions", tags=["decisions"])

class DecisionRequest(BaseModel):
    rca: dict
    finops: dict | None = None

@router.post("/evaluate", response_model=DecisionResult)
def evaluate_decision(request: DecisionRequest):
    return decide_from_rca(request.rca, request.finops)

@router.post("/build/{incident_id}", response_model=DecisionResult)
def build_incident_decision(incident_id: str):
    try:
        return build_decision(incident_id)
    except ValueError as exc:
        raise as_http_error(exc) from exc
