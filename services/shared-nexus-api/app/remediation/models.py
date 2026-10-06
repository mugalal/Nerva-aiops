from pydantic import BaseModel
from app.decision_engine.models import DecisionAction


class RemediationResult(BaseModel):
    action: DecisionAction
    success: bool
    message: str
    execution_id: str | None = None