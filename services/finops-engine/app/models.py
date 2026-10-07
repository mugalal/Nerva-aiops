from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    #Response of /scale-options. Same fields as contracts/finops_context.json.
    model_config = ConfigDict(extra="forbid")
    service: str
    current_replicas: int = Field(ge=1)
    current_cpu_request_m: int = Field(gt=0)
    observed_cpu_pct: float = Field(ge=0, le=100)
    temporary_scale_options: list[ScaleOption]

class ResourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replicas: int = Field(ge=1)
    cpu_request_m: int = Field(gt=0)
    memory_request_mb: int = Field(gt=0)


class ObservedUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    avg_cpu_pct: float = Field(ge=0, le=100)
    avg_memory_pct: float = Field(ge=0, le=100)


class FinOpsRecommendation(BaseModel):
    #Response of /recommend. Same fields as contracts/finops_recommendation.json.
    model_config = ConfigDict(extra="forbid")
    service: str
    current: ResourceSpec
    observed: ObservedUsage
    recommended: ResourceSpec
    estimated_monthly_saving_pct: float = Field(ge=0, le=100)
    reliability_risk: str

class MeasurementWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: datetime
    end: datetime
    sample_count: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def end_after_start(self):
        if self.end <= self.start:
            raise ValueError("window.end must be after window.start")
        return self


class WindowUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    avg_cpu_pct: float = Field(ge=0, le=100)
    peak_cpu_pct: float = Field(ge=0, le=100)
    avg_memory_pct: float = Field(ge=0, le=100)
    peak_memory_pct: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def peak_not_below_average(self):
        if self.peak_cpu_pct < self.avg_cpu_pct or self.peak_memory_pct < self.avg_memory_pct:
            raise ValueError("peak must be >= average")
        return self


class RecommendRequest(BaseModel):
    """Body of POST /internal/finops/recommend."""
    model_config = ConfigDict(extra="forbid")
    service: str = Field(min_length=1)
    current: ResourceSpec
    observed: WindowUsage
    window: MeasurementWindow
    traffic_pattern: str | None = None


class ScaleOptionsRequest(BaseModel):
    #Body of POST /internal/finops/scale-options.
    model_config = ConfigDict(extra="forbid")
    service: str = Field(min_length=1)
    incident_id: str | None = None
    current_replicas: int = Field(ge=1)
    cpu_request_m: int = Field(gt=0)
    memory_request_mb: int = Field(gt=0)
    observed_cpu_pct: float = Field(ge=0, le=100)
    scale_duration_minutes: int = Field(gt=0)
    candidate_replicas: list[int] | None = None

class RecommendResponse(BaseModel):
    #Body returned by /recommend. The frozen contract sits inside `recommendation`.
    model_config = ConfigDict(extra="forbid")
    status: Literal["RECOMMENDED", "NO_RECOMMENDATION", "INSUFFICIENT_EVIDENCE"]
    reason: str
    assumptions: list[str]
    recommendation: FinOpsRecommendation