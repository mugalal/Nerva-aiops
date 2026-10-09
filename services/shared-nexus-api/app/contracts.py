from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class IncidentView(FrozenModel):
    incident_id: str = Field(min_length=1)
    started_at: AwareDatetime
    status: str
    severity: str
    affected_services: list[str] = Field(min_length=1)
    anomaly_ids: list[str]


class AnomalyEvent(FrozenModel):
    anomaly_id: str = Field(min_length=1)
    timestamp: AwareDatetime
    service: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    severity: str
    model: str = Field(min_length=1)
    features: dict[str, float]


class RCAResult(FrozenModel):
    incident_id: str = Field(min_length=1)
    root_cause: str = Field(min_length=1)
    affected_component: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]


class ScaleOption(FrozenModel):
    replicas: int = Field(strict=True, ge=1)
    estimated_cost_delta: float = Field(ge=0)
    risk: Literal["LOW", "MEDIUM", "HIGH"]


class FinOpsContext(FrozenModel):
    service: str
    current_replicas: int = Field(strict=True, ge=1)
    current_cpu_request_m: int = Field(strict=True, gt=0)
    observed_cpu_pct: float = Field(ge=0, le=100)
    temporary_scale_options: list[ScaleOption]


class TelemetryMetrics(FrozenModel):
    cpu: float = Field(ge=0)
    memory: float = Field(ge=0)
    request_rate: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    http_5xx_rate: float = Field(ge=0, le=1)
    replica_count: int = Field(strict=True, ge=0)


class TelemetrySnapshot(FrozenModel):
    timestamp: AwareDatetime
    service: str = Field(min_length=1)
    version: str = Field(min_length=1)
    metrics: TelemetryMetrics


class DeploymentEvent(FrozenModel):
    event_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    old_version: str = Field(min_length=1)
    new_version: str = Field(min_length=1)
    commit_sha: str
    pipeline_id: str
    timestamp: AwareDatetime
    status: str


class RecoveryMetrics(FrozenModel):
    latency_p95_ms: float = Field(ge=0)
    http_5xx_rate: float = Field(ge=0, le=1)


class RecoveryResult(FrozenModel):
    incident_id: str
    recovered: StrictBool
    before: RecoveryMetrics
    after: RecoveryMetrics
    recovery_time_seconds: float = Field(ge=0)
    slo_restored: StrictBool


class DecisionProposal(FrozenModel):
    incident_id: str
    recommended_action: Literal["ROLLBACK", "SCALE", "ESCALATE"]
    target: str
    parameters: dict
    confidence: float = Field(ge=0, le=1)
    risk: Literal["LOW", "MEDIUM", "HIGH"]
    reason: str
    approval_required: bool


class ActionResult(FrozenModel):
    action_id: str
    incident_id: str
    action: Literal["ROLLBACK", "SCALE"]
    status: Literal["SUCCESS", "FAILED"]
    started_at: AwareDatetime
    completed_at: AwareDatetime
