from enum import Enum
from pydantic import BaseModel


class DecisionAction(str, Enum):
    ROLLBACK = "ROLLBACK"
    SCALE = "SCALE"
    ESCALATE = "ESCALATE"


class DecisionResult(BaseModel):
    action: DecisionAction
    reason: str
    approval_required: bool
    confidence: float
    scale_option: dict | None = None