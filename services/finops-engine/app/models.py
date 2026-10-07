from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    status: Literal["ok", "degraded", "unavailable"]
    version: str
    environment: str

class ScaleOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replicas: int = Field(ge=1)
    estimated_cost_delta: float
    risk: str


class FinOpsContext(BaseModel):
    """Response of /scale-options. Same fields as contracts/finops_context.json."""
    model_config = ConfigDict(extra="forbid")
    service: str
    current_replicas: int = Field(ge=1)
    current_cpu_request_m: int = Field(gt=0)
    observed_cpu_pct: float = Field(ge=0, le=100)
    temporary_scale_options: list[ScaleOption]