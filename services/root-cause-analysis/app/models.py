from datetime import datetime

from pydantic import BaseModel, Field


# ============================================================
# M1 - Telemetry
# ============================================================

class TelemetryMetrics(BaseModel):
    cpu: float
    memory: float
    request_rate: float
    latency_p95_ms: float
    http_5xx_rate: float
    replica_count: int


class TelemetrySnapshot(BaseModel):
    timestamp: datetime
    service: str
    version: str
    metrics: TelemetryMetrics


# ============================================================
# M2 - Anomaly Detection
# ============================================================

class AnomalyFeatures(BaseModel):
    request_rate: float
    latency_p95_ms: float
    http_5xx_rate: float
    cpu: float


class AnomalyEvent(BaseModel):
    anomaly_id: str
    timestamp: datetime
    service: str
    score: float
    severity: str
    model: str
    features: AnomalyFeatures


# ============================================================
# Deployment Evidence
# ============================================================

class DeploymentEvent(BaseModel):
    event_id: str
    service: str
    old_version: str
    new_version: str
    commit_sha: str
    pipeline_id: str
    timestamp: datetime
    status: str


# ============================================================
# Incident
# ============================================================

class Incident(BaseModel):
    incident_id: str
    started_at: datetime
    status: str
    severity: str
    affected_services: list[str]
    anomaly_ids: list[str]


# ============================================================
# Internal M3 Evidence Bundle
# ============================================================

class EvidenceBundle(BaseModel):
    """
    Internal evidence collected by M3.

    This is richer than the public RCAResult contract.
    """

    incident: Incident

    telemetry: TelemetrySnapshot | None = None

    anomaly: AnomalyEvent | None = None

    deployment: DeploymentEvent | None = None

    provider_errors: list[str] = Field(default_factory=list)


# ============================================================
# API Request
# ============================================================

class AnalyzeRequest(BaseModel):
    incident_id: str


# ============================================================
# Public M3 RCA Contract
# ============================================================

class RCAResult(BaseModel):
    """
    Public RCA contract consumed by downstream NEXUS modules.

    IMPORTANT:
    Keep this aligned with contracts/rca_result.json.
    """

    incident_id: str
    root_cause: str
    affected_component: str
    confidence: float
    evidence: list[str]