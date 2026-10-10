from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from .resource_config import finite_number

class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str
    status: Literal["ok", "degraded", "unavailable"]
    version: str
    environment: str

class ScaleOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replicas: int = Field(ge=1)
    estimated_cost_delta: float = Field(ge=0, allow_inf_nan=False)
    risk: Literal["LOW", "MEDIUM", "HIGH"]


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
    start: AwareDatetime
    end: AwareDatetime
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

class LiveRecommendRequest(BaseModel):
    """Body of POST /internal/finops/recommend-live (M6 fetches the data from M1)."""
    model_config = ConfigDict(extra="forbid")
    service: str = Field(min_length=1)
    current: ResourceSpec
    cpu_limit_m: float = Field(gt=0, allow_inf_nan=False)
    memory_limit_mb: float = Field(gt=0, allow_inf_nan=False)
    window_start: AwareDatetime
    window_end: AwareDatetime
    step_seconds: int = Field(default=15, ge=5, le=300)
    traffic_pattern: str | None = None

    @field_validator("cpu_limit_m", "memory_limit_mb", mode="before")
    @classmethod
    def valid_limits(cls, value):
        return finite_number(value, "resource limit", positive=True)

    @model_validator(mode="after")
    def end_after_start(self):
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        if self.current.cpu_request_m > self.cpu_limit_m or self.current.memory_request_mb > self.memory_limit_mb:
            raise ValueError("Resource requests must not exceed limits")
        return self
