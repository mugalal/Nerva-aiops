from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


Scenario = Literal["bad_deployment", "traffic_spike"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TelemetryMetrics(StrictModel):
    cpu: float = Field(ge=0)
    memory: float = Field(ge=0)
    request_rate: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    http_5xx_rate: float = Field(ge=0, le=1)
    replica_count: int = Field(ge=0)


class TelemetrySnapshot(StrictModel):
    timestamp: datetime
    service: str = Field(min_length=1)
    version: str = Field(min_length=1)
    metrics: TelemetryMetrics


class TelemetryWindowPoint(StrictModel):
    timestamp: datetime
    metrics: TelemetryMetrics


class TelemetryWindow(StrictModel):
    service: str
    version: str
    start: datetime
    end: datetime
    step_seconds: int
    points: list[TelemetryWindowPoint]


class TimeWindowRequest(StrictModel):
    service: str = Field(min_length=1)
    start: AwareDatetime
    end: AwareDatetime
    step_seconds: int = Field(default=15, ge=5, le=300)

    @model_validator(mode="after")
    def validate_window(self):
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class MetricStatistics(StrictModel):
    minimum: float
    maximum: float
    average: float
    p95: float


class BaselineThresholds(StrictModel):
    latency_p95_ms_max: float = Field(ge=0)
    http_5xx_rate_max: float = Field(ge=0, le=1)


class HealthyBaseline(StrictModel):
    service: str
    version: str
    measured_at: datetime
    window_start: datetime
    window_end: datetime
    sample_count: int = Field(gt=0)
    request_rate: MetricStatistics
    latency_p95_ms: MetricStatistics
    http_5xx_rate: MetricStatistics
    cpu: MetricStatistics
    memory: MetricStatistics
    replica_count: MetricStatistics
    thresholds: BaselineThresholds


class DeploymentEvent(StrictModel):
    event_id: str
    service: str
    old_version: str
    new_version: str
    commit_sha: str
    pipeline_id: str
    timestamp: datetime
    status: str


class KubernetesContext(StrictModel):
    service: str
    namespace: str
    desired_replicas: int
    ready_replicas: int
    pod_restarts: int
    version: str
    service_health: Literal["ok", "degraded", "unavailable"]
    events: list[str]


class CaptureEvidenceRequest(StrictModel):
    incident_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    scenario: Scenario


class IncidentEvidence(StrictModel):
    incident_id: str
    service: str
    scenario: str
    captured_at: datetime
    before: TelemetrySnapshot
    selected_logs: list[str]
    kubernetes: KubernetesContext | None
    deployment_event: DeploymentEvent | None
    provider_errors: list[str]
    baseline: HealthyBaseline | None = None


class RecoveryValidationRequest(StrictModel):
    incident_id: str = Field(min_length=1)
    service: str = Field(min_length=1)
    action_completed_at: AwareDatetime
    scenario: Scenario


class RecoveryMetrics(StrictModel):
    latency_p95_ms: float = Field(ge=0)
    http_5xx_rate: float = Field(ge=0, le=1)


class RecoveryResult(StrictModel):
    incident_id: str
    recovered: bool
    before: RecoveryMetrics
    after: RecoveryMetrics
    recovery_time_seconds: float = Field(ge=0)
    slo_restored: bool


class RecoveryEvidenceRecord(StrictModel):
    incident_id: str
    service: str
    scenario: str
    action_completed_at: datetime
    validated_at: datetime
    before: TelemetrySnapshot
    after: TelemetrySnapshot
    result: RecoveryResult
    measurement_window: TelemetryWindow | None = None


class DependencyHealth(StrictModel):
    status: Literal["ok", "degraded", "unavailable"]
    detail: str


class HealthResponse(StrictModel):
    service: str
    status: Literal["ok", "degraded", "unavailable"]
    version: str
    environment: str
    provider_mode: Literal["real", "mock"]
    dependencies: dict[str, DependencyHealth]


class LogEntriesResponse(StrictModel):
    incident_id: str
    service: str
    entries: list[str]
