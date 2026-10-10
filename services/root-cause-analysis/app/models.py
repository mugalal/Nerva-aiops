from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TelemetryMetrics(StrictModel):
    cpu: float = Field(ge=0)
    memory: float = Field(ge=0)
    request_rate: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    http_5xx_rate: float = Field(ge=0, le=1)
    replica_count: int = Field(ge=1)


class TelemetrySnapshot(StrictModel):
    timestamp: AwareDatetime
    service: str = Field(min_length=1)
    version: str = Field(min_length=1)
    metrics: TelemetryMetrics


class AnomalyFeatures(StrictModel):
    request_rate: float = Field(ge=0)
    latency_p95_ms: float = Field(ge=0)
    http_5xx_rate: float = Field(ge=0, le=1)
    cpu: float = Field(ge=0)


class AnomalyEvent(StrictModel):
    anomaly_id: str = Field(min_length=1)
    timestamp: AwareDatetime
    service: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)
    severity: str
    model: str
    features: AnomalyFeatures


class DeploymentEvent(StrictModel):
    event_id: str
    service: str
    old_version: str
    new_version: str
    commit_sha: str
    pipeline_id: str
    timestamp: AwareDatetime
    status: str


class Incident(StrictModel):
    incident_id: str = Field(min_length=1)
    started_at: AwareDatetime
    status: str
    severity: str
    affected_services: list[str] = Field(min_length=1)
    anomaly_ids: list[str]


class MetricStatistics(StrictModel):
    minimum: float = Field(ge=0)
    maximum: float = Field(ge=0)
    average: float = Field(ge=0)
    p95: float = Field(ge=0)


class BaselineThresholds(StrictModel):
    latency_p95_ms_max: float = Field(gt=0)
    http_5xx_rate_max: float = Field(ge=0, le=1)


class HealthyBaseline(StrictModel):
    service: str
    version: str
    measured_at: AwareDatetime
    window_start: AwareDatetime
    window_end: AwareDatetime
    sample_count: int = Field(ge=1)
    request_rate: MetricStatistics
    latency_p95_ms: MetricStatistics
    http_5xx_rate: MetricStatistics
    cpu: MetricStatistics
    memory: MetricStatistics
    replica_count: MetricStatistics
    thresholds: BaselineThresholds


class EvidencePreview(StrictModel):
    service: str
    provider_mode: Literal["real", "mock"]
    collected_at: AwareDatetime
    telemetry: TelemetrySnapshot
    deployment_event: DeploymentEvent | None
    selected_logs: list[str]
    provider_errors: list[str]
    baseline: HealthyBaseline | None
    kubernetes: dict[str, Any] | None
    resource_config: dict[str, Any] | None = None


class EvidenceBundle(StrictModel):
    incident: Incident
    telemetry: TelemetrySnapshot | None = None
    anomaly: AnomalyEvent | None = None
    deployment: DeploymentEvent | None = None
    baseline: HealthyBaseline | None = None
    provider_errors: list[str] = Field(default_factory=list)


class AnalyzeRequest(StrictModel):
    incident_id: str = Field(min_length=1, max_length=200,
                             pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")


class RCAResult(StrictModel):
    incident_id: str
    root_cause: str
    affected_component: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]
