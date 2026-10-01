from fastapi import APIRouter
from pydantic import BaseModel
from app.decision_engine.engine import decide_from_rca
from app.decision_engine.models import DecisionResult


router = APIRouter(
    prefix="/internal/decisions",
    tags=["decisions"]
)

class DecisionRequest(BaseModel):
    rca: dict
    finops: dict | None = None
    
@router.post("/evaluate", response_model=DecisionResult)
def evaluate_decision(request: DecisionRequest):
    return decide_from_rca(
        request.rca,
        request.finops
    )